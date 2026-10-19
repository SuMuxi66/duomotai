export type AudioPlaybackKind = "wav-queue" | "pcm-stream";

type AudioPlaybackCallbacks = {
  onPlaybackComplete?: (kind: AudioPlaybackKind) => void;
};

type QueuedAudio = {
  url: string;
};

type PcmChunk = {
  data: Float32Array<ArrayBuffer>;
  sampleRate: number;
  seq: number;
};

export type PcmQueueStats = {
  queueDepth: number;
  capacity: number;
  maxCapacity: number;
  highWaterMark: number;
  scheduledCount: number;
  playedCount: number;
  droppedCount: number;
  underrunCount: number;
  totalEnqueued: number;
  playing: boolean;
};

function pcm16ToFloat32(bytes: Uint8Array): Float32Array<ArrayBuffer> {
  const sampleCount = Math.floor(bytes.length / 2);
  const float32 = new Float32Array(sampleCount);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < sampleCount; i++) {
    float32[i] = view.getInt16(i * 2, true) / 32768;
  }
  return float32;
}

const WAV_QUEUE_MAX_SIZE = 10;

// ===== PCM 队列可调参数（溢出保护 / 平滑缓冲 / 监控）=====
// 说明：流式 TTS 生成速度通常远超 1x 实时播放，长回复（如 40s 语音 ≈ 270 块）
// 会在播放期间大量堆积。容量不足时丢最旧块会导致内容跳跃漏词（听感卡顿），
// 因此上限按"最长可预见回复全量缓冲"设定（1500 块 ≈ 240s 语音 ≈ 23MB，内存可接受）。
const PCM_QUEUE_BASE_CAPACITY = 1000; // 基础容量（初始即大水槽，TTS 突发/后台告警不卡顿）
const PCM_QUEUE_MAX_CAPACITY = 1500; // 动态扩容硬上限（覆盖超长回复，杜绝丢块）
const PCM_QUEUE_EXPAND_STEP = 100; // 压力下每次扩容步长（快速吸收突发，减少丢块窗口）
const PCM_QUEUE_SHRINK_AFTER_MS = 4000; // 压力解除后多久回落到基础容量
const QUEUE_WARN_RATIO = 0.7; // 深度 ≥70% 容量 → 预警
const QUEUE_CRITICAL_RATIO = 0.9; // 深度 ≥90% 容量 → 严重告警
const SCHEDULE_AHEAD_SECONDS = 4.0; // 音频时间线最多预排 4 秒，超出留在队列待调度。
// 小米按句合成的实测吞吐仅 ~1.15x 实时且到达一阵一阵（单块 0.96s 音频、间隔 2-3s），
// 预排 2s 时缓冲频繁被打穿（underrun=可感知卡顿）；提到 4s 可吸收全部观测间隙。
// 生产>消耗，预排垫只会累积不会透支，代价仅是音频略滞后于文本显示（2026-09-18 调整，原 2.0）
const FIRST_CHUNK_LEAD_SECONDS = 1.0; // 首块起始/underrun重同步的提前量：既避免起始爆音与竞态，
// 也起「缓冲垫」作用——TTS 是按句合成、音频一阵一阵到（实测个别批次仅 0.41x 实时），
// 留垫底可吸收句间空档，避免每块都触发 underrun 重排（2026-09-18 调整，原 0.6）
const UNDERRUN_EPSILON_SECONDS = 0.005; // 判定时间线出现"空洞"的容差
const SEAM_FADE_SECONDS = 0.004; // 每块首尾 4ms 淡入淡出，消除拼接爆音
const STATS_LOG_INTERVAL_MS = 5000; // 周期性队列状态日志间隔
// 块级日志采样：每 N 块输出 1 条，避免长回复产生数百条 console.log 拖慢主线程
const CHUNK_LOG_SAMPLE = 20;

