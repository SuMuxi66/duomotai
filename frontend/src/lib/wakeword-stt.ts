/**
 * 基于后端 STT 的唤醒词检测模块
 * 
 * 不依赖浏览器 SpeechRecognition，而是通过麦克风录音 + 后端 SiliconFlow STT 识别
 * 适用于国内网络环境，避免 Google Speech API 不稳定的问题
 * 
 * 状态机：
 * IDLE → LISTENING → COLLECTING → IDLE → LISTENING
 *
 * 基础工具（PCM 编码/音量、内联 VAD 与回声门控、繁简转换与唤醒词匹配、云端失败
 * 文案、云端流式轮次状态）已抽取至 ./voice/ 子模块。
 */

import { playAnswerTone, playHangupTone } from "./call-sounds";
import { startSttLiveDisplay, SttLiveDisplayHandle } from "./stt-live-display";
import {
  calculateRmsVolume,
  downsampleBuffer,
  floatTo16BitPCM,
  toBase64,
} from "./voice/pcm";
import { createEchoGate, createInlineVAD } from "./voice/vad";
import { looksLikeCloudFailure } from "./voice/failure-markers";
import {
  toSimplifiedChinese,
  containsWakeWord as containsWakeWordIn,
  extractAfterWakeWord as extractAfterWakeWordIn,
} from "./voice/wake-words";
import { CloudStreamRound } from "./voice/cloud-round";

/** 端侧 ASR（RK3588 设备）：16kHz PCM16 推流，返回 {type:'asr',text,isPartial} 流式结果，比后端云端 STT 快得多 */
export const DEFAULT_ASR_URL = "ws://192.168.1.248:8768/asr";

/* 全局音频上下文解锁（解决唤醒词无反馈）
 * Chrome 自动播放策略会把非用户手势创建的 AudioContext 挂起为 suspended，
 * 此时 onaudioprocess 不触发，麦克风音频不会推送到端侧 KWS/ASR。
 * 这里登记所有由唤醒词创建的 AudioContext，并在首次用户手势时强制 resume，
 * 保证用户点击页面后音频上下文一定恢复运行。 */
const activeAudioContexts = new Set<AudioContext>();
let gestureUnlockBound = false;
function registerAudioContext(ctx: AudioContext) {
  activeAudioContexts.add(ctx);
  if (gestureUnlockBound) return;
  gestureUnlockBound = true;
  const unlock = () => {
    activeAudioContexts.forEach((c) => {
      if (c.state === 'suspended') c.resume().catch(() => {});
    });
  };
  window.addEventListener('pointerdown', unlock);
  window.addEventListener('touchstart', unlock);
}

enum WakeWordState {
  IDLE = 'IDLE',
  LISTENING = 'LISTENING',
  AWAITING_FEEDBACK = 'AWAITING_FEEDBACK',
  COLLECTING = 'COLLECTING',
  /** 连续对话窗口期：唤醒后进入多轮对话，无需重复唤醒词（手机级体验） */
  CONVERSING = 'CONVERSING',
}

export interface SttWakeWordConfig {
  wakeWords: string[];
  onWakeWordDetected: (recognizedText: string) => void;
  onWakeWordTriggered?: () => void;
  /** 发送音频到后端进行识别, sttOnly=true 时仅做 STT 不自动提交 LLM */
  sendAudioForStt: (pcm16_b64: string, isLast: boolean, sttOnly?: boolean) => void;
  audioDeviceId?: string;
  onStatusChange?: (status: string) => void;
  onError?: (errorType: string, message: string) => void;
  onTranscriptChange?: (text: string) => void;
  /** 轮询识别间隔（毫秒），默认 2500ms */
  pollIntervalMs?: number;
  /** KWS WS 地址，启用端侧 KWS 唤醒词检测（替代后端STT匹配唤醒词） */
  kwsUrl?: string;
  /** 端侧 ASR WS 地址，启用后指令收集走端侧 VAD+ASR（替代后端STT），空则回退后端STT */
  asrUrl?: string;
  /** 连续对话窗口：唤醒后进入多轮对话，无需重复唤醒词（手机级体验）。默认 true */
  continuousDialog?: boolean;
  /** 连续对话窗口时长（毫秒）：AI 播报完成后等待用户继续说的时长，超时退回待命。默认 8000 */
  continuousDialogWindowMs?: number;
  /** 连续对话窗口激活/结束回调（用于 UI 提示） */
  onConversationWindowChange?: (active: boolean) => void;
  /** 连续对话窗口期内用户开始说话时的回调（用于打断 AI 播报，无需播唤醒反馈） */
  onUserSpeechInDialog?: () => void;
  /** 仅显示用的浏览器实时转写回调（后端整段识别无中间结果，由浏览器并行识别补实时上屏） */
  onLiveDisplayText?: (text: string) => void;
  /**
   * 云端优先：唤醒后并行双发——音频在推端侧 ASR 的同时整段上传后端云端 STT，
   * 云端结果先到就用云端（准确率更高），超时或云端不可用则用端侧结果兜底。
   * 关闭则退回纯端侧（低延迟）。默认 true。
   * 可传函数：每次裁决时实时取值，UI 开关切换无需重建监听器。
   */
  preferCloud?: boolean | (() => boolean);
  /**
   * 云端流式会话通道（云端优先时启用）：进入收集期 begin()，说话期间逐帧 push
   * （16k PCM16 base64），说话结束 end() 收口。云端 partial 说话期间即回，
   * 输入框实时显示云端文本（优先于端侧）；省去说完后的整段重传。
   */
  cloudStream?: {
    begin: () => void;
    push: (pcm16_b64: string) => void;
    end: () => void;
  };
  debug?: boolean;
}

export interface SttWakeWordController {
  start: () => Promise<void>;
  stop: () => void;
  destroy: () => void;
  startCollecting: () => void;
  /** 处理后端返回的 STT 识别结果 */
  handleSttResult: (text: string, isPartial: boolean) => void;
  /** 通知 AI 播报状态变化（用于精确计时连续对话窗口：从 AI 播报完成开始算窗口） */
  notifyAiState: (speaking: boolean) => void;
  /** 通知唤醒反馈 TTS 播报状态变化（播放期间屏蔽 ASR/KWS 推流，防止 TTS 回声自循环） */
  setFeedbackSpeaking: (speaking: boolean) => void;
}

