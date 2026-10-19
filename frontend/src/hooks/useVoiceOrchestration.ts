import { Dispatch, MutableRefObject, SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WAKE_WORDS } from "../lib/constants";
import { createContinuousSpeechListener, createSimpleWakeWordListener } from "../lib/wakeword";
import { createSttContinuousListener, createSttWakeWordListener, SttContinuousController, SttWakeWordController } from "../lib/wakeword-stt";
import { trimWakeWordPrefix } from "../lib/voice/wake-words";
import { createTTSController } from "../lib/tts";
import {
  CookingVoiceContext,
  ContinuousSpeechListenerController,
  createVoiceOrchestrationPlan,
  getDesiredVoiceMode,
  VoiceLogLevel,
  WakeWordListenerController,
} from "../lib/voice-orchestrator";
import { recognizeCookingIntent } from "../lib/cooking-intent";

interface UseVoiceOrchestrationOptions {
  wakeWordEnabled: boolean;
  continuousMode: boolean;
  setContinuousMode: Dispatch<SetStateAction<boolean>>;
  continuousSpeechPaused: boolean;
  isAiSpeaking: boolean;
  isAiSpeakingRef: MutableRefObject<boolean>;
  selectedAudioDevice: string;
  sendInterrupt: () => void;
  sendTextMessageRef: MutableRefObject<((text: string) => Promise<void>) | null>;
  /** 后端 STT 模式下仅添加用户消息到 UI，不重复发送 WebSocket（后端已自动处理） */
  addUserMessageOnlyRef: MutableRefObject<((text: string) => void) | null>;
  stopBrowserTts: () => void;
  setWakeWordDetected: Dispatch<SetStateAction<boolean>>;
  setWakeWordLiveTranscript: Dispatch<SetStateAction<string>>;
  setWakeWordStatusText: Dispatch<SetStateAction<string>>;
  cooking?: CookingVoiceContext;
  /**
   * 做饭意图检测回调。
   * 不传参或传 "start"：进入/重新开始做饭流程；
   * 传 "stop"：退出做饭流程（仅在已激活状态下识别到明确的停止类强意图时触发）。
   */
  onCookingIntentDetected?: (action?: "start" | "stop") => void;
  sttProvider: string;
  /**
   * 云端优先（端侧兜底）识别策略，唤醒词与持续识别/通话模式共用同一开关：
   * true=云端优先——唤醒后双发（云端先回用云端）；持续识别直接走云端（实时 partial），
   * 云端失败回退端侧。false=纯端侧（低延迟），端侧设备不在场时自动回退云端 STT。默认 true。
   */
  sttPreferCloud?: boolean;
  /** 语音实时转写上屏（浏览器并行识别的显示文本；仅显示，不参与消息发送） */
  notifyLiveSttText?: (text: string) => void;
  /** 读取输入框中的实时转写文本（仅当框内为转写文本时返回，否则空串）；用于后端识别为空时回退发送 */
  getLiveSttBoxText?: () => string;
  /** WebSocket 发送函数，用于发送音频到后端 STT */
  wsSendRef: MutableRefObject<((type: string, payload: any) => void) | null>;
}

interface UseVoiceOrchestrationResult {
  resumeVoiceOrchestration: (delay?: number, reason?: string) => void;
  handleWsConnected: () => void;
  handleWsDisconnected: () => void;
  /** 处理后端返回的 STT 识别结果，传递给唤醒词/持续识别控制器 */
  handleSttResult: (text: string, isPartial: boolean) => void;
  /** 由后端唤醒词检测触发的流程 */
  triggerWakeWordFromBackend: () => void;
  /** 通话模式：当前是否处于通话中 */
  callActive: boolean;
  /** 通话模式：挂断/结束通话（供 HUD 按钮或眼镜手势调用） */
  hangupCall: () => void;
}

// 语音指令：唤醒词模式下说"打开持续唤醒/开启持续识别"等变体 → 开启「持续识别」
// （continuousMode=true，免唤醒词持续监听），拦截后不提交给后端 LLM。
function matchContinuousModeEnableCommand(text: string): boolean {
  const t = text.replace(/\s+/g, "");
  if (!/(持续|连续)(唤醒|识别|监听)/.test(t)) return false;
  return /(打开|开启|启动|开始|进入)/.test(t);
}

// 端侧语音服务地址（模型配置-语音识别中配置；默认香橙派 192.168.1.248）
function readEdgeServiceUrl(storageKey: string): string | undefined {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw);
    return typeof parsed === "string" && parsed.trim() ? parsed.trim() : undefined;
  } catch {
    return undefined;
  }
}