export function createAudioPlaybackController(
  initialCallbacks: AudioPlaybackCallbacks = {},
  audioOutputDeviceId?: string
) {
  let callbacks = initialCallbacks;
  let wavQueue: QueuedAudio[] = [];
  let isPlayingQueue = false;
  let currentAudio: HTMLAudioElement | null = null;
  let currentAudioUrl: string | null = null;

  let pcmAudioContext: AudioContext | null = null;
  let pcmStreamEnded = false;
  let pcmDestroyed = false;
  // 解码后的 PCM 块队列（仅含尚未提交到音频时间线的块）
  let pcmQueue: PcmChunk[] = [];
  // 已提交到音频时间线的源节点集合（含正在播放与预排的）
  const pcmScheduledSources = new Set<AudioBufferSourceNode>();
  let pcmNextStartTime = 0; // 时间线上下一个块的起始时刻
  let pcmTimelineInitialized = false;
  let pcmSchedulerRunning = false;
  let pcmPlayedCount = 0;
  let pcmEnqueueCount = 0;
  let pcmDroppedCount = 0;
  let pcmUnderrunCount = 0;
  let pcmHighWaterMark = 0;
  let pcmCapacity = PCM_QUEUE_BASE_CAPACITY;
  let pcmLastPressureAt = 0;
  let pcmWarnActive = false;
  let pcmCriticalActive = false;
  let pcmStatsLogTimer: ReturnType<typeof setInterval> | null = null;
  let pcmShrinkTimer: ReturnType<typeof setInterval> | null = null;
  // 当前 PCM 流的 request_id：识别"新流到来但旧流未正常收尾"的插播场景
  let pcmActiveRequestId: string | undefined;
  // ===== 插播挂起（insert-then-resume）=====
  // 新流（倒计时提醒/油温播报等）到来而旧流仍在播放时，不再清场打断：
  // 旧流已预排的块（≤2 秒）继续播完，未调度块挂起缓冲；新流从时间线尾部插入；
  // 新流收尾后按后进先出把挂起流接回队列续播（嵌套插播逐层恢复）。
  type SuspendedPcmStream = {
    requestId: string;
    queue: PcmChunk[];
    ended: boolean; // 挂起期间已收到该流的 tts_pcm_end
    suspendedAt: number;
  };
  let pcmSuspendedStreams: SuspendedPcmStream[] = [];
  // 已被打断丢弃的流 id（barge-in/stopAll）：其迟到残块直接丢弃，防止"复活续播"
  const pcmStoppedRequestIds = new Set<string>();
  // WAV 插播（enqueueWavBase64）：用虚拟流 id 占住活跃流位，使被挂起 PCM 流的
  // 迟到块与收尾仍走"挂起缓冲"路径（playPcmChunk/endPcmStream 的 suspended 查找），
  // WAV 队列播完后再接回（见 playNextInQueue 收尾分支）
  const WAV_STREAM_ID = "__wav_interject__";
  let wavInterjectActive = false; // WAV 插播进行中（已挂起 PCM，等 WAV 队列播完接回；防 WAV 连发重复挂起）
  // AudioContext suspended 时的节流重试恢复（自动播放策略：无手势 resume 被拒属预期，
  // 任意一次点击/按键由 unlockAudioContext 立即解锁，这里兜底"手势之后再次被挂起"的场景）
  let pcmLastCtxResumeAttemptAt = 0;

  console.log('[TTS][播放] 扬声器设备:', audioOutputDeviceId || '默认');

  // 全局手势解锁：浏览器自动播放策略会把无手势上下文创建的 AudioContext 挂起
  // （resume 无手势时被拒，导致 TTS 永远静音）。用户任意点击/按键时立即恢复。
  const unlockAudioContext = () => {
    if (pcmAudioContext && pcmAudioContext.state === "suspended") {
      console.log('[TTS][播放] 检测到音频上下文被挂起，用户手势解锁恢复');
      try {
        // 静音缓冲 + resume 组合：部分浏览器要求"手势内实际启动过音频"才彻底解锁；
        // 解锁瞬间，先前积压的警报音频会自动补播
        const silentBuf = pcmAudioContext.createBuffer(1, 1, 22050);
        const silentSrc = pcmAudioContext.createBufferSource();
        silentSrc.buffer = silentBuf;
        silentSrc.connect(pcmAudioContext.destination);
        silentSrc.start(0);
      } catch (err) {
        console.warn('[TTS][播放] 静音缓冲启动失败（继续尝试 resume）:', err);
      }
      pcmAudioContext.resume().catch((err: any) => {
        console.warn('[TTS][播放] 手势解锁恢复失败:', err);
      });
    }
  };
  if (typeof document !== "undefined") {
    document.addEventListener("pointerdown", unlockAudioContext, true);
    document.addEventListener("keydown", unlockAudioContext, true);
  }

  // 页面加载即预热音频上下文：避免第一次警报 TTS 到达时才创建
  // （无手势 → 挂起 → 警报静音）。创建后处于 suspended 属正常，
  // 首次用户手势由上方 unlockAudioContext 恢复，积压音频自动补播。
  try {
    pcmAudioContext = new AudioContext();
    console.log('[TTS][播放] 音频上下文已预创建（等待首次用户手势激活）:', pcmAudioContext.state);
  } catch (err) {
    console.warn('[TTS][播放] 预创建 AudioContext 失败:', err);
  }

  function setCallbacks(nextCallbacks: AudioPlaybackCallbacks) {
    callbacks = nextCallbacks;
  }

  function setOutputDevice(deviceId: string) {
    console.log('[TTS][播放] 切换扬声器设备:', audioOutputDeviceId || '默认', '->', deviceId || '默认');
    audioOutputDeviceId = deviceId;
    // 如果 AudioContext 已存在，尝试立即切换
    // 跳过虚拟眼镜音频 ID，眼镜扬声器由后端通过 /ws/glasses-audio 推送，不走浏览器本地播放
    if (pcmAudioContext && typeof (pcmAudioContext as any).setSinkId === 'function') {
      if (deviceId && deviceId !== '__GLASSES_AUDIO__') {
        (pcmAudioContext as any).setSinkId(deviceId).catch((err: any) => {
          console.warn('[TTS][播放] AudioContext 切换扬声器失败:', err);
        });
      }
    }
  }

  // ===== 队列状态监控与预警 =====

  function getPcmQueueStats(): PcmQueueStats {
    return {
      queueDepth: pcmQueue.length,
      capacity: pcmCapacity,
      maxCapacity: PCM_QUEUE_MAX_CAPACITY,
      highWaterMark: pcmHighWaterMark,
      scheduledCount: pcmScheduledSources.size,
      playedCount: pcmPlayedCount,
      droppedCount: pcmDroppedCount,
      underrunCount: pcmUnderrunCount,
      totalEnqueued: pcmEnqueueCount,
      playing: pcmScheduledSources.size > 0 || pcmQueue.length > 0 || pcmSchedulerRunning || pcmSuspendedStreams.length > 0,
    };
  }

  function reportQueueDepth() {
    const depth = pcmQueue.length;
    if (depth > pcmHighWaterMark) {
      pcmHighWaterMark = depth;
    }
    if (depth >= pcmCapacity) {
      pcmLastPressureAt = Date.now();
    }
    const warnLine = Math.max(1, Math.floor(pcmCapacity * QUEUE_WARN_RATIO));
    const criticalLine = Math.max(1, Math.floor(pcmCapacity * QUEUE_CRITICAL_RATIO));
    if (depth >= criticalLine && !pcmCriticalActive) {
      pcmCriticalActive = true;
      pcmWarnActive = true;
      console.error(
        `[TTS][播放][预警] PCM 队列深度严重过高: ${depth}/${pcmCapacity}（≥${Math.round(QUEUE_CRITICAL_RATIO * 100)}%），已触发扩容/降级保护`,
      );
    } else if (depth >= warnLine && !pcmWarnActive) {
      pcmWarnActive = true;
      console.warn(
        `[TTS][播放][预警] PCM 队列深度偏高: ${depth}/${pcmCapacity}（≥${Math.round(QUEUE_WARN_RATIO * 100)}%）`,
      );
    } else if (depth < warnLine * 0.6 && (pcmWarnActive || pcmCriticalActive)) {
      // 迟滞回退，避免告警抖动
      console.log(`[TTS][播放][预警解除] PCM 队列回落至安全水位: ${depth}/${pcmCapacity}`);
      pcmWarnActive = false;
      pcmCriticalActive = false;
    }
  }

  function maybeExpandCapacity() {
    if (pcmQueue.length >= pcmCapacity && pcmCapacity < PCM_QUEUE_MAX_CAPACITY) {
      const prev = pcmCapacity;
      pcmCapacity = Math.min(PCM_QUEUE_MAX_CAPACITY, pcmCapacity + PCM_QUEUE_EXPAND_STEP);
      console.warn(`[TTS][播放] 队列压力检测: 容量动态扩容 ${prev} -> ${pcmCapacity}`);
    }
  }

  function scheduleCapacityShrinkCheck() {
    // 低水位持续一段时间后回落容量，防止一次突发把容量永久顶满
    // 幂等：destroy 清理后控制器复活（warmUp）时可能重复注册
    if (pcmShrinkTimer) return;
    pcmShrinkTimer = setInterval(() => {
      if (
        pcmCapacity > PCM_QUEUE_BASE_CAPACITY &&
        pcmQueue.length < pcmCapacity * 0.5 &&
        Date.now() - pcmLastPressureAt > PCM_QUEUE_SHRINK_AFTER_MS
      ) {
        const prev = pcmCapacity;
        pcmCapacity = Math.max(PCM_QUEUE_BASE_CAPACITY, pcmCapacity - PCM_QUEUE_EXPAND_STEP);
        console.log(`[TTS][播放] 压力解除: 队列容量回落 ${prev} -> ${pcmCapacity}`);
      }
    }, PCM_QUEUE_SHRINK_AFTER_MS);
  }
  scheduleCapacityShrinkCheck();

  function startStatsLogging() {
    if (pcmStatsLogTimer) return;
    pcmStatsLogTimer = setInterval(() => {
      const s = getPcmQueueStats();
      if (!s.playing) return;
      // 队列统计仍持续更新（高水位/压力水位用于动态扩容），但不再打印逐条监控日志
    }, STATS_LOG_INTERVAL_MS);
  }

  function stopStatsLogging() {
    if (pcmStatsLogTimer) {
      clearInterval(pcmStatsLogTimer);
      pcmStatsLogTimer = null;
    }
  }

  // ===== 平滑缓冲：接缝淡入淡出，消除块间拼接爆音 =====

  function applySeamFades(data: Float32Array, sampleRate: number) {
    const fadeLen = Math.min(Math.floor(SEAM_FADE_SECONDS * sampleRate), Math.floor(data.length / 2));
    for (let i = 0; i < fadeLen; i++) {
      const gain = i / fadeLen;
      data[i] *= gain;
      data[data.length - 1 - i] *= gain;
    }
  }

  function decodePcmBase64(pcmB64: string): Float32Array<ArrayBuffer> {
    const binary = atob(pcmB64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return pcm16ToFloat32(bytes);
  }

  async function ensureAudioContext(sampleRate: number): Promise<AudioContext> {
    if (!pcmAudioContext) {
      pcmAudioContext = new AudioContext({ sampleRate });
      if (
        audioOutputDeviceId &&
        audioOutputDeviceId !== '__GLASSES_AUDIO__' &&
        typeof (pcmAudioContext as any).setSinkId === 'function'
      ) {
        (pcmAudioContext as any).setSinkId(audioOutputDeviceId).catch((err: any) => {
          console.warn('[TTS][播放] AudioContext 设置扬声器失败:', err);
        });
      }
    }
    if (pcmAudioContext.state === "suspended") {
      // 浏览器自动播放策略下，无用户手势时 resume() 可能永久 pending，
      // 这里加超时防止调度泵死锁（块仍会入队，等 context 恢复后播放）
      await Promise.race([
        pcmAudioContext.resume(),
        new Promise<void>((resolve) => setTimeout(resolve, 1000)),
      ]);
    }
    return pcmAudioContext;
  }

  /**
   * 预热 AudioContext：在用户手势上下文（如点击发送）中提前创建并恢复，
   * 规避自动播放策略导致后续 WS 回调创建的 AudioContext 处于 suspended 而无声。
   *
   * 若控制器此前被 destroy()（例如 React StrictMode 的开发期 cleanup），
   * 这里同时"复活"控制器，避免 pcmDestroyed 永久置位导致 PCM 只入队不播放。
   */
  async function warmUp(): Promise<void> {
    try {
      if (pcmDestroyed) {
        pcmDestroyed = false;
        stopAll();
        scheduleCapacityShrinkCheck(); // destroy 已清掉回落监测，复活时补注册
        console.log('[TTS][播放] AudioContext 预热：控制器已复活（此前被 destroy 标记）');
      }
      await ensureAudioContext(24000);
      console.log('[TTS][播放] AudioContext 预热完成:', pcmAudioContext?.state);
    } catch (err) {
      console.warn('[TTS][播放] AudioContext 预热失败:', err);
    }
  }

  // 音频上下文是否处于可出声状态（running = 已被用户手势解锁）
  function isPcmContextRunning(): boolean {
    return !!pcmAudioContext && pcmAudioContext.state === "running";
  }

  function handleSourceEnded(source: AudioBufferSourceNode, playedSeq: number) {
    pcmScheduledSources.delete(source);
    pcmPlayedCount += 1;
    if (playedSeq % CHUNK_LOG_SAMPLE === 0) {
      console.log(`[TTS][播放] PCM 播放进度: 第 ${playedSeq} 块完成, 已播=${pcmPlayedCount}, 队列=${pcmQueue.length}`);
    }
    // 预排窗口随播放推进而空出，重新触发调度泵把队列剩余块补进时间线
    void pumpSchedule().then(() => tryFinishPcmPlayback());
  }

  /**
   * 调度泵：把队列中已解码的 PCM 块按精确时间线预排到 AudioContext。
   * 相邻块的 start 时刻无缝衔接（由音频硬件时钟保证），彻底消除
   * "播完一块→主线程解码下一块"造成的块间空洞卡顿。
   */
  async function pumpSchedule() {
    if (pcmSchedulerRunning) return;
    pcmSchedulerRunning = true;
    try {
      while (!pcmDestroyed && pcmQueue.length > 0) {
        const ctx = await ensureAudioContext(pcmQueue[0].sampleRate);
        const now = ctx.currentTime;

        // 上下文被挂起时低频重试 resume：无手势会被浏览器拒绝（静默忽略），
        // 一旦某次尝试或用户手势解锁成功，积压块立即按时间线补播
        if (ctx.state === "suspended" && Date.now() - pcmLastCtxResumeAttemptAt > 500) {
          pcmLastCtxResumeAttemptAt = Date.now();
          ctx.resume().catch(() => {});
        }

        if (!pcmTimelineInitialized || pcmNextStartTime < now - UNDERRUN_EPSILON_SECONDS) {
          if (pcmTimelineInitialized) {
            pcmUnderrunCount += 1;
            console.warn(
              `[TTS][播放] 检测到播放时间线空洞(underrun #${pcmUnderrunCount})，重同步时间线, 队列=${pcmQueue.length}`,
            );
          }
          pcmNextStartTime = now + FIRST_CHUNK_LEAD_SECONDS;
          pcmTimelineInitialized = true;
        }

        // 时间线已预排足够远：剩余块留在队列，等窗口空出再调度
        if (pcmNextStartTime - now > SCHEDULE_AHEAD_SECONDS) break;

        const chunk = pcmQueue.shift()!;
        if (chunk.data.length === 0) continue;

        applySeamFades(chunk.data, chunk.sampleRate);
        // AudioBufferSourceNode 会自动按 context 采样率重采样，混流采样率也安全
        const buffer = ctx.createBuffer(1, chunk.data.length, chunk.sampleRate);
        buffer.copyToChannel(chunk.data, 0);

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);

        pcmScheduledSources.add(source);
        pcmNextStartTime += chunk.data.length / chunk.sampleRate;
        const playedSeq = chunk.seq;
        // 不再输出逐块调度进度日志，仅保留播放完成时的汇总（见 tryFinishPcmPlayback）
        source.onended = () => handleSourceEnded(source, playedSeq);
        try {
          source.start(pcmNextStartTime - chunk.data.length / chunk.sampleRate);
        } catch (err) {
          pcmScheduledSources.delete(source);
          console.warn(`[TTS][播放] PCM 块 ${playedSeq} 调度失败:`, err);
        }
      }
    } finally {
      pcmSchedulerRunning = false;
    }
  }

  function tryFinishPcmPlayback() {
    if (!pcmStreamEnded) return;
    if (pcmQueue.length > 0 || pcmScheduledSources.size > 0 || pcmSchedulerRunning) return;
    pcmStreamEnded = false;
    stopStatsLogging();
    // 还有挂起流（嵌套插播 ≥3 条流的场景）：先接回下一条续播，完成回调推迟到
    // 全部播完再发——中间流若提前发完成，useChatEngine 会把 isAiSpeaking 复位，
    // 而接回的音频还在播，回声门控开窗会被 TTS 自己的声音触发打断
    if (pcmSuspendedStreams.length > 0) {
      console.log(
        `[TTS][播放] 当前流播完，接回挂起流续播（剩余 ${pcmSuspendedStreams.length} 条），完成回调推迟`,
      );
      resumeNextSuspendedStream();
      // 接回的缓冲块必须立即驱动调度泵（同 endPcmStream 收尾路径），
      // 否则插播音频已播完且挂起流已收尾时缓冲块会永久滞留
      void pumpSchedule().then(() => tryFinishPcmPlayback());
      tryFinishPcmPlayback();
      return;
    }
    console.log(`[TTS][播放] PCM 流播放完成, 共播放 ${pcmPlayedCount} 块, 丢弃 ${pcmDroppedCount} 块, underrun ${pcmUnderrunCount} 次`);
    callbacks.onPlaybackComplete?.("pcm-stream");
  }

  function enqueueWavBase64(wavB64: string) {
    if (!wavB64) {
      return;
    }

    // WAV 到达视为插播：PCM 正文播放中 → 挂起当前流（未调度块入挂起缓冲，
    // 已预排 ≤2 秒尾音播完），WAV 播完自动接回——而不是旧行为的清场+墓碑
    // （会把主回答拦腰掐断，且其 request_id 进墓碑后该流后续块全部被静默丢弃）。
    // PCM 已收尾（仅剩尾音）或无 PCM 在播 → 保持旧行为直接清场接管。
    if (pcmActiveRequestId !== undefined && !pcmStreamEnded) {
      if (!wavInterjectActive) {
        suspendActivePcmStream(WAV_STREAM_ID);
        pcmActiveRequestId = WAV_STREAM_ID;
        pcmStreamEnded = false;
        wavInterjectActive = true; // WAV 连发时只挂起一次，后续 WAV 直接排队
      }
    } else {
      stopPcmPlayback();
    }
    const binary = atob(wavB64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    const blob = new Blob([bytes], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    if (wavQueue.length >= WAV_QUEUE_MAX_SIZE) {
      const dropped = wavQueue.shift();
      URL.revokeObjectURL(dropped!.url);
      console.warn(`[TTS][播放] WAV 队列已满(${WAV_QUEUE_MAX_SIZE}), 丢弃最旧数据并释放 URL`);
    }
    wavQueue.push({ url });

    if (!isPlayingQueue) {
      isPlayingQueue = true;
      playNextInQueue();
    }
  }

  // 把当前活跃流挂起：未调度块移入缓冲，已预排块继续播完（插播从其尾部开始）
  function suspendActivePcmStream(incomingRequestId: string) {
    if (!pcmActiveRequestId) return;
    const suspended: SuspendedPcmStream = {
      requestId: pcmActiveRequestId,
      queue: pcmQueue,
      ended: false,
      suspendedAt: Date.now(),
    };
    pcmSuspendedStreams.push(suspended);
    pcmQueue = [];
    console.log(
      `[TTS][播放] 插播挂起: ${suspended.requestId} -> ${incomingRequestId}，暂存未调度块 ${suspended.queue.length}，已预排 ${pcmScheduledSources.size} 块播完后插入；插播收尾后自动续播`,
    );
  }

  // 当前流收尾后接回挂起流续播（后进先出：嵌套插播按逆序逐层恢复，最后才回到最外层主任务）；
  // 挂起超时且无块无收尾的流视为已死并丢弃。返回是否成功接回了一条流
  function resumeNextSuspendedStream(): boolean {
    while (pcmSuspendedStreams.length > 0) {
      const next = pcmSuspendedStreams.pop()!;
      if (!next.ended && next.queue.length === 0 && Date.now() - next.suspendedAt > 30000) {
        pcmStoppedRequestIds.add(next.requestId);
        console.warn(`[TTS][播放] 挂起流 ${next.requestId} 超时（>30s）无新块且未收尾，判定已死并丢弃`);
        continue;
      }
      pcmQueue = pcmQueue.concat(next.queue);
      pcmActiveRequestId = next.requestId;
      pcmStreamEnded = next.ended;
      console.log(
        `[TTS][播放] 插播结束，恢复流 ${next.requestId}：接回 ${next.queue.length} 块续播${next.ended ? "（该流已收尾，播完即完成）" : ""}`,
      );
      return true;
    }
    return false;
  }

  async function playPcmChunk(
    pcmB64: string,
    sampleRate: number,
    requestId?: string,
    chunkIndex?: number,
    ttsText?: string,
  ) {
    if (!pcmB64) {
      return;
    }

    // 已被打断丢弃的流（barge-in/stopAll）的迟到残块：直接丢弃，防止复活续播
    if (requestId && pcmStoppedRequestIds.has(requestId)) {
      return;
    }

    // 属于挂起流的后续块：追加进其缓冲，不参与当前时间线调度（插播期间旧流照常接收）
    if (requestId) {
      const suspended = pcmSuspendedStreams.find((s) => s.requestId === requestId);
      if (suspended) {
        const data = decodePcmBase64(pcmB64);
        if (data.length === 0) return;
        pcmEnqueueCount += 1;
        while (suspended.queue.length >= PCM_QUEUE_MAX_CAPACITY) {
          suspended.queue.shift();
          pcmDroppedCount += 1;
        }
        suspended.queue.push({ data, sampleRate, seq: pcmEnqueueCount });
        return;
      }
    }

    // 流切换守卫（插播挂起版）：新 request_id 到来而当前流未收尾 → 挂起当前流而非清场。
    // 当前流已预排的块（≤2 秒）播完后新流插入；新流收尾后自动续播（见 resumeNextSuspendedStream）。
    // pcmStreamEnded=true 表示当前流已正常收尾（仅剩尾音在播），新流直接排在尾音之后，无需挂起。
    // 活跃位是 WAV 插播虚拟流时不挂起（WAV 不在 PCM 时间线上，无缓冲可接回），
    // 新 PCM 流直接接管活跃位，WAV 队列播完时按"已有 PCM 在播"分支收尾。
    if (
      requestId &&
      pcmActiveRequestId &&
      pcmActiveRequestId !== WAV_STREAM_ID &&
      requestId !== pcmActiveRequestId &&
      !pcmStreamEnded
    ) {
      suspendActivePcmStream(requestId);
    }
    if (requestId) {
      if (requestId !== pcmActiveRequestId) {
        // 活跃流切换：完成判定跟随新流（旧流即便残留 end 标志也不再触发提前完成）
        pcmStreamEnded = false;
      }
      pcmActiveRequestId = requestId;
    }

    startStatsLogging();

    // 到达即解码：解码成本移出播放关键路径，后续只做轻量调度
    const data = decodePcmBase64(pcmB64);
    pcmEnqueueCount += 1;
    const seq = pcmEnqueueCount;
    const textPreview = ttsText ? ttsText.replace(/\s+/g, " ").slice(0, 120) : "";

    if (data.length === 0) {
      console.log(
        `[TTS][播放] 空 PCM 块${chunkIndex !== undefined ? ` #${chunkIndex + 1}` : ""}${requestId ? ` request_id=${requestId}` : ""}，跳过`,
      );
      return;
    }

    // ---- 溢出保护 ----
    // 1) 动态扩容：突发流量下先扩容吸收峰值（最多 MAX_CAPACITY）
    maybeExpandCapacity();
    // 2) 硬上限内仍放不下 → 平滑降级：丢弃最旧的未调度块。
    //    只丢队列里的块，已提交到音频时间线的块不受影响，播放连续性不被破坏；
    //    每块自带接缝淡入淡出，丢弃处无爆音。
    while (pcmQueue.length >= pcmCapacity) {
      const dropped = pcmQueue.shift();
      pcmDroppedCount += 1;
      console.warn(
        `[TTS][播放] PCM 队列溢出(${pcmQueue.length + 1}/${pcmCapacity}), 平滑丢弃最旧未调度块 seq=${dropped?.seq}, 累计丢弃=${pcmDroppedCount}`,
      );
    }

    pcmQueue.push({ data, sampleRate, seq });
    reportQueueDepth();
    if (seq % CHUNK_LOG_SAMPLE === 0) {
      console.log(
        `[TTS][播放] 入队进度: 第 ${seq} 块${textPreview ? `, 文本=${textPreview}` : ""}, 队列=${pcmQueue.length}/${pcmCapacity}`,
      );
    }

    // 触发调度泵（幂等，串行执行）
    await pumpSchedule();
    tryFinishPcmPlayback();
  }

  function endPcmStream(requestId?: string) {
    if (requestId) {
      if (pcmStoppedRequestIds.has(requestId)) return;
      if (pcmActiveRequestId && requestId !== pcmActiveRequestId) {
        // 收尾的是挂起中的流：仅做标记，等它被接回后排空队列再完成
        const suspended = pcmSuspendedStreams.find((s) => s.requestId === requestId);
        if (suspended) {
          suspended.ended = true;
          return;
        }
        console.warn(
          `[TTS][播放] 收到非活跃流的收尾 request_id=${requestId}（活跃=${pcmActiveRequestId ?? "无"}），忽略`,
        );
        return;
      }
    }
    pcmStreamEnded = true;
    // 当前流收尾：若有挂起流，按后进先出接回最近被挂起的一条续播
    resumeNextSuspendedStream();
    // 接回的缓冲块必须立即驱动调度泵：若插播音频已全部播完（无在播源触发
    // handleSourceEnded）、且挂起流已收尾（不再有新块到达），缓冲块会永久
    // 滞留队列——表现为续播时"丢了几句话"。
    void pumpSchedule().then(() => tryFinishPcmPlayback());
    tryFinishPcmPlayback();
  }

  function playNextInQueue() {
    if (wavQueue.length === 0) {
      isPlayingQueue = false;
      clearCurrentAudio();
      // WAV 是插播进来的（虚拟流占着活跃位）：播完先接回被挂起的 PCM 流续播，
      // 完成回调推迟给 PCM 流收尾时发；仅当没有挂起流可接（WAV 本就是主角）
      // 才按旧语义立即发 wav-queue 完成
      if (pcmActiveRequestId === WAV_STREAM_ID) {
        pcmActiveRequestId = undefined;
        wavInterjectActive = false;
        if (resumeNextSuspendedStream()) {
          void pumpSchedule().then(() => tryFinishPcmPlayback());
          return;
        }
      }
      callbacks.onPlaybackComplete?.("wav-queue");
      return;
    }

    const next = wavQueue.shift()!;
    const audio = new Audio(next.url);
    if (audioOutputDeviceId && audioOutputDeviceId !== '__GLASSES_AUDIO__') {
      if (typeof (audio as any).setSinkId === 'function') {
        (audio as any).setSinkId(audioOutputDeviceId).catch((err: any) => {
          console.warn('[TTS][播放] 设置扬声器失败:', err);
        });
      }
    }
    currentAudio = audio;
    currentAudioUrl = next.url;

    const continueQueue = () => {
      if (currentAudio !== audio) {
        return;
      }
      clearCurrentAudio();
      playNextInQueue();
    };

    audio.onended = continueQueue;
    audio.onerror = continueQueue;
    void audio.play().catch(continueQueue);
  }

  function clearCurrentAudio() {
    if (currentAudio) {
      currentAudio.onended = null;
      currentAudio.onerror = null;
      try {
        currentAudio.pause();
        if (Number.isFinite(currentAudio.duration)) {
          currentAudio.currentTime = currentAudio.duration;
        }
      } catch {}
      currentAudio = null;
    }

    if (currentAudioUrl) {
      URL.revokeObjectURL(currentAudioUrl);
      currentAudioUrl = null;
    }
  }

  function stopWavQueuePlayback() {
    wavQueue.forEach((item) => {
      URL.revokeObjectURL(item.url);
    });
    wavQueue = [];
    isPlayingQueue = false;
    clearCurrentAudio();
  }

  function stopPcmPlayback() {
    // 记录被打断丢弃的流 id（含挂起中的流），其后续迟到残块将被丢弃
    if (pcmActiveRequestId) {
      pcmStoppedRequestIds.add(pcmActiveRequestId);
    }
    for (const s of pcmSuspendedStreams) {
      pcmStoppedRequestIds.add(s.requestId);
    }
    pcmSuspendedStreams = [];
    pcmStreamEnded = false;
    pcmQueue = [];
    pcmTimelineInitialized = false;
    pcmWarnActive = false;
    pcmCriticalActive = false;
    // 打断（barge-in）：立即静音所有已预排+在播的源节点
    pcmScheduledSources.forEach((source) => {
      try {
        source.onended = null;
        source.stop();
      } catch {}
    });
    pcmScheduledSources.clear();
    pcmPlayedCount = 0;
    pcmDroppedCount = 0;
    pcmUnderrunCount = 0;
    pcmEnqueueCount = 0;
    pcmHighWaterMark = 0;
    pcmCapacity = PCM_QUEUE_BASE_CAPACITY;
    pcmActiveRequestId = undefined;
    wavInterjectActive = false; // 打断同时结束 WAV 插播状态（挂起流已在上面清空）
    stopStatsLogging();
  }

  function stopAll() {
    stopPcmPlayback();
    stopWavQueuePlayback();
  }

  /**
   * 是否仍有 PCM 音频在播/待播（活跃流未收尾、队列/预排/挂起流非空）。
   * assistant_final 到达时若为 false，说明本轮 TTS 已播完或根本没有音频，
   * 调用方据此不应再把 isAiSpeaking 置回 true（否则回声门控会永久压麦）。
   */
  function hasPendingPcmAudio(): boolean {
    return (
      pcmSchedulerRunning ||
      pcmQueue.length > 0 ||
      pcmScheduledSources.size > 0 ||
      pcmSuspendedStreams.length > 0 ||
      (pcmActiveRequestId !== undefined && !pcmStreamEnded)
    );
  }

  /**
   * WS 断线重连时的全量复位：清空播放状态、挂起流与墓碑（旧连接不会再有
   * 残块到来，墓碑留着只会误杀新流），不触发 onPlaybackComplete——
   * 调用方（useChatEngine onDisconnected）自行做上层状态复位。
   */
  function reconnectReset() {
    stopAll();
    pcmStoppedRequestIds.clear();
    pcmActiveRequestId = undefined;
    pcmStreamEnded = false;
  }

  async function destroy() {
    pcmDestroyed = true;
    stopAll();
    if (pcmShrinkTimer) {
      clearInterval(pcmShrinkTimer);
      pcmShrinkTimer = null;
    }
    if (pcmAudioContext) {
      const audioContext = pcmAudioContext;
      pcmAudioContext = null;
      await audioContext.close();
    }
  }

  return {
    destroy,
    endPcmStream,
    enqueueWavBase64,
    getStats: getPcmQueueStats,
    hasPendingPcmAudio,
    // 音频上下文是否处于可出声状态（running）——警报处理用它在通道未激活时提示用户点击
    isPcmContextRunning,
    playPcmChunk,
    reconnectReset,
    setCallbacks,
    setOutputDevice,
    stopAll,
    stopPcmPlayback,
    warmUp,
  };
}