export async function createSttWakeWordListener(
  config: SttWakeWordConfig
): Promise<SttWakeWordController> {
  const {
    wakeWords,
    onWakeWordDetected,
    onWakeWordTriggered,
    sendAudioForStt,
    audioDeviceId,
    onStatusChange,
    onError,
    onTranscriptChange,
    pollIntervalMs = 2500,
    kwsUrl = "ws://192.168.1.248:8767/kws",
    asrUrl = DEFAULT_ASR_URL,
    continuousDialog = true,
    continuousDialogWindowMs = 8000,
    onConversationWindowChange,
    onUserSpeechInDialog,
    onLiveDisplayText,
    preferCloud = true,
    cloudStream,
    debug = false,
  } = config;

  // 云端等待窗口（毫秒）：发出云端请求后最多等这么久，超时即采用端侧结果。
  // 2026-09-11 由 2500 改 5000（用户拍板"先云端，5 秒没回换端侧"）：实测整段上传 +
  // Fun-ASR Realtime 往返可达 2~3s，2500 窗口曾让端侧乱码"币分上"抢在云端正确
  // 结果"关闭风扇"之前提交。静音收口计时已联动 CLOUD_WAIT_MS+1500，无需另改。
  const CLOUD_WAIT_MS = 5000;
  // preferCloud 支持传函数：实时读取 UI 开关（切换后立即生效，无需重建监听器）
  const shouldPreferCloud = (): boolean =>
    typeof preferCloud === "function" ? preferCloud() : preferCloud;
  let state: WakeWordState = WakeWordState.IDLE;
  let textBuffer = "";
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  let maxTimer: ReturnType<typeof setTimeout> | null = null;
  // 浏览器并行识别会话（仅用于输入框实时显示，不参与消息发送）
  let liveDisplay: SttLiveDisplayHandle | null = null;
  // 进入指令收集期：启动一份实时转写会话，收集期内多短语累加显示（与 textBuffer 累积语义一致）
  // 云端中间结果出现后（cloudRound.partialSeen）浏览器实时文本不再上屏，避免覆盖云端显示
  function startLiveDisplayForCollecting() {
    liveDisplay?.stop();
    liveDisplay = onLiveDisplayText
      ? startSttLiveDisplay((t) => { if (!cloudRound.partialSeen) onLiveDisplayText(t); })
      : null;
  }

  // Audio 相关
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;
  let acc: Float32Array[] = [];
  const targetSampleRate = 16000;
  let isRecording = false;

  // KWS WS（端侧唤醒词检测，替代后端STT匹配）
  let kwsWs: WebSocket | null = null;
  // KWS 可达性探测：拔掉香橙派后 KWS 连不上会无限 1s 重连且状态栏仍显示"已启动"
  // （假在线，唤醒词永远检测不到）。连续失败达阈值 → 自动启动后端 STT 轮询兜底
  // （说的唤醒词由云端识别匹配）；KWS 重新连上（Pi 插回）→ 自动停轮询恢复纯端侧。
  let kwsConsecFails = 0;
  const KWS_FALLBACK_FAILS = 3;
  // 轮询兜底是否由 KWS 降级触发（区别于未配 kwsUrl 的常驻轮询），恢复时用于还原状态文案
  let kwsFallbackPolling = false;
  // ASR WS（端侧 VAD+ASR 指令收集，替代后端STT）
  let asrWs: WebSocket | null = null;

  // 轮询识别
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  // 内联 VAD（复用 processor 音频数据，不额外获取麦克风）
  let inlineVad: ReturnType<typeof createInlineVAD> | null = null;
  let hasSpeech = false; // 当前轮次是否有说话

  let lastStatus = "";

  // 连续对话窗口相关
  let aiSpeaking = false;
  let conversationWindowTimer: ReturnType<typeof setTimeout> | null = null;

  // 唤醒反馈 TTS 播放屏蔽期：播放期间屏蔽 ASR/KWS 推流，防止 TTS 回声自循环
  // （"你好我在"被 ASR 识别成指令 / 被 KWS 误触发唤醒词）
  let feedbackSpeaking = false;

  // ===== 云端优先（并行双发）状态 =====
  // 端侧 ASR 结果先到时暂存在此，等云端裁决；云端胜出则丢弃
  let pendingOnDeviceText = "";
  // 本轮是否已发过云端请求（端侧 final 与 VAD onSpeechEnd 谁先到谁发，只发一次）
  let cloudRequestSent = false;
  // 云端等待超时定时器：到点未拿到有效云端结果则回退端侧
  let cloudWaitTimer: ReturnType<typeof setTimeout> | null = null;
  // 云端专用音频缓冲：收集期内无条件累积，不依赖 inlineVad 判定。
  // （acc 只在 VAD 判定"在说话"时才累积，前端 VAD 不触发时 acc 会空，导致无音频可发云端）
  let cloudAcc: Float32Array[] = [];
  const CLOUD_ACC_MAX_FRAMES = 800; // 约 800×4096/48k ≈ 68s，远超 15s 单轮上限，仅防内存失控

  // ===== 云端流式会话状态 =====
  // 已抽取为 ./voice/cloud-round.ts 的 CloudStreamRound（与持续识别路径共用同一实现）
  const cloudRound = new CloudStreamRound({ tag: '[SttWakeWord]', debug });

  // 进入收集期时开启云端流式会话（云端优先且通道可用）
  function beginCloudStreamRound() {
    if (!shouldPreferCloud() || !cloudStream) { cloudRound.reset(() => {}); return; }
    cloudRound.begin(() => cloudStream!.begin());
  }

  // 回声抑制门控：AI 播报期间抑制疑似回声推流（防止 TTS 声音被设备 ASR 识别成指令而打断）
  // 唤醒词模式下打断由设备 VAD speech_start 触发，门控只负责抑制回声帧，此处不接打断 hook
  const echoGate = createEchoGate();

  function updateStatus(status: string) {
    if (lastStatus === status) return;
    lastStatus = status;
    onStatusChange?.(status);
  }

  function updateTranscript(text: string) {
    onTranscriptChange?.(text.trim());
  }

  function containsWakeWord(text: string): boolean {
    return containsWakeWordIn(wakeWords, text);
  }

  function extractAfterWakeWord(text: string): string {
    return extractAfterWakeWordIn(wakeWords, text);
  }

  function startSilenceTimer(ms: number) {
    if (silenceTimer) clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      sendResult();
    }, ms);
  }

  function startMaxTimer() {
    if (maxTimer) clearTimeout(maxTimer);
    maxTimer = setTimeout(() => {
      sendResult();
    }, 15000);
  }

  let resultSubmitted = false;
  function sendResult() {
    if (resultSubmitted) return;  // 防重：避免多实例/重复触发导致空结果跳过
    resultSubmitted = true;
    // 指令提交，收集期结束：停止实时转写会话
    liveDisplay?.stop();
    liveDisplay = null;
    let result = textBuffer.trim();
    result = result.replace(/^。|。$/g, '').replace(/([\u4e00-\u9fa5])。/g, '$1');
    result = result.replace(/^你好[，,]?我在[！!，,。.?？、；;：:\s]*/g, '');
    result = result.replace(/^你好[，,]?我在/g, '');
    if (result) {
      const simplified = toSimplifiedChinese(result);
      console.log('[SttWakeWord] 发送最终结果:', simplified);
      onWakeWordDetected(simplified);
      if (continuousDialog) {
        // 连续对话：不销毁监听器，进入窗口期等待用户继续说（无需再唤醒）
        enterConversationWindow();
      } else {
        // 单次唤醒模式：发送完立即退回待命，下一次对话需重新喊唤醒词。
        // 同时退出收集状态并停止 ASR 推流，避免麦克风持续收音把 AI 播报回声/噪音误识别成指令。
        state = WakeWordState.LISTENING;
        resultSubmitted = false; // 允许下一轮唤醒后再发送
        acc = [];
        hasSpeech = false;
        inlineVad?.reset();
        // 确保 KWS 保持连接以继续监听下一次唤醒词（若期间断开则重连）
        if (!kwsWs || kwsWs.readyState !== WebSocket.OPEN) {
          if (openKws) openKws();
        }
        onConversationWindowChange?.(false);
        updateStatus('等待唤醒词');
      }
    } else {
      console.log('[SttWakeWord] 空结果，不发送');
      onWakeWordDetected("");
      // 空结果不进连续对话窗口，退回待命等待唤醒词
      if (state !== WakeWordState.IDLE) {
        state = WakeWordState.LISTENING;
        updateStatus('未检测到语音，等待唤醒词');
      }
    }
    textBuffer = "";
    updateTranscript("");
    resetCloudState();
    stopPolling();
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    if (maxTimer) {
      clearTimeout(maxTimer);
      maxTimer = null;
    }
  }

  // ============ 连续对话窗口（手机级多轮对话）============
  function startConversationWindowTimer() {
    if (conversationWindowTimer) clearTimeout(conversationWindowTimer);
    conversationWindowTimer = setTimeout(() => {
      conversationWindowTimer = null;
      if (state === WakeWordState.CONVERSING) {
        // 窗口超时：退回待命，等待下一次唤醒词
        state = WakeWordState.LISTENING;
        textBuffer = "";
        updateTranscript("");
        onConversationWindowChange?.(false);
        updateStatus('连续对话结束，等待唤醒词');
      }
    }, continuousDialogWindowMs);
  }

  function enterConversationWindow() {
    state = WakeWordState.CONVERSING;
    resultSubmitted = false; // 允许下一轮发送
    textBuffer = "";
    acc = [];
    hasSpeech = false;
    inlineVad?.reset();
    updateTranscript("");
    onConversationWindowChange?.(true);
    updateStatus('连续对话中，可直接继续说（无需唤醒词）');
    // 窗口计时：若 AI 正在播报，则等 AI 播完（notifyAiState(false)）再计时；
    // 否则立即启动兜底计时（兼容未接入 AI 状态信号的场景）
    if (!aiSpeaking) {
      startConversationWindowTimer();
    }
  }

  // 由外部在 AI 播报状态变化时调用
  function notifyAiState(speaking: boolean) {
    aiSpeaking = speaking;
    // 同步回声门控：AI 播报期间启用回声抑制（抑制 TTS 回声推流）
    echoGate.setAiSpeaking(speaking);
    // 窗口期在等待 LLM 响应期间超时退出（LLM 思考 > 窗口时长）时，
    // AI 开始播报前重新进入连续对话窗口：保证播报期间用户可直接说话打断（barge-in），
    // 无需再喊唤醒词。enterConversationWindow 内部因 aiSpeaking=true 不会启动计时。
    if (speaking && continuousDialog && state === WakeWordState.LISTENING) {
      if (debug) console.log('[SttWakeWord] AI 开始播报，恢复连续对话窗口（可随时打断）');
      enterConversationWindow();
    }
    if (state !== WakeWordState.CONVERSING) return;
    if (speaking) {
      // AI 开始播报：暂停窗口计时，给用户听完的时间
      if (conversationWindowTimer) {
        clearTimeout(conversationWindowTimer);
        conversationWindowTimer = null;
      }
      updateStatus('AI 回复中，可随时打断或稍后继续说');
    } else {
      // AI 播报完成：启动连续对话窗口计时
      startConversationWindowTimer();
    }
  }

  // 由外部在唤醒反馈 TTS 播放状态变化时调用
  // 播放期间屏蔽 ASR/KWS 音频推流，从源头杜绝"你好我在"回声被识别成指令或误触发唤醒词
  function setFeedbackSpeaking(speaking: boolean) {
    feedbackSpeaking = speaking;
    if (speaking) {
      // 反馈开始播放：重置 VAD 和音频缓冲，丢弃可能的回声累积
      acc = [];
      hasSpeech = false;
      inlineVad?.reset();
      if (debug) console.log('[SttWakeWord] 唤醒反馈 TTS 开始播放，屏蔽 ASR/KWS 推流');
    } else {
      if (debug) console.log('[SttWakeWord] 唤醒反馈 TTS 播放结束，恢复 ASR/KWS 推流');
    }
  }

  // 清掉云端等待窗口（云端已出结果/已判定失败/本轮结束）
  function clearCloudWait() {
    if (cloudWaitTimer) {
      clearTimeout(cloudWaitTimer);
      cloudWaitTimer = null;
    }
  }

  // 回退端侧：用暂存的端侧结果收口；端侧也没结果则交给静音/超时计时器走空结果分支
  function fallbackToOnDevice(reason: string) {
    clearCloudWait();
    if (!pendingOnDeviceText) {
      if (debug) console.log(`[SttWakeWord] ${reason}，且端侧无结果，等待端侧或超时`);
      return;
    }
    if (debug) console.log(`[SttWakeWord] ${reason}，回退端侧结果: ${pendingOnDeviceText}`);
    textBuffer = pendingOnDeviceText;
    pendingOnDeviceText = "";
    sendResult();
  }

  // 本轮收集结束后复位云端优先状态，避免跨轮串扰
  function resetCloudState() {
    clearCloudWait();
    cloudRequestSent = false;
    pendingOnDeviceText = "";
    cloudAcc = [];
    // 防悬空流式会话（本轮没走到收口分支就结束时补发 end；后端另有空闲看门狗兜底）
    cloudRound.reset(() => cloudStream?.end());
  }

  // 处理后端返回的 STT 结果
  function handleSttResult(text: string, isPartial: boolean) {
    if (!text.trim()) return;

    const simplified = toSimplifiedChinese(text.trim());
    if (debug) console.log(`[SttWakeWord] 收到识别结果: "${simplified}" (isPartial=${isPartial}, state=${state})`);

    if (state === WakeWordState.LISTENING) {
      // 唤醒词检测
      if (containsWakeWord(simplified)) {
        if (debug) console.log('[SttWakeWord] 检测到唤醒词:', simplified);
        onWakeWordTriggered?.();
        updateStatus('检测到唤醒词，等待反馈播放');
        state = WakeWordState.AWAITING_FEEDBACK;
        textBuffer = extractAfterWakeWord(simplified).replace(/^[，,。.!！?？、；;：:\s]+/, '');
        updateTranscript(textBuffer);
        // 检测到唤醒词后停止轮询识别
        stopPolling();
      }
    } else if (state === WakeWordState.AWAITING_FEEDBACK) {
      // 等待反馈播放完成，丢弃所有识别结果（可能是 TTS 回声）
    } else if (state === WakeWordState.COLLECTING) {
      if (!isPartial) {
        // 云端良性失败（识别不出/未启用/模型不可用）：立即回退端侧，不占用等待窗口
        if (looksLikeCloudFailure(simplified)) {
          if (debug) console.log('[SttWakeWord] 云端识别失败:', simplified);
          fallbackToOnDevice('云端识别失败');
          return;
        }
        // 云端优先：拿到有效云端结果即胜出，丢弃端侧暂存并立即收口
        cloudRound.partialSeen = true;
        clearCloudWait();
        pendingOnDeviceText = "";
        textBuffer += simplified;
        if (debug) console.log('[SttWakeWord] 采用云端结果:', simplified);
        startSilenceTimer(3000);
        sendResult();
      } else if (!looksLikeCloudFailure(simplified)) {
        // 云端中间结果：说话期间实时上屏，此后端侧/浏览器实时文本不再覆盖（云端优先显示）
        cloudRound.partialSeen = true;
        updateTranscript(`${textBuffer}${simplified}`.trim());
        startSilenceTimer(3000);
      }
    }
  }

  // 云端优先：说完后发起云端识别。流式会话已在收音时直接收口（无需整段重发），
  // 否则整段上传后端 STT（sttOnly=true，只回文本不自动提交 LLM）。
  // 端侧 final 与 VAD onSpeechEnd 谁先到谁发，一轮只发一次；返回是否真的发出去了。
  function requestCloudStt(from: string): boolean {
    if (!shouldPreferCloud() || state !== WakeWordState.COLLECTING) return false;
    if (cloudRequestSent) return false;
    if (feedbackSpeaking) return false;
    cloudRequestSent = true;
    if (cloudRound.active) {
      // 流式收口：音频已逐帧上行，end 只触发会话收尾出 stt_final，通常 1s 内可达
      cloudRound.endOnce(() => cloudStream?.end());
      if (debug) console.log(`[SttWakeWord] 云端流式收口（触发点=${from}），等待 stt_final`);
    } else {
      const sent = flushAndSend();
      if (!sent) {
        // 没有可发的音频，撤回标志，留给下一个触发点重试
        cloudRequestSent = false;
        if (debug) console.warn(`[SttWakeWord] 无音频可发云端（触发点=${from}），本轮跳过云端`);
        return false;
      }
      if (debug) console.log(`[SttWakeWord] 已发云端 STT（触发点=${from}），等待窗口 ${CLOUD_WAIT_MS}ms`);
    }
    if (!cloudWaitTimer) {
      cloudWaitTimer = setTimeout(() => {
        cloudWaitTimer = null;
        fallbackToOnDevice('云端超时');
      }, CLOUD_WAIT_MS);
    }
    return true;
  }

  // 发送音频到后端 STT（每次作为独立完整录音段）
  // 音频源优先用 cloudAcc（收集期全量），退回 acc（VAD 过滤后的片段）
  function flushAndSend(): boolean {
    const src = cloudAcc.length > 0 ? cloudAcc : acc;
    if (!ctx || src.length === 0) return false;

    const total = src.reduce((n, a) => n + a.length, 0);
    const buf = new Float32Array(total);
    let off = 0;
    for (const a of src) {
      buf.set(a, off);
      off += a.length;
    }
    acc = [];
    cloudAcc = [];

    const ds = downsampleBuffer(buf, ctx.sampleRate, targetSampleRate);
    const pcm16 = floatTo16BitPCM(ds);
    const b64 = toBase64(new Uint8Array(pcm16.buffer));

    if (debug) console.log(`[SttWakeWord] 发送音频: ${pcm16.length} samples, ${Math.round(pcm16.length / targetSampleRate * 1000)}ms, 状态=${state}`);
    // 唤醒词模式：isLast=true 获取完整 STT 结果，sttOnly=true 不触发 LLM
    sendAudioForStt(b64, true, true);
    // 注意：收集期（COLLECTING）内不停止实时显示会话——多个短语会累加为一条指令，显示保持累加
    return true;
  }

  // 启动轮询识别：每隔 pollIntervalMs 刷新并发送一次音频（仅用于 LISTENING 唤醒词检测）
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      // 轮询仅用于唤醒词检测；COLLECTING 模式由 VAD onSpeechEnd 触发发送
      if (state !== WakeWordState.LISTENING) return;
      if (!hasSpeech) {
        // 没检测到说话，不浪费 API 调用
        acc = []; // 丢弃静音
        return;
      }
      flushAndSend();
      // 每次轮询发送后重置 hasSpeech，下一轮需要重新检测到说话才发送
      hasSpeech = false;
    }, pollIntervalMs);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ============ KWS WS（端侧唤醒词检测）============
  /** KWS 连不上（香橙派不在场）→ 启动后端 STT 轮询兜底唤醒（拉起后由云端识别匹配唤醒词） */
  function maybeStartKwsFallbackPolling(reason: string) {
    if (!kwsUrl) return; // 未配端侧地址本来就常驻轮询
    if (pollTimer) return;
    kwsFallbackPolling = true;
    startPolling();
    console.warn(`[SttWakeWord] KWS 不可达（${reason}）→ 已切换云端轮询唤醒（说唤醒词仍可用，延迟略高）`);
    if (state === WakeWordState.LISTENING) {
      updateStatus('端侧唤醒词不可用，已切换云端唤醒');
    }
  }

  function openKws() {
    if (!kwsUrl) return; // 未配/探测不到端侧地址：由 start() 常驻云端轮询兜底，勿建空 WS（会抛 SyntaxError）
    try {
      kwsWs = new WebSocket(kwsUrl);
      kwsWs.binaryType = "arraybuffer";
      kwsWs.onopen = () => {
        if (debug) console.log('[SttWakeWord] KWS WS 已连接，开始推流');
        const wasFallback = kwsFallbackPolling;
        kwsConsecFails = 0;
        if (wasFallback) {
          // 香橙派插回来了：停轮询，恢复纯端侧 KWS 唤醒（避免双路重复触发）
          kwsFallbackPolling = false;
          stopPolling();
          console.log('[SttWakeWord] KWS 已恢复，停止云端轮询兜底');
          if (state === WakeWordState.LISTENING) {
            updateStatus('端侧唤醒词已恢复');
          }
        }
      };
      kwsWs.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
          if (msg.event === 'wakeword' && state === WakeWordState.LISTENING) {
            if (debug) console.log('[SttWakeWord] KWS 命中:', msg.keyword);
            onKwsHit();
          }
        } catch (e) { /* 忽略非 JSON */ }
      };
      kwsWs.onerror = () => { if (debug) console.warn('[SttWakeWord] KWS WS 错误'); };
      kwsWs.onclose = () => {
        kwsConsecFails += 1;
        if (kwsConsecFails >= KWS_FALLBACK_FAILS) {
          maybeStartKwsFallbackPolling(`连续 ${kwsConsecFails} 次未连上 ${kwsUrl}`);
        }
        if (debug) console.warn('[SttWakeWord] KWS WS 断开，1s 后重连');
        if (state === WakeWordState.LISTENING) {
          setTimeout(() => { if (state === WakeWordState.LISTENING) openKws(); }, 1000);
        }
      };
    } catch (e) {
      if (debug) console.warn('[SttWakeWord] KWS 启动失败，回退后端STT匹配:', e);
      kwsConsecFails += 1;
      maybeStartKwsFallbackPolling(`建连异常 ${String(e)}`);
    }
  }
  function closeKws() {
    if (kwsWs) {
      try { kwsWs.onopen = null; kwsWs.onmessage = null; kwsWs.onerror = null; kwsWs.onclose = null; kwsWs.close(); } catch (e) {}
      kwsWs = null;
    }
  }
  // ============ ASR WS（端侧 VAD+ASR 指令收集）============
  function openAsr() {
    if (!asrUrl) return;
    try {
      asrWs = new WebSocket(asrUrl);
      asrWs.binaryType = "arraybuffer";
      asrWs.onopen = () => { if (debug) console.log('[SttWakeWord] ASR WS 已连接'); };
      asrWs.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
          if (msg.type === 'vad') {
            // 连续对话窗口期内：用户直接说话（免唤醒词）→ 立即进入下一轮收集
            if (msg.event === 'speech_start' && state === WakeWordState.CONVERSING) {
              if (conversationWindowTimer) {
                clearTimeout(conversationWindowTimer);
                conversationWindowTimer = null;
              }
              onConversationWindowChange?.(false);
              // 用户直接说话（抢话/续说）：打断 AI 播报，但不播唤醒反馈
              onUserSpeechInDialog?.();
              state = WakeWordState.COLLECTING;
              startLiveDisplayForCollecting();
              resultSubmitted = false;
              textBuffer = "";
              updateTranscript("");
              acc = [];
              hasSpeech = false;
              inlineVad?.reset();
              resetCloudState();
              beginCloudStreamRound();
              if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
              if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
              updateStatus('我在听，请说');
              startMaxTimer();
              startSilenceTimer(8000);
            }
            if (debug) console.log('[SttWakeWord] ASR VAD:', msg.event, 'state=' + state);
          } else if (msg.type === 'asr') {
            // 反馈 TTS 播放期间丢弃所有 ASR 结果（双保险，防止回声被识别成指令）
            if (feedbackSpeaking) {
              if (debug && !msg.isPartial) console.log('[SttWakeWord] 反馈播放期间丢弃 ASR 结果:', msg.text);
            } else if (state === WakeWordState.COLLECTING) {
              if (msg.isPartial) {
                // 云端中间结果已出现过时，端侧 partial 不再覆盖输入框显示（云端优先显示）
                if (!cloudRound.partialSeen) updateTranscript(msg.text || '');
              } else {
                const text = (msg.text || '').trim();
                if (debug) console.log('[SttWakeWord] ASR final(端侧):', text);
                if (text) {
                  // 云端优先：端侧结果先暂存并立刻把音频送云端，由云端裁决；
                  // 云端超时/失败时 fallbackToOnDevice 会拿这里的结果收口。
                  pendingOnDeviceText = text;
                  updateTranscript(text);
                  if (shouldPreferCloud()) {
                    requestCloudStt('端侧ASR final');
                    if (cloudWaitTimer) {
                      // 云端结果在路上：把静音计时延长到等待窗口之外，
                      // 否则 3s 静音到点会拿空 textBuffer 抢先提交成"未检测到语音"
                      startSilenceTimer(CLOUD_WAIT_MS + 1500);
                    } else {
                      // 云端请求没发出（无音频可发 / 未启用云端）：直接用端侧结果收口
                      textBuffer = text;
                      pendingOnDeviceText = "";
                      sendResult();
                    }
                  } else {
                    textBuffer = text;
                    sendResult();
                  }
                }
              }
            } else if (state === WakeWordState.CONVERSING) {
              // 双保险：ASR 直接出 final 文本（vad speech_start 未触发场景），也当下一轮
              if (!msg.isPartial && (msg.text || '').trim()) {
                if (conversationWindowTimer) {
                  clearTimeout(conversationWindowTimer);
                  conversationWindowTimer = null;
                }
                onConversationWindowChange?.(false);
                textBuffer = (msg.text || '').trim();
                updateTranscript(textBuffer);
                state = WakeWordState.COLLECTING;
                sendResult(); // sendResult 会再次进入 CONVERSING（连续多轮）
              }
            }
          }
        } catch (e) { /* 忽略非 JSON */ }
      };
      asrWs.onerror = () => { if (debug) console.warn('[SttWakeWord] ASR WS 错误'); };
      asrWs.onclose = () => {
        if (debug) console.warn('[SttWakeWord] ASR WS 断开，1s 后重连');
        if (state !== WakeWordState.IDLE) {
          setTimeout(() => { if (state !== WakeWordState.IDLE && !asrWs) openAsr(); }, 1000);
        }
      };
    } catch (e) {
      if (debug) console.warn('[SttWakeWord] ASR 启动失败，回退后端STT:', e);
    }
  }
  function closeAsr() {
    if (asrWs) {
      try { asrWs.onopen = null; asrWs.onmessage = null; asrWs.onerror = null; asrWs.onclose = null; asrWs.close(); } catch (e) {}
      asrWs = null;
    }
  }
  function onKwsHit() {
    if (state === WakeWordState.IDLE) return;
    // 退出连续对话窗口（若处于窗口期，用户又显式喊了唤醒词）
    if (conversationWindowTimer) {
      clearTimeout(conversationWindowTimer);
      conversationWindowTimer = null;
    }
    onConversationWindowChange?.(false);
    // 始终触发唤醒反馈（onWakeWordTriggered 会停TTS=barge-in + 播"我在"）
    onWakeWordTriggered?.();
    if (state === WakeWordState.LISTENING) {
      if (debug) console.log('[SttWakeWord] KWS 唤醒，One-shot 直接进指令收集');
      updateStatus('检测到唤醒词，请说指令');
    } else {
      if (debug) console.log('[SttWakeWord] KWS 唤醒打断(barge-in)，重新听指令');
      updateStatus('唤醒打断，重新听指令');
      stopPolling();
    }
    // One-shot：不等"我在"播完，立即进 COLLECTING
    state = WakeWordState.COLLECTING;
    startLiveDisplayForCollecting();
    beginCloudStreamRound();
    textBuffer = "";
    updateTranscript("");
    acc = [];
    hasSpeech = false;
    resultSubmitted = false;
    inlineVad?.reset();
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
    if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
    startMaxTimer();
    startSilenceTimer(8000);
  }

  async function start(): Promise<void> {
    if (state !== WakeWordState.IDLE) return;

    state = WakeWordState.LISTENING;
    textBuffer = "";
    isRecording = true;
    acc = [];
    hasSpeech = false;

    updateStatus('唤醒词正在启动...');

    try {
      // 获取麦克风
      const audioConfig = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      };

      if (audioDeviceId) {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { ...audioConfig, deviceId: { exact: audioDeviceId } },
          video: false,
        });
      } else {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConfig,
          video: false,
        });
      }

      ctx = new AudioContext();
      // 浏览器自动播放策略会把非用户手势创建的 AudioContext 挂起（suspended），
      // 导致 onaudioprocess 不触发、音频不推送到端侧 KWS，唤醒词因此无反馈。需显式 resume。
      registerAudioContext(ctx);
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      source = ctx.createMediaStreamSource(stream);

      // @ts-ignore
      processor = ctx.createScriptProcessor(4096, 1, 1);

      // 创建内联 VAD（复用 processor 音频数据，不额外获取麦克风）
      inlineVad = createInlineVAD(60, 3, {
        onSpeechStart: () => {
          hasSpeech = true;
          if (debug) console.log('[SttWakeWord] VAD 检测到说话');
        },
        onSpeechEnd: () => {
          if (debug) console.log(`[SttWakeWord] VAD 说话结束, state=${state}`);
          // COLLECTING：端侧 ASR 若已出 final 则云端请求已发出；这里兜底——
          // 端侧不可用时（8768 连不上/无 final）仍要把音频送云端，避免整轮无识别结果。
          requestCloudStt('VAD onSpeechEnd');
          acc = [];
        },
      });

      // 音频处理：VAD 实时过滤静音段，只在有语音时累积音频
      let vadHangoverFrames = 0; // 说话结束后的缓冲帧数
      const VAD_HANGOVER = 8; // 说话结束后继续累积 ~180ms (8帧 × 4096/44100 ≈ 744ms... 实际约 180ms @ 48kHz)

      processor.onaudioprocess = (e) => {
        if (!isRecording) return;
        const input = e.inputBuffer.getChannelData(0);

        // VAD 处理
        inlineVad?.processFrame(input);

        // KWS 推流：始终送（支持 barge-in，AI说话/指令收集时也能唤醒打断）
        // 唤醒反馈 TTS 播放期间暂停推流，防止"你好我在"回声误触发唤醒词
        if (!feedbackSpeaking && kwsWs && kwsWs.readyState === WebSocket.OPEN) {
          try {
            const ds = downsampleBuffer(input, ctx!.sampleRate, targetSampleRate);
            const pcm16 = floatTo16BitPCM(ds);
            kwsWs.send(pcm16.buffer);
          } catch (e) { /* 忽略单帧错误 */ }
        }
        // ASR 推流：COLLECTING / CONVERSING 时送端侧 ASR(8768)。
        // CONVERSING（连续对话窗口期）持续推流，用户直接说话即可进入下一轮，无需唤醒词
        // 唤醒反馈 TTS 播放期间不推流，防止"你好我在"回声被识别成指令（自循环）
        // AI 播报期间经回声门控抑制疑似回声帧，防止 TTS 声音被设备 ASR 识别成指令而打断
        // 门控每帧只判一次，端侧与云端共用（重复调用会推进基线/冷却状态）
        if (!feedbackSpeaking && (state === WakeWordState.COLLECTING || state === WakeWordState.CONVERSING)) {
          if (echoGate.gateFrame(input)) {
            const dsShared = downsampleBuffer(input, ctx!.sampleRate, targetSampleRate);
            const pcm16Shared = floatTo16BitPCM(dsShared);
            if (asrWs && asrWs.readyState === WebSocket.OPEN) {
              try {
                asrWs.send(pcm16Shared.buffer);
              } catch (e) { /* 忽略单帧错误 */ }
            }
            // 云端流式：同一帧复用，仅收集期上行（CONVERSING 窗口期不开会话）
            if (cloudRound.active && state === WakeWordState.COLLECTING) {
              try {
                cloudStream?.push(toBase64(new Uint8Array(pcm16Shared.buffer)));
              } catch (e) { /* 忽略单帧错误 */ }
            }
          }
        }

        // 云端专用缓冲：收集期内无条件累积，保证端侧出结果时一定有音频可发云端
        // （前端 inlineVad 与设备端 VAD 判定不同步，光靠 acc 会出现"有识别结果但无音频"）
        if (state === WakeWordState.COLLECTING || state === WakeWordState.CONVERSING) {
          if (cloudAcc.length < CLOUD_ACC_MAX_FRAMES) {
            cloudAcc.push(new Float32Array(input));
          }
        }

        // 只在有语音（或 hangover 期间）时累积音频
        if (inlineVad?.isSpeaking()) {
          acc.push(new Float32Array(input));
          vadHangoverFrames = VAD_HANGOVER;
        } else if (vadHangoverFrames > 0) {
          // 说话刚结束，继续累积尾部音频（避免截断）
          acc.push(new Float32Array(input));
          vadHangoverFrames--;
        } else {
          // 静音段：不累积，减少传输量
          // acc 中的数据由 polling 或 onSpeechEnd 发送后清空
        }
      };

      source.connect(processor);
      processor.connect(ctx.destination);

      // 启动 KWS WS（端侧唤醒词检测，纯 KWS 模式）
      openKws();
      // 启动 ASR WS（端侧 VAD+ASR 指令收集，替代后端 STT）
      openAsr();

      // 仅当未配 KWS 时回退后端 STT 轮询（默认走 KWS，不轮询，省调用+避免误识别）
      if (!kwsUrl) {
        startPolling();
      }

      updateStatus('唤醒词监听已启动');
      if (debug) console.log('[SttWakeWord] 唤醒词监听已启动');
    } catch (e) {
      updateStatus('唤醒词启动失败');
      onError?.('start-failed', e instanceof Error ? e.message : String(e));
      console.error('[SttWakeWord] 启动失败:', e);
      state = WakeWordState.IDLE;
      throw e;
    }
  }

  function stop(): void {
    state = WakeWordState.IDLE;
    isRecording = false;
    stopPolling();
    closeKws();
    closeAsr();
    kwsConsecFails = 0;
    kwsFallbackPolling = false;
    liveDisplay?.stop();
    liveDisplay = null;

    updateTranscript("");

    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    if (maxTimer) {
      clearTimeout(maxTimer);
      maxTimer = null;
    }
    if (conversationWindowTimer) {
      clearTimeout(conversationWindowTimer);
      conversationWindowTimer = null;
    }

    // 云端等待窗口随监听停止一并清理，避免收尾后仍有定时器触发回退
    resetCloudState();

    // 重置内联 VAD
    inlineVad?.reset();
    inlineVad = null;

    // 断开音频
    processor?.disconnect();
    source?.disconnect();
    ctx?.close();
    stream?.getTracks().forEach((t) => t.stop());

    processor = null;
    source = null;
    ctx = null;
    stream = null;
    acc = [];
    hasSpeech = false;

    onConversationWindowChange?.(false);
    updateStatus('唤醒词监听已停止');
    if (debug) console.log('[SttWakeWord] 唤醒词监听已停止');
  }

  function destroy(): void {
    if (debug) console.log('[SttWakeWord] 销毁唤醒词监听器');
    stop();
    state = WakeWordState.IDLE;
    textBuffer = "";
    resetCloudState();
  }

  function startCollecting(): void {
    if (state === WakeWordState.AWAITING_FEEDBACK) {
      if (debug) console.log('[SttWakeWord] 反馈播放完成，开始收集用户语音');
      state = WakeWordState.COLLECTING;
      // 清空之前的音频缓冲和 VAD 状态，从全新状态开始录音
      acc = [];
      hasSpeech = false;
      inlineVad?.reset();
      resetCloudState();
      beginCloudStreamRound();
      updateStatus('请开始说话');
      startMaxTimer();
      startLiveDisplayForCollecting();
      // COLLECTING 不使用轮询，由 VAD onSpeechEnd 触发发送
      if (textBuffer.trim()) {
        // 唤醒词后面已经有内容（如"小云小云今天天气怎么样"），用较短的等待
        if (debug) console.log('[SttWakeWord] 已有预填内容:', textBuffer);
        startSilenceTimer(3000);
      } else {
        // 纯唤醒词，等待用户开始说话
        startSilenceTimer(8000);
      }
    }
  }

  return { start, stop, destroy, startCollecting, handleSttResult, notifyAiState, setFeedbackSpeaking };
}

