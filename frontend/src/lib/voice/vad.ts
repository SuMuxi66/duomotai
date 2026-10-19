/**
 * 内联 VAD 与回声门控（复用已有音频帧数据，不额外获取麦克风）
 *
 * 从 wakeword-stt.ts 抽取；音量计算复用 ./pcm 的 calculateRmsVolume。
 */

import { calculateRmsVolume } from "./pcm";

/* =========================================================================
 * 轻量回声抑制（前端能量门控）
 * -------------------------------------------------------------------------
 * 问题：AI 播报（本地/眼镜扬声器）声音会被麦克风回采进设备 ASR，误识别成用户
 * 指令，造成"AI 自己打断自己"或把指令串成回声文本。
 *
 * getUserMedia 的 echoCancellation 只对浏览器自己回放的系统输出有效，不能
 * 覆盖：① 眼镜扬声器（回放在设备端）；② 经过 AudioPipeline 的设备 ASR 推流。
 * 因此这里补一层 *能量门控*：
 *   - 维护麦克风底噪基线（无语音时目标电平的滑动均值）
 *   - AI 播报期间，帧能量低于"基线 + 明显涨幅阈值"视为疑似回声 → 不推流
 *   - 检测到显著高于基线的真人语音（能量跃升）→ 放行；并在打断后进入冷却，
 *     忽略随后的回声尾音，避免连环误打断
 * ------------------------------------------------------------------------- */
export interface EchoGateHooks {
  /** 能量门控判定为"真人语音增量"，可触发打断 */
  onHumanSpeechGate?: () => void;
  debug?: boolean;
}

export function createEchoGate(hooks: EchoGateHooks = {}) {
  // 底噪基线：无语音帧电平的指数滑动均值（0-100 音量刻度）
  let noiseFloor = 6;
  // 真人语音相对基线需达到的相对增量（音量刻度，经验值）
  // 2026-10-13 从 14 调至 22；当晚用户要求提到 30：22 在现场仍偏灵敏
  const GATE_RISE = 30;
  // 打断防抖：需连续 N 帧高于门限才判真人语音（单帧瞬态噪声不触发打断）。
  // ScriptProcessor 4096 帧 @48kHz ≈ 85ms/帧，2 帧 ≈ 170ms（短于真人一个音节）
  const GATE_CONSECUTIVE_FRAMES = 2;
  // 打断后冷却：忽略这段时间内的"疑似回声"触发，防止 AI 声音连环误打断（参照实操4 的 1.5s）
  const INTERRUPT_COOLDOWN_MS = 1500;
  // AI 回声探测定标（2026-10-13 新增）：
  // 问题：AI TTS 从扬声器放出来被麦克风回采，纯能量门控分不清"AI 自己的声音"和
  // "真人说话"——AI 一开口能量必然远超 底噪+GATE_RISE → 自己被自己打断，
  // 且高能量回声帧照常推给端侧 ASR → Pi VAD speech_start 连发（日志 1605 连刷）。
  // 解法：setAiSpeaking(true) 后的窗口内，高于门限的帧几乎必然是 AI 回采
  // （扬声器→空气→麦克风几十 ms，先于任何真人语音到达），取均值作回声电平估计；
  // 之后真人判定 = 超过 底噪门限 且 超过 回声电平×ECHO_RISE_RATIO（真人=回声之上叠加）。
  const ECHO_PROBE_MS = 300;      // 探头窗口（≈3.5 帧 @85ms）
  const ECHO_RISE_RATIO = 1.25;   // 真人语音相对回声电平的倍率
  let aiSpeaking = false;
  let aiSpeakingAt = 0;
  let echoProbeSum = 0;
  let echoProbeN = 0;
  let echoLevel = 0;              // 回声电平估计；0=未探测到回声（回退纯底噪门限）
  let lastInterruptAt = 0;
  let smoothing = 0.15;
  let humanStreak = 0; // 连续高于门限的帧数（打断防抖用）

  function rmsOf(samples: Float32Array): number {
    return calculateRmsVolume(samples);
  }

  return {
    setAiSpeaking(s: boolean) {
      if (s && !aiSpeaking) {
        // 每次 AI 开口重新探测回声（回放音量/麦克风位置可能变化）
        aiSpeakingAt = Date.now();
        echoProbeSum = 0;
        echoProbeN = 0;
        echoLevel = 0;
      }
      aiSpeaking = s;
    },
    isAiSpeaking() { return aiSpeaking; },
    /** 当前底噪基线（0-100 音量刻度）：供自适应 VAD 阈值用 */
    getNoiseFloor() { return noiseFloor; },

    /**
     * 对一帧麦克风输入做回声门控。
     * @returns true=放行推流；false=疑似回声，应抑制推流。
     */
    gateFrame(samples: Float32Array): boolean {
      const rms = rmsOf(samples);
      // 更新底噪基线（取较低侧平滑，避免被瞬时噪声抬高）
      if (!aiSpeaking) {
        noiseFloor = noiseFloor * (1 - smoothing) + rms * smoothing;
        noiseFloor = Math.min(noiseFloor, 20); // 底噪不应过高
        humanStreak = 0;
        return true;
      }
      // AI 播报期间：真人语音 = 能量显著高于基线
      const gateRise = noiseFloor + GATE_RISE;
      // 回声探头窗口：AI 自己的声音先到，窗口内高于门限的帧记入回声估计，
      // 且一律不推流不打断（代价：TTS 开口 300ms 内的真人抢话会被吞，罕见场景）
      if (Date.now() - aiSpeakingAt < ECHO_PROBE_MS) {
        if (rms > gateRise) { echoProbeSum += rms; echoProbeN++; }
        return false;
      }
      // 出窗时用探头均值定标回声电平；没采到（回采低于门限）则 echoLevel 保持 0
      if (echoLevel === 0 && echoProbeN > 0) echoLevel = echoProbeSum / echoProbeN;
      // 真人 = 超过底噪门限 且 超过回声电平倍率（无回声探测时只用底噪门限，保持原行为）
      const isHuman = rms > gateRise && (echoLevel === 0 || rms > echoLevel * ECHO_RISE_RATIO);
      if (isHuman) {
        humanStreak++;
      } else {
        humanStreak = 0;
      }
      if (isHuman) {
        // 连续帧确认 + 冷却：单帧瞬态噪声（键盘/椅子/回声尾音）不再触发误打断；
        // 帧本身照常放行，ASR 实时推流不因防抖延迟
        const now = Date.now();
        if (humanStreak >= GATE_CONSECUTIVE_FRAMES && now - lastInterruptAt >= INTERRUPT_COOLDOWN_MS) {
          lastInterruptAt = now;
          hooks.onHumanSpeechGate?.();
        }
        return true; // 放行真人语音
      }
      // 疑似回声（含 AI 自己声音）：不推流（抑制）
      return false;
    },

    /** 手动标记一次打断（外部 sendInterrupt 后调用），进入冷却 */
    noteInterrupt() {
      lastInterruptAt = Date.now();
    },

    reset() {
      noiseFloor = 6;
      aiSpeaking = false;
      aiSpeakingAt = 0;
      echoProbeSum = 0;
      echoProbeN = 0;
      echoLevel = 0;
      lastInterruptAt = 0;
      humanStreak = 0;
    },
  };
}