export function useVoiceOrchestration(options: UseVoiceOrchestrationOptions): UseVoiceOrchestrationResult {
  const {
    wakeWordEnabled,
    continuousMode,
    setContinuousMode,
    continuousSpeechPaused,
    isAiSpeaking,
    isAiSpeakingRef,
    selectedAudioDevice,
    sendInterrupt,
    sendTextMessageRef,
    addUserMessageOnlyRef,
    stopBrowserTts,
    setWakeWordDetected,
    setWakeWordLiveTranscript,
    setWakeWordStatusText,
    cooking,
    onCookingIntentDetected,
    sttProvider,
    sttPreferCloud = true,
    notifyLiveSttText,
    getLiveSttBoxText,
    wsSendRef,
  } = options;

  const wakeWordEnabledRef = useRef(wakeWordEnabled);
  const continuousModeRef = useRef(continuousMode);
  const continuousSpeechPausedRef = useRef(continuousSpeechPaused);
  const sttProviderRef = useRef(sttProvider);
  // 识别策略（云端优先/纯端侧）：经 ref 透传给长生命周期控制器，UI 切换即时生效
  const sttPreferCloudRef = useRef(sttPreferCloud);
  // 实时转写回调经 ref 透传：控制器长生命周期，避免闭包捕获过期版本
  const notifyLiveSttRef = useRef(notifyLiveSttText);
  useEffect(() => {
    notifyLiveSttRef.current = notifyLiveSttText;
  }, [notifyLiveSttText]);
  const getLiveSttBoxTextRef = useRef(getLiveSttBoxText);
  useEffect(() => {
    getLiveSttBoxTextRef.current = getLiveSttBoxText;
  }, [getLiveSttBoxText]);
  const wakeWordListenerRef = useRef<WakeWordListenerController | null>(null);
  const continuousSpeechListenerRef = useRef<ContinuousSpeechListenerController | null>(null);
  // STT 模式下的控制器
  const sttWakeWordRef = useRef<SttWakeWordController | null>(null);
  const sttWakeWordSettingRef = useRef(false); // 防并发创建锁（setupWakeWordListener 是 async）
  const sttContinuousRef = useRef<SttContinuousController | null>(null);
  const wakeWordResumeTimerRef = useRef<number | null>(null);
  const wakeWordSuspendedRef = useRef(false);
  // 唤醒反馈流程级超时定时器：triggerWakeWordFromBackend 播放"你好我在"后启动，
  // 反馈 endCallback 正常到达时清除；超时到达说明反馈被吞/合成器异常，兜底走
  // 与完成等价的收集恢复路径，保证流程不死等
  const wakeWordFeedbackTimeoutRef = useRef<number | null>(null);
  const wsConnectedRef = useRef(false);
  const voiceLogTimesRef = useRef<Record<string, number>>({});
  const reconcileVoiceOrchestrationRef = useRef<((reason?: string) => Promise<void>) | null>(null);
  // 持续语音识别消息去重
  const lastContinuousSpeechTextRef = useRef<string>("");
  const lastContinuousSpeechTimeRef = useRef<number>(0);

  // 做饭状态最新值：唤醒词监听器是长生命周期闭包，setupWakeWordListener 在已有监听器时
  // 不会重建（forceRestart=false 直接 return），闭包捕获的 cooking 会过期；
  // 因此意图判定时必须读 ref 拿最新状态，否则"已激活"门控可能读到创建时的旧快照
  const cookingRef = useRef<CookingVoiceContext | undefined>(cooking);
  useEffect(() => {
    cookingRef.current = cooking;
  }, [cooking]);

  // 通话模式：通话状态（接听/通话中/挂断）由持续识别控制器驱动
  const [callActive, setCallActive] = useState(false);
  
  // 唤醒词反馈 TTS 控制器 - 使用 useMemo 避免每次渲染创建新实例
  const wakeWordFeedbackTtsRef = useRef<any>(useMemo(
    () => createTTSController({ rate: 1.1, pitch: 1, volume: 1 }, () => {
    console.log('[唤醒词反馈] 语音播放完成');
    // 正常完成：清除流程级超时兜底（防止超时定时器稍后重复走恢复路径）
    if (wakeWordFeedbackTimeoutRef.current !== null) {
      clearTimeout(wakeWordFeedbackTimeoutRef.current);
      wakeWordFeedbackTimeoutRef.current = null;
    }
    // 反馈播放结束：恢复 ASR/KWS 推流（解除回声屏蔽期）
    sttWakeWordRef.current?.setFeedbackSpeaking?.(false);
    (wakeWordListenerRef.current as any)?.setFeedbackSpeaking?.(false);
    // 反馈播放完成后，通知唤醒词监听器开始收集用户语音
    if (wakeWordListenerRef.current || sttWakeWordRef.current) {
      // 前端检测模式：通知现有监听器开始收集
      (wakeWordListenerRef.current as any)?.startCollecting?.();
      sttWakeWordRef.current?.startCollecting?.();
    } else {
      // 后端检测模式：重新启动语音编排收集用户语音
      console.log('[唤醒词反馈] 后端检测模式，重启语音编排收集用户语音');
      setWakeWordDetected(false);
      if (!wakeWordEnabledRef.current && !continuousModeRef.current) {
        console.log('[唤醒词反馈] 语音入口已关闭，跳过重启编排');
        return;
      }
      resumeVoiceOrchestration(100, "backend-wakeword-feedback-done");
    }
  }),
  []));
  
  // 设置错误回调
  wakeWordFeedbackTtsRef.current.onError = (error: any) => {
    console.error('[唤醒词反馈] TTS错误:', error);
  };
  // 设置播放开始回调：屏蔽 ASR/KWS 推流，防止"你好我在"回声自循环
  // （TTS 声音经麦克风回采 → ASR 识别成指令 / KWS 误触发唤醒词）
  wakeWordFeedbackTtsRef.current.onStart = () => {
    sttWakeWordRef.current?.setFeedbackSpeaking?.(true);
    (wakeWordListenerRef.current as any)?.setFeedbackSpeaking?.(true);
  };

  // 唤醒词反馈"你好我在"的可靠播放
  // 1) 先解除可能残留的回声屏蔽（feedbackSpeaking 若卡在 true，会静音 KWS/ASR，
  //    导致后续唤醒词完全检测不到——典型表现为"第二次喊唤醒词没反应"）
  // 2) cancel()/speak() 竞态的根治在 tts.ts：speak() 让路旧 utterance 时内部会等
  //    cancel 收尾（被取消 utterance 的 onend/onerror 或 120ms 超时，取先到）再发
  //    新 utterance，并有 onstart 看门狗兜底被吞场景。此处的 ~120ms 延迟是历史
  //    保守措施，继续保留作为双保险（代价可忽略）。
  const playWakeWordFeedback = useCallback(() => {
    const wasAiSpeaking = isAiSpeakingRef.current;
    sendInterrupt(); // barge-in: 停当前 AI TTS（未在播报时内部为空操作）
    sttWakeWordRef.current?.setFeedbackSpeaking?.(false);
    (wakeWordListenerRef.current as any)?.setFeedbackSpeaking?.(false);
    setTimeout(() => {
      try {
        console.log('[唤醒词反馈] 播放: 你好我在！');
        wakeWordFeedbackTtsRef.current.speak("你好我在！");
      } catch (error) {
        console.error('[唤醒词反馈] 播放失败:', error);
      }
    }, wasAiSpeaking ? 120 : 0);
  }, [sendInterrupt]);

  useEffect(() => {
    wakeWordEnabledRef.current = wakeWordEnabled;
  }, [wakeWordEnabled]);

  useEffect(() => {
    continuousModeRef.current = continuousMode;
  }, [continuousMode]);

  useEffect(() => {
    continuousSpeechPausedRef.current = continuousSpeechPaused;
  }, [continuousSpeechPaused]);

  useEffect(() => {
    sttProviderRef.current = sttProvider;
  }, [sttProvider]);

  useEffect(() => {
    sttPreferCloudRef.current = sttPreferCloud;
  }, [sttPreferCloud]);

  // ==================== 播报期打断（barge-in） ====================
  // 说明：AI 播报期间**必须保留** KWS/ASR 音频推流，用户才能随时说话打断。
  // 回声防护由两层完成，而非物理停流：
  //  1) continuous/wakeword 各自的 echoGate（能量门控）：AI 播报期 → 抑制疑似回声帧，
  //     真人语音能量显著跃升 → 放行推流 → 设备 VAD speech_start → onUserSpeechInDialog → sendInterrupt。
  //  2) 唤醒反馈"你好我在"（wakeWordFeedbackTtsRef onStart/onEnd）仍走 setFeedbackSpeaking，
  //     短反馈期间物理停流，防止"你好我在"被识别成指令/唤醒词自循环。
  // 历史：曾把 isAiSpeaking 同步进 setFeedbackSpeaking（半双工物理停流），
  // 导致播报期用户说话完全无法被检测 → 打断功能失效。已移除该逻辑。
  const useBackendStt = sttProvider !== "browser";

  const setVoiceStatus = useCallback((status: string) => {
    setWakeWordStatusText((previousStatus) => (previousStatus === status ? previousStatus : status));
  }, [setWakeWordStatusText]);

  const logVoiceEvent = useCallback((key: string, message: string, level: VoiceLogLevel = "log", throttleMs = 1200) => {
    const now = Date.now();
    const lastAt = voiceLogTimesRef.current[key] ?? 0;

    if (now - lastAt < throttleMs) {
      return;
    }

    voiceLogTimesRef.current[key] = now;
    console[level](message);
  }, []);

  const clearWakeWordResumeTimer = useCallback(() => {
    if (wakeWordResumeTimerRef.current) {
      clearTimeout(wakeWordResumeTimerRef.current);
      wakeWordResumeTimerRef.current = null;
    }
  }, []);

  const resumeVoiceOrchestration = useCallback((delay = 0, reason = "resume") => {
    clearWakeWordResumeTimer();
    wakeWordResumeTimerRef.current = window.setTimeout(() => {
      wakeWordResumeTimerRef.current = null;
      void reconcileVoiceOrchestrationRef.current?.(reason);
    }, delay);
  }, [clearWakeWordResumeTimer]);

  const stopContinuousSpeech = useCallback(() => {
    if (sttContinuousRef.current) {
      sttContinuousRef.current.stop();
      sttContinuousRef.current = null;
    }
    if (continuousSpeechListenerRef.current) {
      continuousSpeechListenerRef.current.stop();
      continuousSpeechListenerRef.current = null;
    }
  }, []);

  // 发送音频到后端 STT（每次作为独立录音段）
  // sttOnly=true 时仅做语音识别，不自动提交到 LLM（用于唤醒词检测）
  const sendAudioChunk = useCallback((pcm16_b64: string, isLast: boolean, sttOnly = false) => {
    const audioSize = Math.round(pcm16_b64.length * 3 / 4); // base64 解码后大约大小
    console.log(`[STT] 发送音频到后端: ${audioSize} bytes, provider=${sttProviderRef.current}, isLast=${isLast}, sttOnly=${sttOnly}`);
    // 每次作为独立的完整录音段发送，seq 从 0 开始
    wsSendRef.current?.("audio_stream", {
      seq: 0,
      pcm16_b64,
      is_last: isLast,
      stt_provider: sttProviderRef.current,
    });
    if (isLast) {
      wsSendRef.current?.("audio_stream_end", {
        last_seq: 0,
        stt_provider: sttProviderRef.current,
        stt_only: sttOnly,
      });
    }
  }, [wsSendRef]);

  // 云端流式 STT（云端优先时启用）：收集期逐帧上行（stream=true），后端喂 Fun-ASR
  // 流式会话，说话期间实时回 stt_partial；audio_stream_end 收口出 stt_final
  const cloudStreamSeqRef = useRef(0);
  const sendCloudStreamBegin = useCallback(() => {
    cloudStreamSeqRef.current = 0; // 无显式 begin 帧：仅复位序号，首帧到达时后端建会话
  }, []);
  const sendCloudStreamFrame = useCallback((pcm16_b64: string) => {
    cloudStreamSeqRef.current += 1;
    wsSendRef.current?.("audio_stream", {
      seq: cloudStreamSeqRef.current,
      pcm16_b64,
      stream: true,
      stt_provider: sttProviderRef.current,
    });
  }, [wsSendRef]);
  const sendCloudStreamEnd = useCallback(() => {
    wsSendRef.current?.("audio_stream_end", {
      last_seq: cloudStreamSeqRef.current,
      stt_provider: sttProviderRef.current,
      stt_only: true,
      stream: true,
    });
  }, [wsSendRef]);

  const setupWakeWordListener = useCallback(async (forceRestart = false) => {
    if (forceRestart) {
      if (sttWakeWordRef.current) {
        sttWakeWordRef.current.destroy();
        sttWakeWordRef.current = null;
      }
      if (wakeWordListenerRef.current) {
        wakeWordListenerRef.current.destroy();
        wakeWordListenerRef.current = null;
      }
    }

    // 切换 STT 模式时，先销毁另一种模式的旧监听器
    if (useBackendStt && wakeWordListenerRef.current) {
      wakeWordListenerRef.current.destroy();
      wakeWordListenerRef.current = null;
    }
    if (!useBackendStt && sttWakeWordRef.current) {
      sttWakeWordRef.current.destroy();
      sttWakeWordRef.current = null;
    }

    // 唤醒词检测到后的通用处理
    const onWakeWordDetected = (recognizedText: string) => {
      clearWakeWordResumeTimer();
      setVoiceStatus("已检测到唤醒词");
      setWakeWordLiveTranscript("");
      stopBrowserTts();

      if (isAiSpeakingRef.current) {
        sendInterrupt();
      }

      setWakeWordDetected(true);
      // 连续对话模式：不销毁监听器，交由 wakeword-stt 内部进入连续对话窗口期
      // （CONVERSING 状态）自行管理；下一轮用户说话无需再次唤醒词。
      // 也不置 wakeWordSuspended=true，避免 reconcile 在 AI 播报期间 forceRestart 重建，
      // 那样会打断连续对话。

      const userText = trimWakeWordPrefix(WAKE_WORDS, recognizedText);

      if (userText) {
        // 语音指令拦截：说"打开持续唤醒"等 → 开启持续识别（免唤醒词），
        // 与手动点击「持续识别」按钮等价：setContinuousMode 触发 reconcile
        // 销毁唤醒词监听、启动持续识别（通话模式，自带接听提示音）
        if (matchContinuousModeEnableCommand(userText)) {
          console.log('[唤醒词] 检测到开启持续识别指令:', userText);
          setVoiceStatus("持续识别已开启");
          setWakeWordDetected(false);
          setWakeWordLiveTranscript("");
          wakeWordSuspendedRef.current = false;
          if (!continuousModeRef.current) {
            setContinuousMode(true);
          }
          return;
        }

        // 当前是否已处于做饭流程（面板激活或状态机非 IDLE）；读 ref 取最新状态
        const currentCooking = cookingRef.current;
        const cookingActive = !!currentCooking && (
          currentCooking.enabled === true || (currentCooking.state ?? "IDLE") !== "IDLE"
        );
        // 检测做饭意图（激活状态下泛化短词被门控，避免聊天中提"做饭"误触发重置）
        const cookingIntent = recognizeCookingIntent(userText, { cookingActive });

        // 做饭流程中：明确的"退出/停止做饭"强意图 → 派发 stop
        if (cookingActive && cookingIntent.isStopIntent) {
          console.log('[唤醒词] 做饭流程中检测到退出做饭意图:', userText);
          setVoiceStatus("正在退出做饭模式...");
          setWakeWordDetected(false);
          wakeWordSuspendedRef.current = false;
          onCookingIntentDetected?.("stop");
          return;
        }

        if (cookingIntent.isCookingIntent && cookingIntent.confidence > 0.4) {
          if (!cookingActive) {
            // 初次进入做饭（未激活）：保持原有行为
            console.log('[唤醒词] 检测到做饭意图:', userText, '置信度:', cookingIntent.confidence);
            setVoiceStatus("检测到做饭意图，正在启动做饭模式...");
            setWakeWordDetected(false);
            wakeWordSuspendedRef.current = false; // 重置暂停状态
            onCookingIntentDetected?.();
            return;
          }
          if (cookingIntent.isRestartIntent) {
            // 已在做饭流程中：仅明确的"重新开始/再来一次/换个菜"才重置状态机
            console.log('[唤醒词] 做饭流程中检测到重新开始意图:', userText);
            setVoiceStatus("正在重新开始做饭...");
            setWakeWordDetected(false);
            wakeWordSuspendedRef.current = false;
            onCookingIntentDetected?.("start");
            return;
          }
          // 已在做饭流程中 + 泛化"做饭"提及（如"这个菜做饭要注意什么"）：
          // 不重置状态机、不派发 cooking start，降级为普通对话
          console.log('[唤醒词] 做饭流程进行中，忽略泛化做饭意图:', userText);
        }
        // 普通对话意图
        setVoiceStatus("正在提交问题给大模型...");
        setWakeWordLiveTranscript("");
        void sendTextMessageRef.current?.(userText);
        setWakeWordDetected(false);
        wakeWordSuspendedRef.current = false; // 重置暂停状态
      } else {
        // 后端识别为空：回退发送输入框中的实时转写文本（用户看到的字即所发；
        // 覆盖后端偶发识别失败时"空结果不发送"导致指令丢失的场景）
        const liveBoxText = trimWakeWordPrefix(WAKE_WORDS, (getLiveSttBoxTextRef.current?.() || "").trim());
        if (liveBoxText) {
          console.log('[唤醒词] 后端识别为空，回退发送实时转写文本:', liveBoxText);
          setVoiceStatus("正在提交问题给大模型...");
          setWakeWordLiveTranscript("");
          void sendTextMessageRef.current?.(liveBoxText);
          setWakeWordDetected(false);
          wakeWordSuspendedRef.current = false;
          return;
        }
        setVoiceStatus("请继续说出完整问题");
        setWakeWordDetected(false);
        setWakeWordLiveTranscript("");
        wakeWordSuspendedRef.current = false; // 重置暂停状态
        if (!wakeWordEnabledRef.current && !continuousModeRef.current) {
          console.log('[唤醒词] 语音入口已关闭，不重启监听');
        } else {
          resumeVoiceOrchestration(500, "wakeword-empty");
        }
      }
    };

    if (useBackendStt) {
      // 使用后端 STT 的唤醒词检测
      if (sttWakeWordRef.current || sttWakeWordSettingRef.current) return;
      sttWakeWordSettingRef.current = true;
      try {
        setVoiceStatus("唤醒词正在启动...");
        sttWakeWordRef.current = await createSttWakeWordListener({
          wakeWords: WAKE_WORDS,
          kwsUrl: readEdgeServiceUrl("multimodal_edge_kws_url"),
          asrUrl: readEdgeServiceUrl("multimodal_edge_asr_url"),
          sendAudioForStt: sendAudioChunk,
          // 云端优先：唤醒后音频在推端侧 ASR 的同时整段上传后端云端 STT，
          // 云端结果先到用云端（准确率更高），超时/云端不可用自动回退端侧结果。
          // 由「模型配置 → 语音识别 → 云端优先（端侧兜底）」开关控制（传 getter 实时取值，
          // 切换后无需重建监听器）：关闭则退回低延迟的纯端侧识别。
          preferCloud: () => sttPreferCloudRef.current,
          // 云端流式：收集期逐帧上行，说话期间即出云端 partial（输入框实时显示云端文本，
          // 优先于端侧）；说话结束收口会话直接出 final，省去整段重传
          cloudStream: {
            begin: sendCloudStreamBegin,
            push: sendCloudStreamFrame,
            end: sendCloudStreamEnd,
          },
          onLiveDisplayText: (t: string) => { notifyLiveSttRef.current?.(t); },
          debug: true,
          onWakeWordTriggered: () => {
            console.log('[唤醒词反馈] 检测到唤醒词，停AI播报+播放: 你好我在！');
            playWakeWordFeedback();
          },
          onWakeWordDetected,
          audioDeviceId: selectedAudioDevice || undefined,
          onTranscriptChange: (text) => {
            setWakeWordLiveTranscript(text);
          },
          onStatusChange: (status) => {
            if (getDesiredVoiceMode({
              wakeWordEnabled: wakeWordEnabledRef.current,
              continuousMode: continuousModeRef.current,
            }) === "wakeword") {
              setVoiceStatus(status);
            }
          },
          onError: (errorType, message) => {
            logVoiceEvent(`wakeword-error-${errorType}`, `[唤醒词] 监听异常: ${errorType} ${message}`, "warn", 2000);
          },
          // 单次唤醒模式：每轮对话结束立即退回待命，下次使用需重新喊"小云小云"。
          // 关闭唤醒后的持续识别（CONVERSING 窗口），避免麦克风持续收音把 AI 播报回声/噪音误识别成指令。
          continuousDialog: false,
          // 唤醒词打断 AI 播报：反馈 TTS barge-in 已由 onWakeWordTriggered 触发
        });
        await sttWakeWordRef.current.start();
        // 异步启动期间状态可能已变化，启动后二次确认
        if (!wakeWordEnabledRef.current || continuousModeRef.current) {
          console.log('[唤醒词] 启动完成后发现语音入口已关闭，立即销毁');
          sttWakeWordRef.current.destroy();
          sttWakeWordRef.current = null;
          return;
        }
        wakeWordSuspendedRef.current = false;
        setVoiceStatus("唤醒词监听已启动");
      } catch (error) {
        console.error("[App] 唤醒词监听启动失败:", error);
        setVoiceStatus("唤醒词启动失败");
        sttWakeWordRef.current = null;
      } finally {
        sttWakeWordSettingRef.current = false;
      }
    } else {
      // 使用浏览器 SpeechRecognition 的唤醒词检测
      if (wakeWordListenerRef.current) return;

      try {
        setVoiceStatus("唤醒词正在启动...");
        wakeWordListenerRef.current = await createSimpleWakeWordListener({
          wakeWords: WAKE_WORDS,
          onWakeWordTriggered: () => {
            console.log('[唤醒词反馈] 检测到唤醒词，停AI播报+播放: 你好我在！');
            playWakeWordFeedback();
          },
          onWakeWordDetected,
          audioDeviceId: selectedAudioDevice || undefined,
          onTranscriptChange: (text) => {
            setWakeWordLiveTranscript(text);
          },
          onStatusChange: (status) => {
            if (getDesiredVoiceMode({
              wakeWordEnabled: wakeWordEnabledRef.current,
              continuousMode: continuousModeRef.current,
            }) === "wakeword") {
              setVoiceStatus(status);
            }
          },
          onError: (errorType, message) => {
            logVoiceEvent(`wakeword-error-${errorType}`, `[唤醒词] 监听异常: ${errorType} ${message}`, "warn", 2000);
          },
        });
        await wakeWordListenerRef.current.start();
        // 异步启动期间状态可能已变化，启动后二次确认
        if (!wakeWordEnabledRef.current || continuousModeRef.current) {
          console.log('[唤醒词] 启动完成后发现语音入口已关闭，立即销毁');
          wakeWordListenerRef.current.destroy();
          wakeWordListenerRef.current = null;
          return;
        }
        wakeWordSuspendedRef.current = false;
        setVoiceStatus("唤醒词监听已启动");
      } catch (error) {
        console.error("[App] 唤醒词监听启动失败:", error);
        setVoiceStatus("唤醒词启动失败");
        wakeWordListenerRef.current = null;
      }
    }
  }, [
    clearWakeWordResumeTimer,
    logVoiceEvent,
    onCookingIntentDetected,
    resumeVoiceOrchestration,
    selectedAudioDevice,
    sendAudioChunk,
    sendCloudStreamBegin,
    sendCloudStreamEnd,
    sendCloudStreamFrame,
    sendInterrupt,
    sendTextMessageRef,
    setContinuousMode,
    setVoiceStatus,
    setWakeWordDetected,
    setWakeWordLiveTranscript,
    stopBrowserTts,
    isAiSpeakingRef,
    playWakeWordFeedback,
    useBackendStt,
  ]);

  const setupContinuousSpeech = useCallback(async () => {
    // 切换 STT 模式时，先销毁另一种模式的旧监听器
    if (useBackendStt && continuousSpeechListenerRef.current) {
      continuousSpeechListenerRef.current.stop();
      continuousSpeechListenerRef.current = null;
    }
    if (!useBackendStt && sttContinuousRef.current) {
      sttContinuousRef.current.stop();
      sttContinuousRef.current = null;
    }

    if (useBackendStt) {
      if (sttContinuousRef.current) return;
      try {
        sttContinuousRef.current = createSttContinuousListener({
          sendAudioForStt: sendAudioChunk,
          // 端侧 ASR 地址与唤醒词链路同源（模型配置 → 语音识别 → 端侧语音服务）
          asrUrl: readEdgeServiceUrl("multimodal_edge_asr_url"),
          // 端侧设备不在场时自动回退后端云端 STT（本地 VAD 断句 + 整段上传），
          // 否则「没插香橙派」会表现为持续识别完全不工作。
          allowCloudFallback: true,
          // 云端优先（与唤醒词模式同一开关）：开启时持续识别直接走云端 STT
          // （本地 VAD + 云端流式实时 partial），云端失败自动回退端侧；
          // 关闭则端侧优先。传 getter 实时取值，会话开始时评估（切换后重开生效）。
          preferCloud: () => sttPreferCloudRef.current,
          // 云端回"未识别"（采集静音，如麦克风选错/设备哑）时，把输入框里浏览器
          // 实时转写的文本兜底提交给 AI——浏览器识别走系统默认麦克风，与采集设备无关
          getLiveSttFallbackText: () => (getLiveSttBoxTextRef.current?.() || "").trim(),
          // 云端流式（云端回退路径时启用）：说话期间逐帧上行，partial 实时回输入框，
          // 静默收口直接出 stt_final。与唤醒词链路共用同一组上行回调
          //（两条链路互斥，不会并发开两路会话）。
          cloudStream: {
            begin: sendCloudStreamBegin,
            push: sendCloudStreamFrame,
            end: sendCloudStreamEnd,
          },
          onLiveDisplayText: (t: string) => { notifyLiveSttRef.current?.(t); },
          silenceSeconds: 3,
          debug: true,
          onResult: (text: string) => {
            // 过滤无效识别结果
            if (text.includes('未识别') || text.includes('识别失败') || !text.trim()) {
              console.log('[ContinuousSpeech] 跳过无效识别结果:', text);
              return;
            }
            // 持续语音识别消息去重
            const now = Date.now();
            const trimmedText = text.trim();
            if (
              lastContinuousSpeechTextRef.current === trimmedText &&
              now - lastContinuousSpeechTimeRef.current < 2000
            ) {
              console.log('[ContinuousSpeech] 跳过重复消息:', trimmedText);
              return;
            }
            lastContinuousSpeechTextRef.current = trimmedText;
            lastContinuousSpeechTimeRef.current = now;
            console.log("[ContinuousSpeech] 识别内容已发送:", trimmedText);
            // 端侧 ASR 模式（默认）：识别文本由设备内 ASR 返回，前端需自行发给后端
            // （后端不再经 audio_stream_end 自动提交），sendTextMessage 同时完成 UI 展示 + 后端提交
            if (sendTextMessageRef.current) {
              void sendTextMessageRef.current(trimmedText);
            } else {
              // 兜底：前端发送通道未就绪时至少展示到 UI
              addUserMessageOnlyRef.current?.(trimmedText);
            }
          },
          // barge-in：用户开始说话即打断 AI 播报（随时打断）
          onUserSpeechInDialog: () => {
            console.log('[ContinuousSpeech] 检测到用户说话，打断 AI 播报 (barge-in)');
            if (isAiSpeakingRef.current) {
              sendInterrupt();
            }
          },
          audioDeviceId: selectedAudioDevice || undefined,
          // 持续识别 = 通话入口：开启即接听，长静默/说挂断词/停止即挂断
          callMode: true,
          callSilenceTimeoutMs: 40000,
          onCallStateChange: (active: boolean, reason?: string) => {
            setCallActive(active);
            console.log(`[持续识别通话] ${active ? '接听/通话中' : '挂断/结束'} (${reason})`);
          },
          // 接听/挂断提示音由监听器内部播放，这里仅记录日志
          onCallStarted: () => {
            console.log('[持续识别通话] 已接听');
          },
          onCallEnded: (reason: string) => {
            console.log(`[持续识别通话] 已挂断 (${reason})`);
            // 挂断时同步关闭「持续识别」按钮：reason==='stop' 是系统级 teardown
            // （如 WS 断线后 reconcile 调用 listener.stop()），不应翻掉按钮以免丢失
            // 「断线后自动恢复」的武装状态；其余挂断原因（external/voice/silence）才翻。
            if (reason !== 'stop') {
              setContinuousMode(false);
            }
          },
        });
        await sttContinuousRef.current.start();
        // 异步启动期间状态可能已变化，启动后二次确认
        if (!continuousModeRef.current) {
          console.log('[ContinuousSpeech] 启动完成后发现持续识别已关闭，立即停止');
          sttContinuousRef.current.stop();
          sttContinuousRef.current = null;
          return;
        }
      } catch (error) {
        console.error("[App] 持续语音识别启动失败:", error);
        sttContinuousRef.current = null;
      }
    } else {
      if (continuousSpeechListenerRef.current) return;
      try {
        continuousSpeechListenerRef.current = createContinuousSpeechListener({
          silenceSeconds: 3,
          debug: true,
          onResult: (text: string) => {
            // 过滤无效识别结果
            if (text.includes('未识别') || text.includes('识别失败') || !text.trim()) {
              console.log('[ContinuousSpeech] 跳过无效识别结果:', text);
              return;
            }
            // 持续语音识别消息去重
            const now = Date.now();
            const trimmedText = text.trim();
            if (
              lastContinuousSpeechTextRef.current === trimmedText &&
              now - lastContinuousSpeechTimeRef.current < 2000
            ) {
              console.log('[ContinuousSpeech] 跳过重复消息:', trimmedText);
              return;
            }
            lastContinuousSpeechTextRef.current = trimmedText;
            lastContinuousSpeechTimeRef.current = now;
            console.log("[ContinuousSpeech] 识别内容已发送:", trimmedText);
            void sendTextMessageRef.current?.(trimmedText);
          },
          audioDeviceId: selectedAudioDevice || undefined,
        });
        await continuousSpeechListenerRef.current.start();
        // 异步启动期间状态可能已变化，启动后二次确认
        if (!continuousModeRef.current) {
          console.log('[ContinuousSpeech] 启动完成后发现持续识别已关闭，立即停止');
          continuousSpeechListenerRef.current.stop();
          continuousSpeechListenerRef.current = null;
          return;
        }
      } catch (error) {
        console.error("[App] 持续语音识别启动失败:", error);
        continuousSpeechListenerRef.current = null;
      }
    }
  }, [selectedAudioDevice, sendTextMessageRef, addUserMessageOnlyRef, useBackendStt, sendAudioChunk, sendCloudStreamBegin, sendCloudStreamFrame, sendCloudStreamEnd]);

  const reconcileVoiceOrchestration = useCallback(async (reason = "sync") => {
    const plan = createVoiceOrchestrationPlan({
      wakeWordEnabled: wakeWordEnabledRef.current,
      continuousMode: continuousModeRef.current,
      wsReady: wsConnectedRef.current,
      aiSpeaking: isAiSpeakingRef.current,
      manualPause: continuousSpeechPausedRef.current,
      hasWakeWordListener: !!wakeWordListenerRef.current || !!sttWakeWordRef.current,
      hasContinuousSpeechListener: !!continuousSpeechListenerRef.current || !!sttContinuousRef.current,
      wakeWordSuspended: wakeWordSuspendedRef.current,
      cooking,
      reason,
    });

    if (plan.destroyWakeWord) {
      if (wakeWordListenerRef.current) {
        wakeWordListenerRef.current.destroy();
        wakeWordListenerRef.current = null;
      }
      if (sttWakeWordRef.current) {
        sttWakeWordRef.current.destroy();
        sttWakeWordRef.current = null;
      }
    }

    if (plan.stopContinuousSpeech) {
      if (sttContinuousRef.current) {
        sttContinuousRef.current.stop();
        sttContinuousRef.current = null;
      }
      if (continuousSpeechListenerRef.current) {
        continuousSpeechListenerRef.current.stop();
        continuousSpeechListenerRef.current = null;
      }
    }

    wakeWordSuspendedRef.current = plan.nextWakeWordSuspended;

    if (plan.kind !== "wakeword-active" && plan.resetWakeWordUi) {
      setWakeWordDetected(false);
      setWakeWordLiveTranscript("");
    }

    if (plan.action.type === "ensureContinuous") {
      // WS 未连接时不自动创建新的持续识别监听器（无 WS 无法发送音频）；
      // 已有监听器由下方 pause 逻辑挂起，等 WS 恢复后 resume。
      if (!continuousSpeechListenerRef.current && !sttContinuousRef.current && wsConnectedRef.current) {
        await setupContinuousSpeech();
      }

      if (!continuousSpeechListenerRef.current && !sttContinuousRef.current) {
        return;
      }

      if (plan.action.paused) {
        continuousSpeechListenerRef.current?.pause();
        sttContinuousRef.current?.pause();
      } else {
        try {
          await continuousSpeechListenerRef.current?.resume();
          await sttContinuousRef.current?.resume();
        } catch (error) {
          console.error("[App] 恢复持续识别失败:", error);
          return;
        }
      }

      setVoiceStatus(plan.status);
      if (plan.log) {
        logVoiceEvent(plan.log.key, plan.log.message, plan.log.level, plan.log.throttleMs);
      }
      return;
    }

    if (plan.action.type === "startWakeWord") {
      await setupWakeWordListener(plan.action.forceRestart);
      setWakeWordDetected(false);
      setWakeWordLiveTranscript("");
      if (plan.log) {
        logVoiceEvent(plan.log.key, plan.log.message, plan.log.level, plan.log.throttleMs);
      }
      return;
    }

    setVoiceStatus(plan.status);
    if (plan.log) {
      logVoiceEvent(plan.log.key, plan.log.message, plan.log.level, plan.log.throttleMs);
    }
  }, [
    isAiSpeakingRef,
    logVoiceEvent,
    setVoiceStatus,
    setWakeWordDetected,
    setWakeWordLiveTranscript,
    setupContinuousSpeech,
    setupWakeWordListener,
    stopContinuousSpeech,
    cooking,
  ]);

  useEffect(() => {
    reconcileVoiceOrchestrationRef.current = reconcileVoiceOrchestration;
  }, [reconcileVoiceOrchestration]);

  useEffect(() => {
    void reconcileVoiceOrchestration("init");
  }, [reconcileVoiceOrchestration]);

  useEffect(() => {
    return () => {
      clearWakeWordResumeTimer();
      wakeWordSuspendedRef.current = false;
      wsConnectedRef.current = false;
      wakeWordListenerRef.current?.destroy();
      sttWakeWordRef.current?.destroy();
      stopContinuousSpeech();
      setVoiceStatus(wakeWordEnabledRef.current ? "唤醒词监听已停止" : "唤醒词未开启");
      reconcileVoiceOrchestrationRef.current = null;
    };
  }, [clearWakeWordResumeTimer, setVoiceStatus, stopContinuousSpeech]);

  useEffect(() => {
    resumeVoiceOrchestration(0, "voice-deps-changed");
  }, [continuousMode, continuousSpeechPaused, isAiSpeaking, wakeWordEnabled, resumeVoiceOrchestration]);

  useEffect(() => {
    // 将 AI 播报状态推给端侧唤醒词监听器，用于精确计时连续对话窗口：
    // 从 AI 播报完成开始算 8s 窗口，用户可直接继续说，无需再唤醒词
    sttWakeWordRef.current?.notifyAiState?.(isAiSpeaking);
    // 同步给持续通话识别，启用回声门控（AI 播报期间抑制疑似回声推流）
    sttContinuousRef.current?.notifyAiState?.(isAiSpeaking);
  }, [isAiSpeaking]);

  const handleWsConnected = useCallback(() => {
    wsConnectedRef.current = true;
    resumeVoiceOrchestration(650, "ws-connected");
  }, [resumeVoiceOrchestration]);

  const handleWsDisconnected = useCallback(() => {
    wsConnectedRef.current = false;
    resumeVoiceOrchestration(0, "ws-disconnected");
  }, [resumeVoiceOrchestration]);

  const handleSttResult = useCallback((text: string, isPartial: boolean) => {
    console.log(`[STT] 后端识别结果: "${text}" (isPartial=${isPartial})`);
    // 将后端 STT 结果传递给当前活跃的控制器
    if (sttWakeWordRef.current) {
      sttWakeWordRef.current.handleSttResult(text, isPartial);
    }
    if (sttContinuousRef.current) {
      sttContinuousRef.current.handleSttResult(text, isPartial);
    }
  }, []);

  // 通话模式：挂断/结束通话（HUD 按钮或眼镜触摸手势触发），由持续识别控制器驱动
  const hangupCall = useCallback(() => {
    sttContinuousRef.current?.hangupCall();
  }, []);

  // 由后端唤醒词检测触发的流程
  const triggerWakeWordFromBackend = useCallback(() => {
    console.log('[后端唤醒词] 触发前端反馈流程');
    
    // 停止前端唤醒词检测（避免重复检测）
    sttWakeWordRef.current?.destroy();
    sttWakeWordRef.current = null;
    wakeWordListenerRef.current?.destroy();
    wakeWordListenerRef.current = null;
    
    clearWakeWordResumeTimer();
    wakeWordSuspendedRef.current = true;
    setVoiceStatus("后端检测到唤醒词");
    setWakeWordLiveTranscript("");
    stopBrowserTts();

    if (isAiSpeakingRef.current) {
      sendInterrupt();
    }

    setWakeWordDetected(true);

    // 播放 "你好我在！" 反馈
    console.log('[后端唤醒词反馈] 播放语音: 你好我在！');
    try {
      playWakeWordFeedback();
      // 流程级超时兜底（~5s）：反馈若被浏览器吞掉且 tts.ts 的 onstart 看门狗（3s）
      // 也未兜住（如 speechSynthesis 异常挂起、playWakeWordFeedback 内部延迟
      // 播放的定时器丢失），endCallback 永不到达会让本流程死等——超时后直接走
      // 与完成等价的收集恢复路径。正常完成时 endCallback 会清掉本定时器（防重入）
      if (wakeWordFeedbackTimeoutRef.current !== null) {
        clearTimeout(wakeWordFeedbackTimeoutRef.current);
      }
      wakeWordFeedbackTimeoutRef.current = window.setTimeout(() => {
        wakeWordFeedbackTimeoutRef.current = null;
        console.warn('[后端唤醒词反馈] 5s 未收到反馈播放完成回调，超时兜底直接恢复收集');
        setWakeWordDetected(false);
        if (!wakeWordEnabledRef.current && !continuousModeRef.current) {
          console.log('[后端唤醒词] 语音入口已关闭，跳过超时兜底恢复');
          return;
        }
        resumeVoiceOrchestration(100, "backend-wakeword-feedback-timeout");
      }, 5000);
    } catch (error) {
      console.error('[后端唤醒词反馈] 播放失败:', error);
      // 如果播放失败，直接开始收集（无需超时兜底）
      setVoiceStatus("请开始说话");
      setTimeout(() => {
        if (!wakeWordEnabledRef.current && !continuousModeRef.current) {
          console.log('[后端唤醒词] 语音入口已关闭，不重启编排');
          return;
        }
        resumeVoiceOrchestration(500, "backend-wakeword-feedback-failed");
      }, 100);
    }
  }, [clearWakeWordResumeTimer, setVoiceStatus, setWakeWordLiveTranscript, stopBrowserTts, sendInterrupt, setWakeWordDetected, resumeVoiceOrchestration]);

  return {
    resumeVoiceOrchestration,
    handleWsConnected,
    handleWsDisconnected,
    handleSttResult,
    triggerWakeWordFromBackend,
    callActive,
    hangupCall,
  };
}