/**
 * 基于后端 STT 的持续语音识别
 */
export interface SttContinuousConfig {
  onResult: (text: string) => void;
  onStart?: () => void;
  onEnd?: () => void;
  /** 发送音频到后端进行识别, sttOnly=true 时仅做 STT 不自动提交 LLM */
  sendAudioForStt: (pcm16_b64: string, isLast: boolean, sttOnly?: boolean) => void;
  /** 端侧 ASR WS 地址（默认 DEFAULT_ASR_URL）。提供后持续识别优先走设备内流式 ASR（快、低延迟） */
  asrUrl?: string;
  /**
   * 端侧 ASR 连不上时是否自动回退后端云端 STT（默认 true）。
   * 判定口径：整个会话内从未成功连上端侧 ASR（含连接超时/重连仍失败）→ 回退；
   * 一旦连上过就不再降级，只重连。置 false 可强制"纯端侧"。
   */
  allowCloudFallback?: boolean;
  /**
   * 云端优先（与唤醒词模式同一开关）：开启后持续识别不连端侧 ASR，直接走云端 STT
   * （本地 VAD 断句 + 云端流式，说话期间实时 partial）；云端连续失败自动回退端侧
   * （既有反向兜底，整场一次）。关闭则端侧优先（低延迟），端侧不在场自动回退云端。
   * 会话开始时取值一次——切换开关后重开一次持续识别生效。默认 false。
   */
  preferCloud?: boolean | (() => boolean);
  /**
   * 读取输入框中的浏览器实时转写文本（仅当框内为转写文本时返回，否则空串）。
   * 云端回"未识别"类失败文案时兜底：采集设备哑掉/选错时后端听到的全是静音，而浏览器
   * 实时识别走系统默认麦克风往往仍能出字——用它提交，保证"有文字发不出"时聊天不断。
   */
  getLiveSttFallbackText?: () => string;
  /** 仅显示用的浏览器实时转写回调（后端整段识别无中间结果，由浏览器并行识别补实时上屏） */
  onLiveDisplayText?: (text: string) => void;
  /**
   * 云端流式会话通道（云端回退路径时启用）：本地 VAD 判定说话开始 begin()，说话期间逐帧
   * push（16k PCM16 base64），静音收口 end()。云端 partial 说话期间即回（输入框实时显示，
   * 优先于浏览器并行识别），收口直接出 stt_final，省去整段重传。与 SttWakeWordConfig.cloudStream
   * 同构、共用同一组上行回调（唤醒词/持续识别互斥，不会并发开两路会话）。
   */
  cloudStream?: {
    begin: () => void;
    push: (pcm16_b64: string) => void;
    end: () => void;
  };
  audioDeviceId?: string;
  silenceSeconds?: number;
  debug?: boolean;
  /** 通话模式：开启持续识别即"接听"，进 call session；长静默(默认40s)或显式挂断/退出才结束（半双工通话感）。默认 true */
  callMode?: boolean;
  /** 通话模式长静默超时（毫秒）：超过该时长无语音则自动挂断。默认 40000 */
  callSilenceTimeoutMs?: number;
  /** 挂断指令词（客户端拦截，不发给 LLM）。默认包含 挂断/挂掉/结束通话/退出通话/结束对话/退出对话 */
  hangupPhrases?: string[];
  /** 通话状态变化回调（true=接听/通话中，false=挂断/结束）。reason: 'answer'|'voice'|'silence'|'external'|'stop' */
  onCallStateChange?: (active: boolean, reason?: string) => void;
  /** 接通瞬间回调（用于播放接听提示音） */
  onCallStarted?: () => void;
  /** 挂断瞬间回调（用于播放挂断提示音），reason 同 onCallStateChange */
  onCallEnded?: (reason: string) => void;
  /** 用户开始说话（设备 ASR VAD speech_start）回调：用于 barge-in 随时打断 AI 播报 */
  onUserSpeechInDialog?: () => void;
}