/**
 * 创建内联 VAD 控制器（复用已有的音频数据，不额外获取麦克风）
 * threshold 支持传函数（每帧实时取值）——云端路径用自适应阈值（按底噪动态算），
 * 固定阈值 60 对安静麦克风可能永远不触发（2026-09-18 实测：采集电平 20-40 的麦
 * 说半天 VAD 不动，全靠静默兜底轮上传，且流式轮永远不开始 = 无实时转写）。
 */
export function createInlineVAD(
  threshold: number | (() => number),
  silenceSeconds: number,
  callbacks: {
    onSpeechStart?: () => void;
    onSpeechEnd?: () => void;
  }
) {
  let isSpeech = false;
  let silenceStartTime = 0;

  const thresholdOf = () => (typeof threshold === "function" ? threshold() : threshold);

  return {
    /** 处理一帧音频数据 */
    processFrame(samples: Float32Array) {
      const volume = calculateRmsVolume(samples);
      const now = Date.now();
      const th = thresholdOf();

      if (!isSpeech) {
        if (volume >= th) {
          isSpeech = true;
          silenceStartTime = 0;
          callbacks.onSpeechStart?.();
        }
      } else {
        if (volume < th) {
          if (silenceStartTime === 0) {
            silenceStartTime = now;
          }
          if ((now - silenceStartTime) / 1000 >= silenceSeconds) {
            isSpeech = false;
            silenceStartTime = 0;
            callbacks.onSpeechEnd?.();
          }
        } else {
          silenceStartTime = 0;
        }
      }
    },
    /** 重置状态 */
    reset() {
      isSpeech = false;
      silenceStartTime = 0;
    },
    /** 获取当前是否在说话 */
    isSpeaking() {
      return isSpeech;
    },
  };
}