export interface SttContinuousController {
  start: () => Promise<void>;
  stop: () => void;
  pause: () => void;
  resume: () => Promise<void>;
  /** 处理后端返回的 STT 识别结果 */
  handleSttResult: (text: string, isPartial: boolean) => void;
  /** 挂断/结束通话（外部触发，如 HUD 按钮或眼镜触摸手势） */
  hangupCall: (reason?: string) => void;
  /** 当前是否处于通话中 */
  isCallActive: () => boolean;
  /** 通知 AI 播报状态（true=正在播报，用于回声抑制门控） */
  notifyAiState: (speaking: boolean) => void;
}

export function createSttContinuousListener(config: SttContinuousConfig): SttContinuousController {
  const {
    onResult,
    onStart,
    // onEnd is reserved for future use
    sendAudioForStt,
    asrUrl = DEFAULT_ASR_URL,
    allowCloudFallback = true,
    preferCloud = false,
    audioDeviceId,
    silenceSeconds = 3,
    debug = false,
    callMode = true,
    callSilenceTimeoutMs = 40000,
    hangupPhrases = ["挂断", "挂掉", "结束通话", "退出通话", "结束对话", "退出对话"],
    onCallStateChange,
    onCallStarted,
    onCallEnded,
    onUserSpeechInDialog,
    onLiveDisplayText,
    cloudStream,
    getLiveSttFallbackText,
  } = config;

  let isListening = false;
  let isPaused = false;
  // 浏览器并行识别会话（仅用于输入框实时显示，不参与消息发送）
  let liveDisplay: SttLiveDisplayHandle | null = null;

  // 通话模式相关（持续识别=通话入口：点"持续识别"=接听，stop/说挂断词/长静默=挂断）
  let callActive = false;
  let callSilenceTimer: ReturnType<typeof setTimeout> | null = null;

  /** 长静默计时：超过 callSilenceTimeoutMs 无语音则自动挂断 */
  function startCallSilenceTimer() {
    if (callSilenceTimer) clearTimeout(callSilenceTimer);
    callSilenceTimer = setTimeout(() => {
      callSilenceTimer = null;
      if (callActive) {
        hangupCall('silence');
      }
    }, callSilenceTimeoutMs);
  }

  /** 挂断/结束通话：清计时、播挂断音、通知 UI */
  function hangupCall(reason: string = 'external') {
    if (!callActive) return;
    callActive = false;
    if (callSilenceTimer) {
      clearTimeout(callSilenceTimer);
      callSilenceTimer = null;
    }
    onCallStateChange?.(false, reason);
    onCallEnded?.(reason);
    try { playHangupTone(); } catch (e) { console.warn('[持续识别通话] 挂断提示音播放失败', e); }
  }

  function isCallActive() {
    return callActive;
  }

  // Audio
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;
  let acc: Float32Array[] = [];
  const targetSampleRate = 16000;

  // 端侧 ASR（设备内流式识别，低延迟，替代后端云端 STT）
  // ⚠️ 运行时可变：端侧设备不在场时热切到后端云端 STT（见 fallbackToCloudStt）。
  // 原先这里是 const，导致「没插香橙派就彻底没有识别」——PCM 只推给端侧 WS，
  // 从不调 sendAudioForStt，云端 STT 完全没机会接上。
  let onDeviceAsr = !!asrUrl;
  let asrWs: WebSocket | null = null;
  // 云端优先开关（与唤醒词模式同一开关）：支持传函数实时取值；会话开始时评估一次
  const shouldPreferCloud = (): boolean =>
    typeof preferCloud === "function" ? !!preferCloud() : !!preferCloud;
  // 端侧 ASR 可用性探测：整个会话内「从未连上过」才判定设备不在场并降级；
  // 一旦连上过（演示时 Pi 在跑），后续断线只按原逻辑重连、不再中途换链路。
  let asrEverOpened = false;
  let asrFailStreak = 0;
  let asrConnectTimer: ReturnType<typeof setTimeout> | null = null;
  let cloudFallbackDone = false;
  const ASR_CONNECT_TIMEOUT_MS = 1500; // 局域网 WS：通就是几十 ms，1.5s 足够判定不可达（用户手填 IP 但 Pi 不在场时快败）
  const ASR_FALLBACK_ATTEMPTS = 2;
  // 反向兜底（云端失效 → 切回端侧）：云端路径下收到后端失败文案即计数，
  // 连续 2 次判定云端不可用。为防止"两边都不稳"时来回抖动，整场会话只允许反向切一次。
  let cloudFailStreak = 0;
  let reverseSwitches = 0;
  const CLOUD_FAIL_STREAK_LIMIT = 2;

  // ===== 云端流式会话状态（云端回退路径：说话期间逐帧上行，partial 实时回输入框）=====
  // 已抽取为 ./voice/cloud-round.ts 的 CloudStreamRound（与唤醒词路径共用同一实现）
  const cloudRound = new CloudStreamRound({ tag: '[SttContinuous]', debug });

  // ===== 静默兜底轮（云端路径：VAD 阈值 60 对安静麦克风可能永远不触发 → 永不上传）=====
  // 与唤醒词收集期的 cloudAcc 同思路：音频无条件缓冲（有上限），VAD 只做加速；
  // callActive 期间若长时间无 VAD 活动，把缓冲整段上传兜底——保证"说话最多
  // IDLE_ROUND_MS 必出转写"，不依赖前端 VAD 灵敏度。
  let cloudAcc: Float32Array[] = [];
  const CLOUD_ACC_MAX_FRAMES = 150; // ~12s @48kHz/4096，防内存失控
  let idleRoundTimer: ReturnType<typeof setTimeout> | null = null;
  const IDLE_ROUND_MS = 8000;
  let silentRoundCount = 0; // 连续全静音兜底窗口数（设备哑时上传必得 EmptyAudio，暂停上传省配额）
  // 麦克风电平诊断日志节流（窗口内取最大值，避免单帧采样漏掉说话峰值）
  let micLevelLogAt = 0;
  let micLevelWindowMax = 0;

  // ===== 采集静音降级模式（选错/哑麦克风时保住聊天闭环）=====
  // 采集（getUserMedia，走页面选中设备）持续无声、而浏览器实时识别（走系统默认麦）
  // 持续出字 → 判定采集静音：停掉静音兜底轮（上传全 EmptyAudio 毫无意义），改为
  // 监视浏览器识别文本——静默 3s 无变化即自动提交。采集恢复（出现≥阈值帧）自动退出。
  let captureSilent = false;
  let loudSeenAt = 0;          // 最近一次"有能量的帧"（≥24）
  let srTextSeenAt = 0;        // 最近一次浏览器识别出字
  let srLastText = "";         // 浏览器识别当前文本
  let srLastChangeAt = 0;      // 文本最近一次变化时间
  let srLastSubmitted = "";    // 最近一次已提交的浏览器识别文本（防重发刷屏）
  let degradedWatchdog: ReturnType<typeof setInterval> | null = null;

  function startDegradedWatchdog() {
    if (degradedWatchdog) return;
    degradedWatchdog = setInterval(() => {
      if (!isListening || isPaused || onDeviceAsr || !callActive) return;
      const now = Date.now();
      if (!captureSilent) {
        // 进入条件：15s 内没有任何有能量的帧（loudSeenAt=0 即开场就哑也成立），
        // 而浏览器识别 8s 内还在出字
        if (now - loudSeenAt > 15000 && srTextSeenAt > 0 && now - srTextSeenAt < 8000) {
          captureSilent = true;
          console.warn('[SttContinuous] 采集持续静音但浏览器识别正常 → 进入降级模式：'
            + '浏览器识别文本静默 3s 自动提交（请到设置更换麦克风设备以恢复流式）');
          // 降级模式靠浏览器识别出字：确保识别会话活着（VAD 已无法再触发启动）
          if (!liveDisplay) {
            liveDisplay = startSttLiveDisplay((t) => { if (!cloudRound.partialSeen) (onLiveDisplayText ?? (() => {}))(t); });
          }
        }
        return;
      }
      // 降级模式：浏览器识别文本静默 3s 且与上次已提交不同 → 提交
      if (srLastText && srLastText !== srLastSubmitted
        && now - srLastChangeAt >= 3000 && now - srTextSeenAt < 15000) {
        srLastSubmitted = srLastText;
        console.log('[SttContinuous] 降级模式：提交浏览器识别文本:', srLastText);
        onResult(srLastText);
      }
    }, 1000);
  }

  function stopDegradedWatchdog() {
    if (degradedWatchdog) { clearInterval(degradedWatchdog); degradedWatchdog = null; }
  }

  function clearIdleRoundTimer() {
    if (idleRoundTimer) { clearTimeout(idleRoundTimer); idleRoundTimer = null; }
  }

  /** VAD 迟迟不触发时的兜底：把无条件缓冲的音频整段上传（sttOnly，final 由前端提交） */
  function idleRoundFlush() {
    idleRoundTimer = null;
    if (!isListening || isPaused || onDeviceAsr) return;
    if (captureSilent) {
      // 采集静音降级模式：上传只会得到 EmptyAudio，交给浏览器识别文本自动提交
      cloudAcc = [];
      resetIdleRoundTimer();
      return;
    }
    if (cloudRound.active) {
      // 流式轮进行中（VAD 正常工作）：本次兜底不需要，重排等下一轮再判
      resetIdleRoundTimer();
      return;
    }
    const frames = cloudAcc;
    cloudAcc = [];
    const total = frames.reduce((n, a) => n + a.length, 0);
    if (ctx && total >= ctx.sampleRate * 0.7) { // 少于 0.7s 纯噪声不上传
      const buf = new Float32Array(total);
      let off = 0;
      for (const a of frames) { buf.set(a, off); off += a.length; }
      // 全窗口电平贴 0（沿用诊断日志"接近静音"阈值 8）＝设备哑/选错，上传必得
      // EmptyAudio：连续 2 个静音窗口后暂停兜底上传，任何帧电平恢复即重置续传
      let peak = 0;
      for (const a of frames) {
        const v = calculateRmsVolume(a);
        if (v > peak) peak = v;
        if (peak > 8) break;
      }
      silentRoundCount = peak > 8 ? 0 : silentRoundCount + 1;
      if (silentRoundCount >= 2) {
        if (debug) console.log('[SttContinuous] 连续全静音窗口，暂停兜底上传（防 EmptyAudio 空烧配额），采集恢复自动续传');
        resetIdleRoundTimer();
        return;
      }
      const ds = downsampleBuffer(buf, ctx.sampleRate, targetSampleRate);
      const pcm16 = floatTo16BitPCM(ds);
      if (debug) console.log(`[SttContinuous] 静默兜底轮：VAD 未触发，整段上传缓冲 ${Math.round(pcm16.length / targetSampleRate * 1000)}ms`);
      sendAudioForStt(toBase64(new Uint8Array(pcm16.buffer)), true, true);
    }
    resetIdleRoundTimer();
  }

  function resetIdleRoundTimer() {
    clearIdleRoundTimer();
    if (!isListening || isPaused || onDeviceAsr || !callActive) return;
    idleRoundTimer = setTimeout(idleRoundFlush, IDLE_ROUND_MS);
  }

  // VAD 判定说话开始时开启新一轮云端流式（begin 只复位前端序号，首帧到达时后端才建会话）
  function beginCloudStreamRound() {
    if (!cloudStream) { cloudRound.reset(() => {}); return; }
    cloudRound.begin(() => cloudStream!.begin());
  }

  /** 复位云端流式轮次（停止/链路切换时调用；未收口的会话由后端空闲看门狗回收） */
  function resetCloudStreamRound() {
    cloudRound.reset(() => {});
  }

  // 回声抑制门控：AI 播报期间抑制疑似回声推流，避免误识别/自打断
  const echoGate = createEchoGate({
    onHumanSpeechGate: () => {
      if (debug) console.log('[SttContinuous] 回声门控判定真人语音，触发打断');
      onUserSpeechInDialog?.();
    },
  });

  // VAD 阈值（仅后端云端 STT 回退路径使用）：max(30, min(底噪,20)+6)。
  // 底噪封顶 20 → 自适应项上限 26，永远够不到 30，实际等价固定阈值 30。
  // （2026-09-18 曾因固定 60 对安静麦克风不触发改为自适应；2026-10-13 用户将下限 18→50→30：
  // 50 高于现场实测说话电平 17~43，VAD 不触发→流式链路不启动→全靠 8s 兜底轮整段上传。）
  const adaptiveVadThreshold = () =>
    Math.max(30, Math.min(Math.round(echoGate.getNoiseFloor()), 20) + 6);

  // VAD（仅后端云端 STT 回退路径使用）
  let inlineVad: ReturnType<typeof createInlineVAD> | null = null;
  let isSpeaking = false;  // VAD 检测到正在说话

  // 发送累积音频
  function flushAndSend() {
    liveDisplay?.stop();
    liveDisplay = null;
    // 本轮已用云端流式逐帧上行：只需收口出 stt_final（音频已在说话期间到达后端），
    // 不再整段重传——同一段音频两条链路都发会导致同句两次识别/两次提交。
    if (cloudRound.active) {
      cloudRound.endOnce(() => cloudStream?.end());
      cloudRound.active = false;
      cloudAcc = []; // 流式已覆盖本轮音频，静默兜底缓冲作废（防同句重复上传）
      resetIdleRoundTimer(); // 流式轮结束：重新武装静默兜底（下一轮 VAD 再不触发时兜底）
      if (debug) console.log('[SttContinuous] 云端流式收口，等待 stt_final');
      return;
    }
    if (!ctx || acc.length === 0) return;

    const total = acc.reduce((n, a) => n + a.length, 0);
    const buf = new Float32Array(total);
    let off = 0;
    for (const a of acc) {
      buf.set(a, off);
      off += a.length;
    }
    acc = [];

    const ds = downsampleBuffer(buf, ctx.sampleRate, targetSampleRate);
    const pcm16 = floatTo16BitPCM(ds);
    const b64 = toBase64(new Uint8Array(pcm16.buffer));

    if (debug) console.log(`[SttContinuous] 发送音频: ${pcm16.length} samples, ${Math.round(pcm16.length / targetSampleRate * 1000)}ms`);
    // 持续识别：每次发送的也是独立完整录音段。sttOnly=true——识别文本回前端后统一由
    // onResult 上屏+提交（与端侧路径同一条出口），挂断词拦截/去重才真正生效；
    // 若走 stt_only=false 由后端自动提交，用户说的话在界面上无任何转写显示，
    // 且"挂断"等指令词会先被后端当普通提问发给 LLM。
    sendAudioForStt(b64, true, true);
  }

  // 处理后端返回的 STT 结果
  function handleSttResult(text: string, isPartial: boolean) {
    if (!text.trim()) return;

    const simplified = toSimplifiedChinese(text.trim());

    // 云端中间结果（云端回退路径）：说话期间实时上屏。首个有效云端 partial 到达后
    // 停掉浏览器并行识别会话（云端优先显示，避免两路文本在输入框互相覆盖）。
    // "正在识别..."是状态占位不是识别内容，不得当作云端文本。
    if (isPartial) {
      if (!onDeviceAsr && !looksLikeCloudFailure(simplified) && !simplified.includes('正在识别')) {
        if (!cloudRound.partialSeen && debug) console.log('[SttContinuous] 云端 partial 接管实时显示:', simplified);
        cloudRound.partialSeen = true;
        liveDisplay?.stop();
        liveDisplay = null;
      }
      return;
    }

    const cleaned = simplified.replace(/^。|。$/g, '').replace(/([\u4e00-\u9fa5])。/g, '$1').trim();
    if (debug) console.log(`[SttContinuous] 收到识别结果: "${simplified}" -> 清理后: "${cleaned}"`);

    // 通话模式：挂断指令在客户端拦截。**必须在两类分支之前判**——若识别文本由后端
    // 直接提交 LLM，这里再拦就晚了；现在云端路径也是 stt_only=true 由前端提交，拦截真正生效。
    if (cleaned && callActive && hangupPhrases.some(p => cleaned.includes(p))) {
      console.log('[SttContinuous] 检测到挂断指令，结束通话');
      hangupCall('voice');
      return;
    }

    // 云端路径：识别结果统一走 onResult（上屏用户消息 + 提交后端 LLM，与端侧路径同出口）。
    // 失败文案（后端识别异常时的占位文本）只当链路健康信号：连续 2 次 → 切回端侧。
    // 同时重置通话长静默计时：云端路径没有设备 VAD 事件，不重置会让通话每 40s 被误挂断。
    if (!onDeviceAsr) {
      startCallSilenceTimer();
      if (!cleaned || looksLikeCloudFailure(simplified)) {
        // 兜底：采集设备哑掉/选错时后端听到的全是静音（EmptyAudio），而浏览器实时
        // 识别走系统默认麦克风往往仍能出字——把输入框里的转写文本提交，聊天不断线。
        const liveText = (getLiveSttFallbackText?.() || "").trim();
        if (liveText) {
          liveDisplay?.stop();
          liveDisplay = null;
          console.log('[SttContinuous] 云端未识别（采集静音？），用浏览器转写兜底提交:', liveText);
          onResult(liveText);
          return;
        }
        noteCloudFailure(simplified || '(空)');
        return;
      }
      cloudFailStreak = 0;
      onResult(cleaned);
      return;
    }

    if (cleaned) {
      if (debug) console.log('[SttContinuous] 识别内容:', cleaned);
      onResult(cleaned);
    }
  }

  // ============ 音频管线绑定（端侧 ASR / 云端 STT 可热切换）============
  /**
   * 按当前链路绑定 audio 处理管线。降级时重新调用即可热切换，无需重开麦克风。
   *  - 端侧 ASR：逐帧直推设备（设备内 VAD + 流式识别）
   *  - 云端 STT：本地内联 VAD 断句，说完整段上传后端
   */
  function bindAudioPipeline() {
    if (!processor || !ctx) return;
    if (onDeviceAsr) {
      inlineVad = null;
      isSpeaking = false;
      processor.onaudioprocess = (e) => {
        if (!isListening || isPaused) return;
        const input = e.inputBuffer.getChannelData(0);
        // 回声门控：AI 播报期间，疑似回声帧抑制推流（真人语音放行）
        if (!echoGate.gateFrame(input)) return;
        if (asrWs && asrWs.readyState === WebSocket.OPEN) {
          try {
            const ds = downsampleBuffer(input, ctx!.sampleRate, targetSampleRate);
            const pcm16 = floatTo16BitPCM(ds);
            asrWs.send(pcm16.buffer);
          } catch (err) { /* 忽略单帧错误 */ }
        }
      };
    } else {
      // 后端云端 STT 回退路径：前端本地 VAD 检测说话边界，说话期间逐帧上行云端流式
      // （partial 实时回输入框），静默后收口出 final；流式通道不可用则整段上传。
      // VAD 阈值：max(30, min(底噪,20)+6)——自适应项上限 26 够不到 30，实际等价固定 30
      // （2026-09-18 曾因固定 60 对安静麦克风不触发改为自适应；2026-10-13 用户将下限 18→50→30：
      // 50 高于现场实测说话电平 17~43，VAD 不触发→流式链路不启动→全靠 8s 兜底轮整段上传。）
      // 底噪沿用回声门控的基线（上限 20 封顶），防止说话把阈值越推越高。
      inlineVad = createInlineVAD(
        adaptiveVadThreshold,
        silenceSeconds,
        {
          onSpeechStart: () => {
            isSpeaking = true;
            // 清空说话之前的噪音缓冲，只保留说话开始后的音频
            acc = [];
            // 开新一轮云端流式（有通道时）：复位序号/云端优先标志，首帧到达时后端建会话
            beginCloudStreamRound();
            // 说话即活跃：重置通话长静默计时，防长句期间被 40s 无语音误挂断
            startCallSilenceTimer();
            // VAD 正常触发：推迟静默兜底轮（本轮由流式路径负责）
            resetIdleRoundTimer();
            // 浏览器并行识别仅用于输入框实时显示（云端 partial 到达后由 cloudRound.partialSeen 门控让位）；
            // 同时记录 SR 活动/文本变化时间戳——采集静音降级模式据此自动提交
            liveDisplay?.stop();
            liveDisplay = startSttLiveDisplay((t) => {
              srTextSeenAt = Date.now();
              if (t !== srLastText) { srLastText = t; srLastChangeAt = Date.now(); }
              if (!cloudRound.partialSeen) (onLiveDisplayText ?? (() => {}))(t);
            });
            if (debug) console.log('[SttContinuous] 检测到说话，清空噪音缓冲');
          },
          onSpeechEnd: () => {
            isSpeaking = false;
            if (debug) console.log('[SttContinuous] 说话结束，发送音频识别');
            flushAndSend();
          },
        }
      );

      processor.onaudioprocess = (e) => {
        if (!isListening || isPaused) return;
        const input = e.inputBuffer.getChannelData(0);
        // 回声门控：AI 播报期间，疑似回声帧抑制累积/推流（真人语音能量跃升放行，
        // 并经 onHumanSpeechGate 触发 barge-in——与端侧路径同款）
        if (!echoGate.gateFrame(input)) return;
        const frameVolume = calculateRmsVolume(input);
        if (frameVolume > micLevelWindowMax) micLevelWindowMax = frameVolume;
        // 有能量的帧：刷新"采集活着"时间戳；降级模式下出现即恢复正常流式
        if (frameVolume >= 24) {
          loudSeenAt = Date.now();
          if (captureSilent) {
            captureSilent = false;
            srLastSubmitted = "";
            console.log('[SttContinuous] 检测到采集电平恢复 → 退出降级模式，回到云端流式');
          }
        }
        // 麦克风电平诊断（3s 节流，取窗口内最大值——单帧采样会把说话峰谷漏成 0）
        const nowMs = Date.now();
        if (nowMs - micLevelLogAt >= 3000) {
          micLevelLogAt = nowMs;
          const maxLevel = micLevelWindowMax;
          micLevelWindowMax = 0;
          const th = adaptiveVadThreshold();
          console.log(
            `[SttContinuous] 麦克风电平(3s最大): ${maxLevel}/100（VAD阈值≈${th}）` +
            (maxLevel < 8 ? " ⚠️ 接近静音：请到设置里更换麦克风设备（当前选中的设备可能不对/已哑）" : "")
          );
        }
        // 无条件缓冲（静默兜底轮用）：VAD 阈值（现 30）对安静麦克风可能永远不触发，
        // 兜底轮靠这份缓冲保证"说话最多 8s 必上传"
        if (cloudAcc.length < CLOUD_ACC_MAX_FRAMES) cloudAcc.push(new Float32Array(input));
        // 只在说话期间累积音频，静音/噪音不累积
        if (isSpeaking) {
          acc.push(new Float32Array(input));
          // 云端流式：说话期间逐帧上行（16k PCM16 base64），后端实时回 partial
          if (cloudStream && cloudRound.active && !cloudRound.ended && ctx) {
            try {
              const ds = downsampleBuffer(input, ctx.sampleRate, targetSampleRate);
              const pcm16 = floatTo16BitPCM(ds);
              cloudStream.push(toBase64(new Uint8Array(pcm16.buffer)));
            } catch (err) { /* 忽略单帧错误 */ }
          }
        }
        inlineVad?.processFrame(input);
      };
    }
  }

  /** 端侧 ASR 不可用 → 热切到后端云端 STT（本地 VAD 断句 + 整段上传） */
  function fallbackToCloudStt(reason: string) {
    if (!onDeviceAsr || !allowCloudFallback || cloudFallbackDone) return;
    cloudFallbackDone = true;
    onDeviceAsr = false;
    cloudFailStreak = 0;
    resetCloudStreamRound();
    closeAsr();
    bindAudioPipeline();
    resetIdleRoundTimer();
    console.warn(`[SttContinuous] 端侧 ASR 不可用（${reason}）→ 已自动切换到后端云端 STT`);
  }

  /** 记一次端侧 ASR 连接失败；从未连上过且失败达阈值即降级 */
  function noteAsrFailure(reason: string) {
    asrFailStreak += 1;
    if (!asrEverOpened && asrFailStreak >= ASR_FALLBACK_ATTEMPTS) {
      fallbackToCloudStt(`${reason}，连续 ${asrFailStreak} 次未连上 ${asrUrl}`);
    }
  }

  /** 云端失效 → 切回端侧 ASR（反向兜底，整场会话只允许切一次，避免两边抖动） */
  function fallbackToOnDevice(reason: string) {
    if (onDeviceAsr) return; // 已经在端侧
    if (!asrUrl) return; // 没配端侧地址，无从切回
    if (reverseSwitches >= 1) {
      console.warn('[SttContinuous] 端侧与云端都已失败过，保持当前链路，不再来回切换');
      return;
    }
    reverseSwitches += 1;
    cloudFallbackDone = false; // 允许端侧再次不可用时重新降级回云端
    onDeviceAsr = true;
    asrEverOpened = false; // 重新探测端侧可用性
    asrFailStreak = 0;
    cloudFailStreak = 0;
    clearIdleRoundTimer();
    cloudAcc = [];
    resetCloudStreamRound();
    bindAudioPipeline();
    console.log('[SttContinuous] 切回端侧 ASR，重新建立连接', asrUrl);
    openAsr();
    console.warn(`[SttContinuous] 云端 STT 失效（${reason}）→ 已切回端侧 ASR`);
  }

  /** 云端路径下收到后端的失败文案：连续 2 次即认为云端不可用 */
  function noteCloudFailure(text: string) {
    cloudFailStreak += 1;
    console.warn(`[SttContinuous] 云端 STT 失败文案（第 ${cloudFailStreak} 次）: ${text}`);
    if (cloudFailStreak >= CLOUD_FAIL_STREAK_LIMIT) {
      fallbackToOnDevice(`连续 ${cloudFailStreak} 次返回失败文案`);
    }
  }

  // ============ 端侧 ASR WS（快、流式，替代后端云端 STT）============
  function openAsr() {
    if (!onDeviceAsr || !asrUrl) return;
    // 幂等保护：已有活连接/正在连接时不重建。resume/barge-in 会在 AI 状态翻转时反复调 openAsr，
    // 无条件重建会把正在说话的推流掐断——识别 final 随连接丢失（表现为输入框只剩碎片、不自动发送）
    if (asrWs && (asrWs.readyState === WebSocket.OPEN || asrWs.readyState === WebSocket.CONNECTING)) {
      if (debug) console.log('[SttContinuous] ASR WS 已在推流中，保持现有连接');
      return;
    }
    closeAsr(); // 避免重复连接（resume 时旧 WS 可能仍开着）
    try {
      asrWs = new WebSocket(asrUrl);
      asrWs.binaryType = "arraybuffer";
      asrWs.onopen = () => {
        asrEverOpened = true;
        asrFailStreak = 0;
        if (asrConnectTimer) { clearTimeout(asrConnectTimer); asrConnectTimer = null; }
        console.log('[SttContinuous] 端侧 ASR 已连接', asrUrl);
      };
      // 连接超时守护：地址不可达时部分浏览器长时间不发 onerror/onclose，
      // 否则「没插设备」会表现为"一直转圈但永不报错"。超时即判失败，交给降级逻辑。
      if (asrConnectTimer) clearTimeout(asrConnectTimer);
      asrConnectTimer = setTimeout(() => {
        asrConnectTimer = null;
        if (asrEverOpened || !isListening) return;
        if (asrWs && asrWs.readyState === WebSocket.OPEN) return;
        console.warn(`[SttContinuous] 端侧 ASR 连接超时（${ASR_CONNECT_TIMEOUT_MS}ms）: ${asrUrl}`);
        closeAsr();
        noteAsrFailure('连接超时');
        if (!cloudFallbackDone && isListening) setTimeout(() => { if (isListening && !cloudFallbackDone) openAsr(); }, 1000);
      }, ASR_CONNECT_TIMEOUT_MS);
      asrWs.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
          if (msg.type === 'vad') {
            // 用户开始说话：重置通话长静默计时（避免 AI 长播报后误挂断），
            // 并触发 barge-in（随时打断 AI 播报）
            if (msg.event === 'speech_start') {
              startCallSilenceTimer();
              if (debug) console.log('[SttContinuous] ASR VAD speech_start');
              onUserSpeechInDialog?.();
            }
          } else if (msg.type === 'asr') {
            const text = (msg.text || '').toString();
            if (msg.isPartial) {
              // 端侧流式 partial：直接用于输入框实时显示
              if (onLiveDisplayText && text.trim()) onLiveDisplayText(text.trim());
            } else if (text.trim()) {
              // 有识别结果：重置长静默计时，并提交结果
              startCallSilenceTimer();
              handleSttResult(text, false);
            }
          }
        } catch (e) { /* 忽略非 JSON 帧 */ }
      };
      asrWs.onerror = () => {
        console.warn('[SttContinuous] 端侧 ASR WS 出错（端侧设备不可用？检查 ' + asrUrl + '）');
      };
      asrWs.onclose = () => {
        // 原实现这里判 `!asrWs`，但 asrWs 仍指向已关闭的套接字（truthy）→ 重连永不触发。
        // 先置 null 再判定，重连/降级才有机会执行。
        asrWs = null;
        if (asrConnectTimer) { clearTimeout(asrConnectTimer); asrConnectTimer = null; }
        if (!asrEverOpened) noteAsrFailure('连接关闭');
        if (cloudFallbackDone) return;
        console.warn('[SttContinuous] 端侧 ASR 断开，1s 后重连');
        if (isListening) {
          setTimeout(() => { if (isListening && !asrWs && !cloudFallbackDone) openAsr(); }, 1000);
        }
      };
    } catch (e) {
      if (debug) console.warn('[SttContinuous] ASR 启动失败，回退后端STT:', e);
      noteAsrFailure(`建连异常 ${String(e)}`);
    }
  }
  function closeAsr() {
    if (asrConnectTimer) { clearTimeout(asrConnectTimer); asrConnectTimer = null; }
    if (asrWs) {
      try { asrWs.onopen = null; asrWs.onmessage = null; asrWs.onerror = null; asrWs.onclose = null; asrWs.close(); } catch (e) {}
      asrWs = null;
    }
  }

  async function start() {
    if (isListening) return;
    isListening = true;
    isPaused = false;
    acc = [];

    try {
      const audioConfig = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      };

      if (audioDeviceId) {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { ...audioConfig, deviceId: { exact: audioDeviceId } },
          video: false,
        });
      } else {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConfig,
          video: false,
        });
      }

      ctx = new AudioContext();
      // 浏览器自动播放策略会把非用户手势创建的 AudioContext 挂起（suspended），
      // 导致 onaudioprocess 不触发、音频不推送到端侧 KWS，唤醒词因此无反馈。需显式 resume。
      registerAudioContext(ctx);
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      source = ctx.createMediaStreamSource(stream);

      // @ts-ignore
      processor = ctx.createScriptProcessor(4096, 1, 1);

      // 链路复位：云端优先时直接走云端 STT（不连端侧）；否则先尝试端侧 ASR，
      // 端侧不在场时由降级逻辑热切到后端云端 STT。
      // 端侧：PCM 帧直推设备（设备内 VAD + 流式识别），延迟最低；
      // 云端：本地内联 VAD 断句 + 云端流式/整段上传（不依赖任何端侧设备）。
      onDeviceAsr = !!asrUrl && !shouldPreferCloud();
      asrEverOpened = false;
      asrFailStreak = 0;
      cloudFallbackDone = false;
      cloudFailStreak = 0;
      reverseSwitches = 0;
      resetCloudStreamRound();
      bindAudioPipeline();

      source.connect(processor);
      processor.connect(ctx.destination);

      // 开启端侧 ASR 推流（非阻塞；连接就绪前丢弃的少量首帧不影响设备内 VAD 同步）
      if (onDeviceAsr) openAsr();

      if (debug) console.log('[SttContinuous] 持续识别已开启');
      onStart?.();
      // 通话模式：开启持续识别即"接听"，进入 call session
      if (callMode) {
        callActive = true;
        onCallStateChange?.(true, 'answer');
        onCallStarted?.();
        try { playAnswerTone(); } catch (e) { console.warn('[持续识别通话] 接听提示音播放失败', e); }
        startCallSilenceTimer();
      }
      // 云端路径：启动静默兜底轮计时（VAD 迟迟不触发时整段上传缓冲，保底出转写）
      resetIdleRoundTimer();
      startDegradedWatchdog();
    } catch (e) {
      console.error('[SttContinuous] 启动失败:', e);
      isListening = false;
    }
  }

  function stop() {
    isListening = false;
    isPaused = false;

    // 通话模式：停止监听即挂断（清除计时、播挂断音、通知 UI）
    if (callActive) {
      callActive = false;
      if (callSilenceTimer) {
        clearTimeout(callSilenceTimer);
        callSilenceTimer = null;
      }
      onCallStateChange?.(false, 'stop');
      onCallEnded?.('stop');
      try { playHangupTone(); } catch (e) { console.warn('[持续识别通话] 挂断提示音播放失败', e); }
    }

    inlineVad?.reset();
    inlineVad = null;
    closeAsr();
    clearIdleRoundTimer();
    stopDegradedWatchdog();
    cloudAcc = [];
    captureSilent = false;
    silentRoundCount = 0;
    srLastText = "";
    srLastSubmitted = "";
    resetCloudStreamRound();
    liveDisplay?.stop();
    liveDisplay = null;
    echoGate.reset();

    processor?.disconnect();
    source?.disconnect();
    ctx?.close();
    stream?.getTracks().forEach((t) => t.stop());

    processor = null;
    source = null;
    ctx = null;
    stream = null;
    acc = [];

    if (debug) console.log('[SttContinuous] 持续识别已停止');
  }

  function pause() {
    isPaused = true;
    // 通话模式：挂起期间用户不在说话，暂停长静默计时，避免 AI 长播报误触发自动挂断
    if (callSilenceTimer) {
      clearTimeout(callSilenceTimer);
      callSilenceTimer = null;
    }
    clearIdleRoundTimer();
    inlineVad?.reset();
    echoGate.setAiSpeaking(false);
    if (debug) console.log('[SttContinuous] 持续识别已挂起');
  }

  async function resume() {
    // 🔴 幂等：只有真被挂起过才做重置。
    // 调用方（useVoiceOrchestration 的 reconcile）在**每次 React 渲染后**都会调本方法
    // （reconcile 的 useCallback 依赖里挂着每次渲染都换身份的 onCookingIntentDetected），
    // 若无脑清 acc / 重建 inlineVad，云端路径「说话 → 静音 3s 收口」永远走不完：
    // 刚攒的音频被清空、VAD 状态被重置 → flushAndSend 发不出东西 → 整段识别丢空。
    // （2026-09-18 实测排查：端侧路径因为逐帧直推、不依赖 acc，所以看不出问题，
    //   一旦降级到云端就表现为"说了话完全没反应"。）
    if (!isListening || !isPaused) return;
    isPaused = false;
    isSpeaking = false;
    acc = [];
    // 通话模式：恢复后重启长静默计时
    if (callActive) startCallSilenceTimer();
    if (onDeviceAsr) {
      // 恢复推流：重开端侧 ASR WS（pause 期间可能已断开）
      openAsr();
    } else {
      // 重建内联 VAD（后端云端 STT 路径，含端侧降级后的场景）
      bindAudioPipeline();
    }
    // 云端路径：恢复静默兜底轮计时
    resetIdleRoundTimer();
    if (debug) console.log('[SttContinuous] 持续识别已恢复');
    return Promise.resolve();
  }

  /** AI 播报状态同步到回声门控（true=正在播报，期间抑制疑似回声推流） */
  function notifyAiState(speaking: boolean) {
    echoGate.setAiSpeaking(speaking);
    if (debug) console.log(`[SttContinuous] AI 播报状态 -> ${speaking ? '播报中(回声抑制)' : '结束'}`);
  }

  return { start, stop, pause, resume, handleSttResult, hangupCall, isCallActive, notifyAiState };
}
