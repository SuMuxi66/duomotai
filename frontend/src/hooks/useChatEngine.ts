import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { MainContentRef } from "../components/MainContent";
import { ProviderId } from "../components/ProviderIcon";
import { ToastMessage } from "../components/Toast";
import { createContinuousRecorder, ContinuousRecorder } from "../lib/audio";
import { createAudioPlaybackController, type AudioPlaybackKind } from "../lib/audio-playback";
import { createTTSController, TTSController } from "../lib/tts";
import {
  createBrowserSpeechRecognition,
  BrowserSpeechRecognition,
  isSpeechRecognitionSupported,
} from "../lib/browser-speech-recognition";
import { createLatencyTracker, type LatencyRecord, type LatencyStage } from "../lib/latency-tracker";
import type { TemperatureData, WeightData } from "../lib/iot-controller";
import { analyzeLocalCommandText, buildLocalResponses } from "../lib/local-commands";
import { normalizeSttText } from "../lib/stt-normalize";
import { useWebSocket, type WebSocketConnectionState } from "./useWebSocket";
import { useVoiceOrchestration } from "./useVoiceOrchestration";
import { usePersistedState } from "./usePersistedState";
import { Msg, SpeechRecognitionState, AIResponseState } from "../types";
import { STORAGE_KEYS } from "../lib/storage";
import { stripMarkdown, getWsStateLabel } from "../lib/app-utils";
import type { UseCookingModeReturn } from "./useCookingMode";
import type { UseFanControlReturn } from "./useFanControl";
import type { UseDeviceManagerReturn } from "./useDeviceManager";
import type { UseModelConfigReturn } from "./useModelConfig";

const FAN_ENABLED = true;

export interface FileData {
  name: string;
  b64: string;
  type: string;
}

export interface UseChatEngineOptions {
  cooking: UseCookingModeReturn;
  sendCookingActionRef: React.MutableRefObject<((action: string, payload?: Record<string, unknown>) => void) | null>;
  fanControl: UseFanControlReturn;
  deviceMgr: UseDeviceManagerReturn;
  modelConfig: UseModelConfigReturn;
  mainContentRef: React.MutableRefObject<MainContentRef | null>;
  canSendRef: React.MutableRefObject<boolean>;
  sendWsEventRef: React.MutableRefObject<((type: string, payload: any) => void) | null>;
  connectionStateRef: React.MutableRefObject<WebSocketConnectionState>;
  reconnectWsRef: React.MutableRefObject<(() => void) | null>;
}

export interface UseChatEngineReturn {
  // Chat state
  messages: Msg[];
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>;
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  partialStt: string;
  setPartialStt: React.Dispatch<React.SetStateAction<string>>;
  aiResponseState: AIResponseState;
  setAiResponseState: React.Dispatch<React.SetStateAction<AIResponseState>>;
  speechRecognitionState: SpeechRecognitionState;
  isAiSpeaking: boolean;
  isListening: boolean;
  interruptMessage: string | null;
  // Vision / cooking scene
  latestVisionObservation: string;
  isCookingScene: boolean;
  cookingSuggestion: string;
  // Wake word
  wakeWordEnabled: boolean;
  setWakeWordEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  wakeWordDetected: boolean;
  wakeWordStatusText: string;
  wakeWordLiveTranscript: string;
  // Call mode（通话模式）
  callActive: boolean;
  hangupCall: () => void;
  // Continuous mode
  continuousMode: boolean;
  setContinuousMode: React.Dispatch<React.SetStateAction<boolean>>;
  continuousSpeechPaused: boolean;
  setContinuousSpeechPaused: React.Dispatch<React.SetStateAction<boolean>>;
  // UI state
  isTestingTts: boolean;
  showModelSelector: boolean;
  setShowModelSelector: React.Dispatch<React.SetStateAction<boolean>>;
  showPromptSettings: boolean;
  setShowPromptSettings: React.Dispatch<React.SetStateAction<boolean>>;
  chatPanelWidth: number;
  setChatPanelWidth: React.Dispatch<React.SetStateAction<number>>;
  viewportWidth: number;
  isRecording: boolean;
  // Toast
  toasts: ToastMessage[];
  showToast: (text: string, type?: ToastMessage["type"]) => void;
  dismissToast: (id: number) => void;
  // 危险警报大弹窗
  dangerAlert: { message: string; types: string[]; highEnergy: boolean; ts: number } | null;
  dismissDangerAlert: () => void;
  servingsCalc: { requestId: string; labels: string[]; results: (string | null)[] } | null;
  // Actions
  sendInterrupt: () => void;
  sendTextMessage: (text: string, files?: FileData[]) => Promise<void>;
  onSendText: (files?: FileData[]) => void;
  interruptAndContinue: () => void;
  testTts: () => Promise<void>;
  // WebSocket (for ref setting in App)
  wsRef: React.MutableRefObject<{ send: (type: string, payload: any) => void; isConnected: () => boolean } | null>;
  canSend: boolean;
  connectionState: WebSocketConnectionState;
  sendWsEvent: (type: string, payload: any) => void;
  connectWs: () => void;
  // Refs needed by App for JSX
  isAiSpeakingRef: React.MutableRefObject<boolean>;
}

/**
 * 聊天引擎 mega-hook
 * 封装 WebSocket 回调、流式状态、TTS、STT、sendTextMessage、聊天历史、recorder 等
 */
/** STT 状态/占位文案（不是识别内容，如"正在识别..."），不写入输入框覆盖实时转写文本 */
function isSttStatusText(t: string): boolean {
  return !t || t.includes("正在识别") || t.includes("未识别") || t.includes("识别失败") ||
    t.includes("未启用") || t.includes("没有接收") || t.startsWith("[Error") || t === "(Listening...)";
}

export function useChatEngine(options: UseChatEngineOptions): UseChatEngineReturn {
  const {
    cooking,
    sendCookingActionRef,
    fanControl,
    deviceMgr,
    modelConfig,
    mainContentRef,
    canSendRef,
    sendWsEventRef,
    connectionStateRef,
    reconnectWsRef,
  } = options;

  const latencyTracker = useMemo(() => createLatencyTracker(), []);
  const audioPlayback = useMemo(() => createAudioPlaybackController(), []);

  // ===== State =====
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [partialStt, setPartialStt] = useState("");

  // 流式更新节流相关
  const streamingUpdateThrottle = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingStreamingUpdate = useRef<{ delta: string; timestamp: number } | null>(null);
  const streamingStartedRef = useRef(false);
  const reasoningStartedRef = useRef(false);
  const reasoningThrottle = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingReasoningUpdate = useRef<{ reasoning: string; timestamp: number } | null>(null);

  const [speechRecognitionState, setSpeechRecognitionState] = useState<SpeechRecognitionState>('idle');
  const [aiResponseState, setAiResponseState] = useState<AIResponseState>('idle');
  const aiResponseStateRef = useRef<AIResponseState>('idle');
  useEffect(() => {
    aiResponseStateRef.current = aiResponseState;
  }, [aiResponseState]);

  const [isListening, setIsListening] = useState(false);

  const streamingTtsBuffer = useRef<string>('');
  const streamingTtsTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAssistantTextRef = useRef<string>('');
  const reasoningBufferRef = useRef<string>('');
  const reasoningCollapsedRequestIdsRef = useRef<Set<string>>(new Set());

  const [wakeWordEnabled, setWakeWordEnabled] = usePersistedState('multimodal_wake_word_enabled', true);
  // 追踪唤醒词关闭时间，用于忽略关闭后残余的 STT 结果
  const wakeWordDisabledAtRef = useRef<number>(0);
  // 追踪后端 STT 中间结果写入输入框的文本，用于防覆盖用户手打草稿与最终清理
  const sttPartialBoxRef = useRef<string>("");
  // 记录最近一次 stt_final 到达时间，防陈旧 partial 在 final 之后迟到写脏已清空的输入框
  const sttFinalAtRef = useRef<number>(0);
  // 记录浏览器实时转写会话最近一次输出时间：会话活跃期内 stt_final 不清输入框（收集期多短语累加显示）
  const liveSttAtRef = useRef<number>(0);
  const prevWakeWordEnabledRef = useRef(wakeWordEnabled);
  useEffect(() => {
    if (prevWakeWordEnabledRef.current && !wakeWordEnabled) {
      wakeWordDisabledAtRef.current = Date.now();
    }
    prevWakeWordEnabledRef.current = wakeWordEnabled;
  }, [wakeWordEnabled]);
  const [wakeWordDetected, setWakeWordDetected] = useState(false);
  const [wakeWordStatusText, setWakeWordStatusText] = useState("唤醒词未开启");
  const [wakeWordLiveTranscript, setWakeWordLiveTranscript] = useState("");

  const [continuousMode, setContinuousMode] = useState(false);
  const continuousModeRef = useRef(continuousMode);
  useEffect(() => {
    continuousModeRef.current = continuousMode;
  }, [continuousMode]);

  const isAiSpeakingRef = useRef(false);
  const ttsProviderRef = useRef<ProviderId>("browser");
  const useGlassesAudioRef = useRef(deviceMgr.useGlassesAudio);
  useEffect(() => { useGlassesAudioRef.current = deviceMgr.useGlassesAudio; }, [deviceMgr.useGlassesAudio]);
  const [isAiSpeaking, setIsAiSpeaking] = useState(false);

  const [continuousSpeechPaused, setContinuousSpeechPaused] = useState(false);

  const [isTestingTts, setIsTestingTts] = useState(false);
  const [showModelSelector, setShowModelSelector] = useState(false);
  const [showPromptSettings, setShowPromptSettings] = useState(false);

  const [chatPanelWidth, setChatPanelWidth] = usePersistedState(STORAGE_KEYS.chatPanelWidth, 420);
  const [, setIsResizingPanels] = useState(false);
  const [viewportWidth, setViewportWidth] = useState<number>(() => window.innerWidth);
  const panelResizeRef = useRef<HTMLDivElement>(null);
  const resizingRef = useRef(false);

  const [isRecording] = useState(false);

  const [latestVisionObservation, setLatestVisionObservation] = useState<string>("等待视觉分析...");
  const [isCookingScene, setIsCookingScene] = useState<boolean>(false);
  const [cookingSuggestion, setCookingSuggestion] = useState<string>("");

  const [interruptMessage, setInterruptMessage] = useState<string | null>(null);

  // Toast 通知管理
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  // 危险警报大弹窗状态 + 高能警笛控制
  const [dangerAlert, setDangerAlert] = useState<{
    message: string; types: string[]; highEnergy: boolean; ts: number;
  } | null>(null);
  const dangerAlertTimerRef = useRef<number | null>(null);
  const sirenStopRef = useRef<(() => void) | null>(null);

  const dismissDangerAlert = useCallback(() => {
    setDangerAlert(null);
    if (dangerAlertTimerRef.current) {
      clearTimeout(dangerAlertTimerRef.current);
      dangerAlertTimerRef.current = null;
    }
    if (sirenStopRef.current) {
      sirenStopRef.current();
      sirenStopRef.current = null;
    }
  }, []);

  const showDangerAlert = useCallback((message: string, types: string[], highEnergy: boolean) => {
    setDangerAlert({ message, types, highEnergy, ts: Date.now() });
    if (dangerAlertTimerRef.current) clearTimeout(dangerAlertTimerRef.current);
    dangerAlertTimerRef.current = window.setTimeout(() => dismissDangerAlert(), 8000);
  }, [dismissDangerAlert]);

  // 高能危险循环警笛：/bj.mp3 循环播放，随警报弹窗关闭（自动/手动）停止
  const startSiren = useCallback(() => {
    (async () => {
      try {
        const response = await fetch('/bj.mp3');
        const arrayBuffer = await response.arrayBuffer();
        const audioCtx = new AudioContext();
        const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
        const siren = { isPlaying: true, currentSource: null as AudioBufferSourceNode | null };
        sirenStopRef.current = () => {
          siren.isPlaying = false;
          try { siren.currentSource?.stop(); } catch {}
          siren.currentSource = null;
          try { audioCtx.close(); } catch {}
          sirenStopRef.current = null;
        };
        const playLoop = () => {
          if (!siren.isPlaying) return;
          const src = audioCtx.createBufferSource();
          src.buffer = audioBuffer;
          src.connect(audioCtx.destination);
          src.onended = () => { if (siren.isPlaying) playLoop(); };
          siren.currentSource = src;
          src.start(0);
        };
        playLoop();
        console.log('[统一危险检测] 高能警笛已启动');
      } catch (e) {
        console.error('[统一危险检测] 警笛音频加载失败', e);
      }
    })();
  }, []);
  // 智能食材计算进度（servings_calc_* 事件驱动，条目逐项揭示）
  const [servingsCalc, setServingsCalc] = useState<{
    requestId: string;
    labels: string[];
    results: (string | null)[];
  } | null>(null);
  const servingsCalcActiveRef = useRef(false);
  const toastIdRef = useRef(0);
  const showToast = useCallback((text: string, type: ToastMessage["type"] = "info") => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, text, type }]);
  }, []);
  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ===== Refs =====
  const ttsController = useRef<TTSController | null>(null);
  const continuousRecorder = useRef<ContinuousRecorder | null>(null);

  const lastSentMessageRef = useRef<{ text: string; time: number } | null>(null);
  const aiResponseTimeoutRef = useRef<number | null>(null);
  const aiRequestSeqRef = useRef(0);
  // 危险警报播报保护期：警报 TTS 会被扬声器→麦克风回采触发自动打断，
  // 导致警报语音刚开口就被掐断。警报到达后 8 秒内抑制自动打断
  const dangerAlarmGraceUntilRef = useRef(0);
  const activePlaybackRequestIdRef = useRef<string | null>(null);

  const processedResponsesRef = useRef<Set<string>>(new Set());
  const recentResponsesTimeWindowRef = useRef<Map<string, number>>(new Map());
  const RESPONSE_TIME_WINDOW_MS = 3000;

  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);

  const sendTextMessageRef = useRef<((text: string) => Promise<void>) | null>(null);

  const addUserMessageOnlyRef = useRef<((text: string) => void) | null>(null);
  useEffect(() => {
    addUserMessageOnlyRef.current = (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (isAiSpeakingRef.current) {
        ttsController.current?.stop();
        audioPlayback.stopAll();
        isAiSpeakingRef.current = false;
        setIsAiSpeaking(false);
      }
      setMessages((m) => [...m, { role: "user", text: trimmed, timestamp: Date.now() }]);
    };
  }, [setMessages]);

  const audioSeqCounter = useRef(0);

  const finalTranscriptRef = useRef('');
  const interimTranscriptRef = useRef('');
  const silenceTimerRef = useRef<number | null>(null);

  // ===== Effects =====
  useEffect(() => {
    ttsProviderRef.current = modelConfig.ttsProvider;
  }, [modelConfig.ttsProvider]);

  // 扬声器设备切换同步到 audioPlayback 控制器
  useEffect(() => {
    if (audioPlayback && typeof (audioPlayback as any).setOutputDevice === 'function') {
      (audioPlayback as any).setOutputDevice(deviceMgr.selectedAudioOutputDevice);
    }
  }, [deviceMgr.selectedAudioOutputDevice, audioPlayback]);

  // ===== Latency helpers =====
  const markLatencyRecord = useCallback((requestId: string, patch: Partial<LatencyRecord>) => {
    latencyTracker.mark(requestId, patch);
  }, [latencyTracker]);
  const markLatencyRecordFirst = useCallback((
    requestId: string,
    field: "firstTokenAt" | "finalTextAt" | "ttsStartedAt" | "ttsPayloadReadyAt"
  ) => {
    latencyTracker.markFirst(requestId, field);
  }, [latencyTracker]);
  const finalizeLatencyRecord = useCallback((requestId: string, reason: string) => {
    latencyTracker.finalize(requestId, reason);
  }, [latencyTracker]);
  const stopBrowserTts = useCallback(() => {
    ttsController.current?.stop();
  }, []);

  // ===== sendInterrupt =====
  const sendInterrupt = useCallback(() => {
    if (Date.now() < dangerAlarmGraceUntilRef.current) {
      console.log('[打断] 危险警报播报保护期内，抑制自动打断');
      return;
    }
    if (isAiSpeakingRef.current || aiResponseStateRef.current !== 'idle') {
      console.log('[打断] 检测到 AI 响应中，发送打断信号');
      if (wsRef.current) {
        wsRef.current.send("interrupt", {});
      }

      ttsController.current?.stop();
      audioPlayback.stopAll();
      // 打断后清空流式 TTS 缓冲，防止残留文本被追加进下一条回答开头（串话）
      streamingTtsBuffer.current = "";
      isAiSpeakingRef.current = false;
      setIsAiSpeaking(false);
      setAiResponseState('idle');
      servingsCalcActiveRef.current = false;
      setServingsCalc(null);

      setInterruptMessage('[已打断]');
      setTimeout(() => setInterruptMessage(null), 2000);

      setMessages((m) => {
        const last = m[m.length - 1];
        if (last?.role === "assistant_stream") {
          const mm = [...m];
          mm[mm.length - 1] = { role: "assistant", text: last.text + " [已打断]", timestamp: Date.now() };
          return mm;
        }
        return m;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ===== Voice orchestration =====
  const cookingVoiceContext = useMemo(() => ({
    enabled: cooking.isActive,
    state: cooking.cookingState,
    recipeName: cooking.currentRecipe?.name ?? null,
    currentStep: cooking.currentStep,
    currentServings: cooking.currentServings,
    awaitingServings: cooking.cookingState === "AWAITING_SERVINGS",
  }), [
    cooking.currentRecipe?.name,
    cooking.currentStep,
    cooking.currentServings,
    cooking.cookingState,
    cooking.isActive,
  ]);
  // 浏览器并行识别的实时转写上屏（仅显示；与 onSttPartial 写框逻辑一致，含防覆盖草稿/防陈旧）
  const notifyLiveSttText = useCallback((text: string) => {
    liveSttAtRef.current = Date.now();
    setPartialStt(text);
    if (isSttStatusText(text)) return;
    if (Date.now() - sttFinalAtRef.current < 800) return;
    if (input === "" || input === sttPartialBoxRef.current) {
      setInput(text);
      sttPartialBoxRef.current = text;
    }
  }, [input, setInput, setPartialStt]);
  // 供唤醒流程兜底：输入框当前若是实时转写文本（非用户手打），返回之用于"后端识别为空时回退发送"
  const getLiveSttBoxText = useCallback(() => {
    return input !== "" && input === sttPartialBoxRef.current ? input : "";
  }, [input]);
  const {
    resumeVoiceOrchestration,
    handleWsConnected,
    handleWsDisconnected,
    handleSttResult,
    triggerWakeWordFromBackend,
    callActive,
    hangupCall,
  } = useVoiceOrchestration({
    wakeWordEnabled,
    continuousMode,
    setContinuousMode,
    continuousSpeechPaused,
    isAiSpeaking,
    isAiSpeakingRef,
    selectedAudioDevice: deviceMgr.selectedAudioDevice,
    sendInterrupt,
    notifyLiveSttText,
    getLiveSttBoxText,
    sendTextMessageRef,
    addUserMessageOnlyRef,
    stopBrowserTts,
    setWakeWordDetected,
    setWakeWordLiveTranscript,
    setWakeWordStatusText,
    cooking: cookingVoiceContext,
    onCookingIntentDetected: (action?: "start" | "stop") => {
      // 默认 start：语音识别到做饭意图（未激活）或激活中明确"重新开始"时进入/重置做饭流程；
      // stop：激活中识别到明确的"退出/停止做饭"强意图时退出流程
      sendCookingActionRef.current?.(action || "start");
    },
    sttProvider: modelConfig.sttProvider,
    sttPreferCloud: modelConfig.sttPreferCloud,
    wsSendRef: sendWsEventRef,
  });

  // ===== Audio playback callbacks =====
  const handleAudioPlaybackComplete = useCallback((kind: AudioPlaybackKind) => {
    isAiSpeakingRef.current = false;
    setIsAiSpeaking(false);
    const requestId = activePlaybackRequestIdRef.current;
    if (requestId) {
      markLatencyRecord(requestId, { ttsPlaybackFinishedAt: Date.now() });
      finalizeLatencyRecord(requestId, kind === "pcm-stream" ? "pcm-playback-finished" : "tts-audio-finished");
      activePlaybackRequestIdRef.current = null;
    }
    resumeVoiceOrchestration(200, kind === "pcm-stream" ? "tts-pcm-finished" : "tts-audio-finished");
  }, [finalizeLatencyRecord, markLatencyRecord, resumeVoiceOrchestration]);
  useEffect(() => {
    audioPlayback.setCallbacks({
      onPlaybackComplete: handleAudioPlaybackComplete,
    });
    return () => {
      audioPlayback.setCallbacks({});
    };
  }, [audioPlayback, handleAudioPlaybackComplete]);
  useEffect(() => {
    return () => {
      void audioPlayback.destroy();
    };
  }, [audioPlayback]);
  // StrictMode 开发期：上面的卸载 cleanup 会 destroy 播放器（pcmDestroyed=true），
  // 重挂载后若不预热，PCM 只入队不播、无声也无完成回调（要等用户首次手动发消息
  // 才在 warmUp 里复活）。挂载即 warmUp：自动复活被 destroy 的控制器，无手势也安全
  useEffect(() => {
    void audioPlayback.warmUp();
  }, [audioPlayback]);

  // ===== interruptAndContinue =====
  const interruptAndContinue = useCallback(() => {
    if (!continuousMode) {
      return;
    }

    if (isAiSpeaking || isAiSpeakingRef.current) {
      sendInterrupt();
    }

    if (continuousSpeechPaused) {
      setContinuousSpeechPaused(false);
    }

    resumeVoiceOrchestration(0, "interrupt-and-continue");
  }, [continuousMode, continuousSpeechPaused, isAiSpeaking, resumeVoiceOrchestration, sendInterrupt]);

  // ===== sendTextMessage =====
  const sendTextMessage = useCallback(async (text: string, files?: FileData[]) => {
    const trimmedText = normalizeSttText(text.trim());
    if (!trimmedText && (!files || files.length === 0)) return;

    console.log(`[sendTextMessage] 收到消息: "${trimmedText}"`);

    // 用户手势上下文预热 AudioContext，规避自动播放策略导致 TTS 无声
    void audioPlayback.warmUp();

    if (isAiSpeakingRef.current) {
      console.log('[打断] 正在打断当前 TTS 播放');
      ttsController.current?.stop();
      audioPlayback.stopAll();
      isAiSpeakingRef.current = false;
      setIsAiSpeaking(false);
    }

    const now = Date.now();
    console.log(`[sendTextMessage] 防抖检查: lastText="${lastSentMessageRef.current?.text}", lastTime=${lastSentMessageRef.current?.time}, now=${now}, diff=${now - (lastSentMessageRef.current?.time || 0)}`);
    if (lastSentMessageRef.current &&
      lastSentMessageRef.current.text === trimmedText &&
      now - lastSentMessageRef.current.time < 2000) {
      console.log('[防抖] 跳过重复消息:', trimmedText);
      return;
    }
    lastSentMessageRef.current = { text: trimmedText, time: now };
    console.log(`[sendTextMessage] 发送消息: "${trimmedText}" files: ${files?.length || 0}`);

    const shouldRequestVision = modelConfig.visionEnabled;
    const requestId = latencyTracker.startRecord({
      prompt: trimmedText,
      scenario: shouldRequestVision ? "vision-assisted" : "text-only",
      ttsProvider: modelConfig.ttsProvider,
      startedAt: now,
    });

    aiRequestSeqRef.current += 1;
    const currentRequestSeq = aiRequestSeqRef.current;
    if (aiResponseTimeoutRef.current) {
      clearTimeout(aiResponseTimeoutRef.current);
      aiResponseTimeoutRef.current = null;
    }
    setAiResponseState('thinking');
    aiResponseTimeoutRef.current = window.setTimeout(() => {
      if (aiRequestSeqRef.current !== currentRequestSeq) return;
      setAiResponseState('idle');
      setMessages((m) => [
        ...m,
        { role: "system", text: "AI 响应超时，正在尝试重连服务，请稍后重试。", timestamp: Date.now() }
      ]);
      reconnectWsRef.current?.();
    }, 180000); // 3分钟超时
    setMessages((m) => [...m, { role: "user", text: trimmedText, timestamp: now }]);

    let frameB64: string | null = null;
    const temperatureData: TemperatureData | null = null;
    const weightData: WeightData | null = null;

    if (shouldRequestVision) {
      console.log('[视觉] 正在采集帧...');
      markLatencyRecord(requestId, { visionStartedAt: Date.now() });

      if (mainContentRef.current) {
        for (let attempt = 1; attempt <= 3; attempt++) {
          const result = await mainContentRef.current.captureNow();
          if (result?.b64) {
            frameB64 = result.b64;
            console.log(`[视觉] 帧采集成功（第 ${attempt} 次）`);
            break;
          }
          if (attempt < 3) {
            console.warn(`[视觉] 帧采集失败，准备重试（${attempt}/3）`);
            await new Promise((resolve) => setTimeout(resolve, 150));
          }
        }
      }

      markLatencyRecord(requestId, { visionFinishedAt: Date.now() });

      if (!frameB64) {
        console.warn('[视觉] 连续采集失败，本次将仅发送文本');
      }
    }

    const localCommandAnalysis = analyzeLocalCommandText(trimmedText, FAN_ENABLED);
    if (localCommandAnalysis.isCooking) {
      console.log('[炒菜场景检测] 检测到炒菜场景:', trimmedText);
      setIsCookingScene(true);
    } else {
      setIsCookingScene(false);
    }

    console.log("发送用户消息:", { requestId, text: trimmedText, hasVision: !!frameB64, visionSwitchEnabled: modelConfig.visionEnabled, visionRequested: shouldRequestVision, isCooking: localCommandAnalysis.isCooking, hasTemperature: !!temperatureData, hasWeight: !!weightData, weightData, vlm_model: modelConfig.vlmModel, tts_model: modelConfig.ttsModel, tts_provider: modelConfig.ttsProvider, customPrompt: modelConfig.customPrompt });

    const responses = buildLocalResponses(localCommandAnalysis, temperatureData);

    if (localCommandAnalysis.fanAction) {
      const nextFanStatus = await fanControl.controlFan(localCommandAnalysis.fanAction);
      if (nextFanStatus === null) {
        responses.splice(0, 1);
      }
    }

    if (responses.length > 0) {
      const responseText = responses.join('。');
      const now = Date.now();

      // 本地拦截回复不经后端 LLM 流式 TTS——非 browser 供应商（小米等后端 TTS）下会
      // 全程无声。显式请求后端按当前 TTS 供应商播报（browser 供应商走下方分块 speak，
      // 条件互斥避免同一句话播两遍）。
      if (modelConfig.ttsEnabled && ttsProviderRef.current !== "browser" && wsRef.current) {
        wsRef.current.send("tts_speak", { text: responseText });
      }

      setMessages(prev => [...prev, {
        role: 'assistant_stream',
        text: '',
        timestamp: now
      }]);

      let currentIndex = 0;
      const chunkSize = 5;

      const streamInterval = setInterval(() => {
        if (currentIndex >= responseText.length) {
          clearInterval(streamInterval);
          setMessages(prev => {
            const mm = [...prev];
            const last = mm[mm.length - 1];
            if (last?.role === "assistant_stream") {
              mm[mm.length - 1] = { role: "assistant", text: responseText, timestamp: now };
            }
            return mm;
          });
          setAiResponseState('idle');
          markLatencyRecord(requestId, { finalTextAt: Date.now() });
          if (!modelConfig.ttsEnabled) {
            finalizeLatencyRecord(requestId, "local-response-finished");
          }
          return;
        }

        const chunk = responseText.substring(currentIndex, currentIndex + chunkSize);
        currentIndex += chunkSize;

        setMessages(prev => {
          const mm = [...prev];
          const last = mm[mm.length - 1];
          if (last?.role === "assistant_stream") {
            mm[mm.length - 1] = { role: "assistant_stream", text: last.text + chunk, timestamp: now };
          }
          return mm;
        });

        const currentTtsProvider2 = ttsProviderRef.current;
        if (modelConfig.ttsEnabled && ttsController.current && currentTtsProvider2 === "browser" && !useGlassesAudioRef.current) {
          const cleanText = stripMarkdown(chunk);
          if (cleanText) {
            activePlaybackRequestIdRef.current = requestId;
            markLatencyRecordFirst(requestId, "ttsStartedAt");
            ttsController.current.speak(cleanText);
          }
        }
      }, 50);

      return;
    }

    if (canSendRef.current && wsRef.current) {
      markLatencyRecord(requestId, { sentAt: Date.now() });
      const messagePayload: Record<string, any> = {
        request_id: requestId,
        client_started_at: now,
        client_sent_at: Date.now(),
        text: trimmedText,
        has_vision: !!frameB64,
        // 视觉分析开关开启 = 每条消息都请求视觉注入（2026-09-07 用户决策：
        // 开了视觉按钮就必须带图，后端对附带帧的请求一律注入画面）
        vision_requested: shouldRequestVision,
        temperature: temperatureData,
        weight: weightData,
        vlm_model: modelConfig.vlmModel,
        tts_model: modelConfig.ttsModel,
        tts_provider: modelConfig.ttsProvider,
        custom_prompt: modelConfig.customPrompt,
      };

      // Attach vision frame if available
      if (frameB64) {
        messagePayload.jpeg_b64 = frameB64;
      }

      // Attach file uploads if available
      if (files && files.length > 0) {
        messagePayload.file_b64 = files;
      }

      sendWsEventRef.current?.("user_text", messagePayload);
    } else {
      console.error('WebSocket 未连接，触发重连');
      setAiResponseState('idle');
      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }
      setMessages((m) => [...m, { role: "system", text: `连接尚未就绪（${getWsStateLabel(connectionStateRef.current)}），正在自动重连，请稍后再试。`, timestamp: Date.now() }]);
      finalizeLatencyRecord(requestId, "ws-not-ready");
      reconnectWsRef.current?.();
    }
  }, [
    modelConfig.apiKeys, modelConfig.customPrompt, modelConfig.ttsEnabled, modelConfig.ttsProvider,
    modelConfig.ttsModel, modelConfig.visionEnabled, modelConfig.vlmModel,
    audioPlayback, fanControl, finalizeLatencyRecord, latencyTracker, markLatencyRecord,
    markLatencyRecordFirst, mainContentRef, canSendRef, sendWsEventRef, connectionStateRef, reconnectWsRef,
  ]);

  useEffect(() => {
    sendTextMessageRef.current = sendTextMessage;
  }, [sendTextMessage]);

  // ===== Vision sync effects =====
  useEffect(() => {
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", { action: "set_vision", enabled: modelConfig.visionEnabled, fps: modelConfig.visionFps });
    }
  }, [modelConfig.visionEnabled, modelConfig.visionFps, canSendRef, sendWsEventRef]);

  useEffect(() => {
    if (canSendRef.current && modelConfig.visionEnabled) {
      sendWsEventRef.current?.("control", { action: "set_vision", enabled: modelConfig.visionEnabled, fps: modelConfig.visionFps });
    }
  }, [modelConfig.visionEnabled, modelConfig.visionFps, canSendRef, sendWsEventRef]);

  // 定期采集帧并发送到后端进行危险检测（每30秒）
  useEffect(() => {
    console.log('[危险检测] useEffect 触发, visionEnabled:', modelConfig.visionEnabled, 'canSendRef.current:', canSendRef.current);

    // 只以 visionEnabled 决定是否安装定时器；WS 未就绪时由每次抓帧前的守卫跳过，
    // 避免页面加载早于 WS 连接、canSendRef 迟到导致定时器永不启动
    if (!modelConfig.visionEnabled) {
      console.log('[危险检测] 跳过: visionEnabled=false');
      return;
    }

    // 检测开关全关：危险报警与油烟检测都不跑，无需采集帧（省带宽，后端也不再调 VLM）
    if (!modelConfig.dangerAlertEnabled && !modelConfig.oilSmokeAlertEnabled) {
      console.log('[危险检测] 跳过: 危险报警与油烟检测均已关闭');
      return;
    }

    const captureAndSendFrame = async () => {
      console.log('[危险检测] captureAndSendFrame 执行, mainContentRef.current:', !!mainContentRef.current);
      if (!mainContentRef.current || !canSendRef.current) return;

      try {
        console.log('[危险检测] 开始采集帧...');
        const result = await mainContentRef.current.captureNow();
        console.log('[危险检测] 采集结果:', result);
        if (result?.b64) {
          console.log('[危险检测] 发送帧到后端分析');
          sendWsEventRef.current?.("video_frame", {
            jpeg_b64: result.b64,
            frame_id: Date.now(),
            timestamp: Date.now(),
          });
        }
      } catch (e) {
        console.error('[危险检测] 采集帧失败:', e);
      }
    };

    captureAndSendFrame();

    // 按配置的视觉分析帧率抓帧（visionFps=0.2 → 每 5 秒一帧），下限 1 秒防刷帧
    const fps = Number(modelConfig.visionFps) > 0 ? Number(modelConfig.visionFps) : 0.2;
    const intervalMs = Math.max(1000, Math.round(1000 / fps));
    const intervalId = setInterval(captureAndSendFrame, intervalMs);

    return () => clearInterval(intervalId);
  }, [modelConfig.visionEnabled, modelConfig.visionFps, modelConfig.dangerAlertEnabled, modelConfig.oilSmokeAlertEnabled, canSendRef, sendWsEventRef, mainContentRef]);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ===== Panel resize =====
  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!resizingRef.current || !panelResizeRef.current) return;
      const rect = panelResizeRef.current.getBoundingClientRect();
      const minLeft = 320;
      const minRight = 520;
      const next = Math.max(minLeft, Math.min(e.clientX - rect.left, rect.width - minRight));
      setChatPanelWidth(next);
    };

    const onMouseUp = () => {
      if (!resizingRef.current) return;
      resizingRef.current = false;
      setIsResizingPanels(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [setChatPanelWidth]);

  // ===== TTS controller init =====
  useEffect(() => {
    ttsController.current = createTTSController(
      { rate: 1, pitch: 1, volume: 1, lang: 'zh-CN' },
      () => {
        isAiSpeakingRef.current = false;
        setIsAiSpeaking(false);
        console.log('[TTS] 播放完成');
        const requestId = activePlaybackRequestIdRef.current;
        if (requestId) {
          markLatencyRecord(requestId, { ttsPlaybackFinishedAt: Date.now() });
          finalizeLatencyRecord(requestId, "browser-tts-finished");
          activePlaybackRequestIdRef.current = null;
        }
        resumeVoiceOrchestration(200);
      }
    );
    ttsController.current.onStart = () => {
      const requestId = latencyTracker.getLatestRequestId();
      if (!requestId) {
        return;
      }
      activePlaybackRequestIdRef.current = requestId;
      markLatencyRecordFirst(requestId, "ttsStartedAt");
    };
    console.log('[TTS] 控制器已初始化, ttsEnabled:', modelConfig.ttsEnabled, 'ttsProvider:', modelConfig.ttsProvider);

    return () => {
      ttsController.current?.stop();
    };
  }, [latencyTracker, markLatencyRecord, markLatencyRecordFirst, resumeVoiceOrchestration, modelConfig.ttsEnabled, modelConfig.ttsProvider]);

  // ===== Browser STT setup =====
  useEffect(() => {
    if (speechRecognitionRef.current && modelConfig.sttProvider !== 'browser') {
      speechRecognitionRef.current.stop();
      speechRecognitionRef.current = null;
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
    }

    if (modelConfig.sttProvider === 'browser') {
      if (!isSpeechRecognitionSupported()) {
        console.warn('[Browser STT] Browser does not support speech recognition, using backend STT');
        modelConfig.setSttProvider('siliconflow');
        return;
      }

      speechRecognitionRef.current = createBrowserSpeechRecognition(
        {
          lang: 'zh-CN',
          continuous: true,
          interimResults: true,
          debug: true,
        },
        {
          onResult: (result) => {
            const text = (result.transcript || '').trim();
            if (!text) return;

            if (result.isFinal) {
              finalTranscriptRef.current = `${finalTranscriptRef.current}${text}`;
              interimTranscriptRef.current = '';
            } else {
              interimTranscriptRef.current = text;
            }

            const combinedText = `${finalTranscriptRef.current}${interimTranscriptRef.current}`.trim();
            setInput(combinedText);

            if (speechRecognitionState !== 'listening') {
              setSpeechRecognitionState('listening');
            }

            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }

            if (result.isFinal) {
              setSpeechRecognitionState('idle');
            } else {
              silenceTimerRef.current = window.setTimeout(() => {
                const textToSend = `${finalTranscriptRef.current}${interimTranscriptRef.current}`.trim();
                if (textToSend) {
                  setSpeechRecognitionState('analyzing');
                  setSpeechRecognitionState('idle');
                }
              }, 3000);
            }
          },
          onError: (error) => {
            setPartialStt(`[Error: ${error}]`);
            setTimeout(() => setPartialStt(''), 2000);
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          },
          onEnd: () => {
            setPartialStt('');

            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          },
          onStart: () => {
            setSpeechRecognitionState('waiting');
            setPartialStt('');
            finalTranscriptRef.current = '';
            interimTranscriptRef.current = '';

            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          },
        }
      );

      return () => {
        speechRecognitionRef.current?.stop();
        speechRecognitionRef.current = null;
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
        }
      };
    }

    return () => {
      if (speechRecognitionRef.current) {
        speechRecognitionRef.current.stop();
        speechRecognitionRef.current = null;
      }
    };
  }, [modelConfig.sttProvider, speechRecognitionState, modelConfig]);

  // ===== testTts =====
  const testTts = useCallback(async () => {
    if (isTestingTts) return;

    setIsTestingTts(true);
    const testText = "你好，这是语音合成测试。一，二，三，测试完成。";

    try {
      const currentTtsProvider = ttsProviderRef.current;
      const glassesMode = useGlassesAudioRef.current;

      if (glassesMode) {
        // 眼镜扬声器模式：强制走后端 TTS，让后端转发 PCM 到眼镜
        console.log(`[测试 TTS] 眼镜扬声器模式，发送 tts_test 到后端 (provider=${currentTtsProvider})`);
        if (wsRef.current) {
          wsRef.current.send("tts_test", { text: testText, voice: modelConfig.ttsVoice });
        } else {
          console.error('[测试 TTS] WebSocket 未初始化');
          setMessages((m) => [...m, { role: "system", text: "错误：WebSocket 连接未初始化，请刷新页面重试", timestamp: Date.now() }]);
        }
      } else if (currentTtsProvider === "browser" && ttsController.current) {
        ttsController.current.speak(stripMarkdown(testText));
        console.log('[测试 TTS] 浏览器 TTS 已启动');
      } else if (currentTtsProvider !== "browser") {
        console.log(`[测试 TTS] ${currentTtsProvider} TTS 发送 API 请求...`);

        if (wsRef.current) {
          const testMessage = { text: testText, voice: modelConfig.ttsVoice };
          wsRef.current.send("tts_test", testMessage);
          console.log('[测试 TTS] 消息已发送');
        } else {
          console.error('[测试 TTS] WebSocket 未初始化');
          setMessages((m) => [...m, { role: "system", text: "错误：WebSocket 连接未初始化，请刷新页面重试", timestamp: Date.now() }]);
        }
      }
    } catch (e) {
      console.error('[测试 TTS] 失败:', e);
      setMessages((m) => [...m, { role: "system", text: `TTS 测试失败: ${String(e)}`, timestamp: Date.now() }]);
    } finally {
      setTimeout(() => setIsTestingTts(false), 2000);
    }
  }, [isTestingTts, modelConfig.ttsVoice]);

  // ===== WebSocket callbacks =====
  const { wsRef, canSend, connectionState, send: sendWsEvent, connect: connectWs, reconnect: reconnectWs } = useWebSocket({
    onVisionObservation: (payload) => {
      const displayText = `问题: ${payload.user_question}\n答案: ${payload.text}`;
      setLatestVisionObservation(displayText);
    },
    onVisionAnalysis: (payload) => {
      const analysis = payload.analysis || "";
      if (analysis) {
        console.log(`[视觉分析] ${analysis}`);
      }
    },
    onAssistantReasoningDelta: (reasoning, payload) => {
      if (!reasoning) return;
      const requestId = payload?.request_id as string | undefined;
      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
      }

      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }

      if (!reasoningStartedRef.current) {
        reasoningStartedRef.current = true;
        setAiResponseState('thinking');
        isAiSpeakingRef.current = true;
        setIsAiSpeaking(true);
      }

      reasoningBufferRef.current += reasoning;
      const now = Date.now();

      if (pendingReasoningUpdate.current) {
        pendingReasoningUpdate.current.reasoning += reasoning;
        pendingReasoningUpdate.current.timestamp = now;
      } else {
        pendingReasoningUpdate.current = { reasoning, timestamp: now };
      }

      if (!reasoningThrottle.current) {
        reasoningThrottle.current = setTimeout(() => {
          if (pendingReasoningUpdate.current) {
            const { reasoning: pendingReasoning } = pendingReasoningUpdate.current;
            pendingReasoningUpdate.current = null;
            const shouldCollapse = requestId ? reasoningCollapsedRequestIdsRef.current.has(requestId) : false;

            setMessages((m) => {
              const mm = [...m];
              const existingIndex = requestId
                ? mm.findIndex((msg) => msg.requestId === requestId && (msg.role === "assistant" || msg.role === "assistant_stream"))
                : -1;
              const current = existingIndex >= 0 ? mm[existingIndex] : undefined;

              if (current?.role === "assistant_stream" && current.reasoning) {
                mm[existingIndex] = {
                  ...current,
                  reasoning: (current.reasoning || "") + pendingReasoning,
                  reasoningExpanded: shouldCollapse ? false : true,
                  requestId: requestId || current.requestId,
                  timestamp: current.timestamp || now,
                };
                return mm;
              }

              return [...m, {
                role: 'assistant_stream',
                text: '',
                requestId,
                reasoning: reasoningBufferRef.current,
                reasoningExpanded: shouldCollapse ? false : true,
                timestamp: now,
              }];
            });
          }
          reasoningThrottle.current = null;
        }, 50);
      }
    },
    onAssistantDelta: (d, payload) => {
      if (!d) return;
      // 智能计算卡片：正文开始流式输出时收起
      if (servingsCalcActiveRef.current) {
        servingsCalcActiveRef.current = false;
        setServingsCalc(null);
      }
      const requestId = payload?.request_id as string | undefined;
      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        const record = latencyTracker.getRecord(requestId);
        if (record && !record.firstTokenAt) {
          markLatencyRecordFirst(requestId, "firstTokenAt");
        }
      }
      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }
      if (!streamingStartedRef.current) {
        streamingStartedRef.current = true;
        setAiResponseState('responding');
        isAiSpeakingRef.current = true;
        setIsAiSpeaking(true);
      }

      if (requestId) {
        reasoningCollapsedRequestIdsRef.current.add(requestId);
      }

      const now = Date.now();
      if (pendingStreamingUpdate.current) {
        pendingStreamingUpdate.current.delta += d;
        pendingStreamingUpdate.current.timestamp = now;
      } else {
        pendingStreamingUpdate.current = { delta: d, timestamp: now };
      }

      if (!streamingUpdateThrottle.current) {
        streamingUpdateThrottle.current = setTimeout(() => {
          if (pendingStreamingUpdate.current) {
            const { delta } = pendingStreamingUpdate.current;
            pendingStreamingUpdate.current = null;

            setMessages((m) => {
              const mm = [...m];
              let targetIndex = requestId
                ? mm.findIndex((msg) => msg.requestId === requestId && msg.role === "assistant_stream")
                : -1;

              if (targetIndex === -1) {
                for (let i = mm.length - 1; i >= 0; i--) {
                  if (mm[i].role === "assistant_stream") {
                    targetIndex = i;
                    break;
                  }
                }
              }

              // 只允许续写未完成的流式消息；上一条已定稿（assistant）时必须新开气泡，
              // 否则连续多条预设引导消息（无 request_id）会被拼进上一个气泡
              const lastStream = targetIndex >= 0 ? mm[targetIndex] : undefined;
              if (!lastStream || lastStream.role !== "assistant_stream") {
                return [...m, { role: "assistant_stream" as const, text: delta, requestId, timestamp: Date.now() }];
              }
              const last = lastStream;

              const shouldCollapse = requestId && reasoningCollapsedRequestIdsRef.current.has(requestId);
              if (shouldCollapse) {
                reasoningCollapsedRequestIdsRef.current.delete(requestId);
              }
              const nextMessage: Msg = {
                ...last,
                role: "assistant_stream",
                text: (last.text || "") + delta,
                reasoningExpanded: shouldCollapse ? false : last.reasoningExpanded,
                timestamp: last.timestamp || Date.now(),
                requestId: requestId || last.requestId,
              };
              if (targetIndex >= 0) {
                mm[targetIndex] = nextMessage;
                return mm;
              }
              return [...m, nextMessage];
            });
          }
          streamingUpdateThrottle.current = null;
        }, 50);
      }

      if (modelConfig.ttsEnabled && ttsController.current) {
        streamingTtsBuffer.current += d;

        if (streamingTtsTimeout.current) {
          clearTimeout(streamingTtsTimeout.current);
        }

        const buffer = streamingTtsBuffer.current;
        const currentTtsProvider = ttsProviderRef.current;
        console.log('[流式 TTS] 检查触发条件, buffer长度:', buffer.length, 'ttsProvider:', currentTtsProvider, 'delta:', d);

        const punctuations = ['。', '！', '？', '.', '!', '?'];
        let lastPunctIndex = -1;
        for (const p of punctuations) {
          const idx = buffer.lastIndexOf(p);
          if (idx > lastPunctIndex) lastPunctIndex = idx;
        }

        if (lastPunctIndex >= 0 || buffer.length >= 50) {
          const textToSpeak = lastPunctIndex >= 0 ? buffer.substring(0, lastPunctIndex + 1) : buffer;
          const remaining = lastPunctIndex >= 0 ? buffer.substring(lastPunctIndex + 1) : "";
          console.log('[流式 TTS] 触发，发送长度:', textToSpeak.length, '保留长度:', remaining.length);
          const cleanText = stripMarkdown(textToSpeak);
          if (cleanText && currentTtsProvider === "browser" && !useGlassesAudioRef.current) {
            console.log('[流式 TTS] 浏览器 TTS 播放:', cleanText.substring(0, 50));
            if (requestId) {
              activePlaybackRequestIdRef.current = requestId;
              markLatencyRecordFirst(requestId, "ttsStartedAt");
            }
            ttsController.current.speak(cleanText);
            streamingTtsBuffer.current = remaining;
          } else if (cleanText && currentTtsProvider !== "browser") {
            // 后端流式 TTS 已接管：音频经 tts_pcm/pcm_chunk 逐块回前端播放，
            // 前端句子切分播报只服务 browser 提供商——清掉缓冲避免无限增长与日志刷屏
            streamingTtsBuffer.current = "";
          }
        } else {
          streamingTtsTimeout.current = setTimeout(() => {
            if (streamingTtsBuffer.current) {
              console.log('[流式 TTS] 超时触发，文本长度:', streamingTtsBuffer.current.length);
              const cleanText = stripMarkdown(streamingTtsBuffer.current);
              if (cleanText && currentTtsProvider === "browser" && !useGlassesAudioRef.current) {
                console.log('[流式 TTS] 浏览器 TTS 播放(超时):', cleanText.substring(0, 50));
                if (requestId) {
                  activePlaybackRequestIdRef.current = requestId;
                  markLatencyRecordFirst(requestId, "ttsStartedAt");
                }
                ttsController.current?.speak(cleanText);
              }
              streamingTtsBuffer.current = "";
            }
          }, 1500);
        }
      } else {
        console.log('[流式 TTS] 跳过 TTS, ttsEnabled:', modelConfig.ttsEnabled, 'ttsController.current:', !!ttsController.current);
      }
    },
    onAssistantFinal: (finalText, payload) => {
      const requestId = payload?.request_id as string | undefined;
      const reasoning = typeof payload?.reasoning === "string" ? payload.reasoning : "";

      const responseKey = `${requestId}-${finalText}`;
      if (processedResponsesRef.current.has(responseKey)) {
        console.log('[App 去重] 跳过重复的最终响应:', finalText?.substring(0, 50));
        return;
      }

      const textKey = `text-${finalText}`;
      const now = Date.now();
      const lastResponseTime = recentResponsesTimeWindowRef.current.get(textKey);

      if (lastResponseTime && (now - lastResponseTime) < RESPONSE_TIME_WINDOW_MS) {
        console.log('[App 时间窗口去重] 跳过短时间内的重复响应:', finalText?.substring(0, 50));
        return;
      }

      processedResponsesRef.current.add(responseKey);
      recentResponsesTimeWindowRef.current.set(textKey, now);

      if (processedResponsesRef.current.size > 100) {
        const iterator = processedResponsesRef.current.values();
        const firstValue = iterator.next().value;
        if (firstValue) {
          processedResponsesRef.current.delete(firstValue);
        }
      }

      setTimeout(() => {
        recentResponsesTimeWindowRef.current.delete(textKey);
      }, 10000);

      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        // 必须先记录 finalTextAt，否则下方计算 reasoningDurationMs 时取不到结束时间
        markLatencyRecordFirst(requestId, "finalTextAt");
      }

      const requestRecord = requestId ? latencyTracker.getRecord(requestId) : undefined;
      const reasoningDurationMs = requestRecord?.finalTextAt && requestRecord?.llmStartedAt
        ? Math.max(0, requestRecord.finalTextAt - requestRecord.llmStartedAt)
        : requestRecord?.finalTextAt && requestRecord?.sentAt
          ? Math.max(0, requestRecord.finalTextAt - requestRecord.sentAt)
          : undefined;

      if (finalText) {
        lastAssistantTextRef.current = finalText;
      }
      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }
      const pendingDeltaText = pendingStreamingUpdate.current?.delta || "";
      if (streamingUpdateThrottle.current) {
        clearTimeout(streamingUpdateThrottle.current);
        streamingUpdateThrottle.current = null;
      }
      pendingStreamingUpdate.current = null;
      setAiResponseState('idle');
      reasoningBufferRef.current = '';
      streamingStartedRef.current = false;
      reasoningStartedRef.current = false;
      if (reasoningThrottle.current) {
        clearTimeout(reasoningThrottle.current);
        reasoningThrottle.current = null;
      }
      pendingReasoningUpdate.current = null;

      if (isCookingScene && finalText) {
        setCookingSuggestion(finalText);
      }

      setMessages((m) => {
        const mm = [...m];
        let targetIndex = requestId
          ? mm.findIndex((msg) => msg.requestId === requestId && (msg.role === "assistant" || msg.role === "assistant_stream"))
          : -1;

        if (targetIndex === -1) {
          targetIndex = mm.findIndex((msg) => msg.role === "assistant_stream");
        }

        if (targetIndex === -1) {
          targetIndex = mm.length - 1;
        }

        const last = targetIndex >= 0 ? mm[targetIndex] : undefined;
        const now = Date.now();

        if (last?.role === "assistant" && last.text === finalText && (now - (last.timestamp || 0)) < RESPONSE_TIME_WINDOW_MS) {
          console.log('[App UI去重] 跳过重复的assistant消息（UI层）');
          return m;
        }

        if (last?.role === "assistant_stream") {
          if (finalText) {
            mm[targetIndex] = { role: "assistant", text: finalText, requestId, reasoning: reasoning || last.reasoning || reasoningBufferRef.current, reasoningExpanded: last.reasoningExpanded, reasoningDurationMs, timestamp: last.timestamp || now };
          } else {
            mm[targetIndex] = { role: "assistant", text: pendingDeltaText || last.text, requestId, reasoning: reasoning || last.reasoning || reasoningBufferRef.current, reasoningExpanded: last.reasoningExpanded, reasoningDurationMs, timestamp: last.timestamp || now };
          }
        } else if (finalText || pendingDeltaText) {
          mm.push({ role: "assistant", text: finalText || pendingDeltaText, requestId, reasoning: reasoning || reasoningBufferRef.current, reasoningExpanded: false, reasoningDurationMs, timestamp: now });
        }

        return mm;
      });

      if (streamingTtsTimeout.current) {
        clearTimeout(streamingTtsTimeout.current);
        streamingTtsTimeout.current = null;
      }

      const currentTtsProvider2 = ttsProviderRef.current;
      if (modelConfig.ttsEnabled && ttsController.current && streamingTtsBuffer.current) {
        console.log('[流式 TTS] 处理剩余文本，长度:', streamingTtsBuffer.current.length);
        const cleanText = stripMarkdown(streamingTtsBuffer.current);
        if (cleanText && currentTtsProvider2 === "browser" && !useGlassesAudioRef.current) {
          isAiSpeakingRef.current = true;
          setIsAiSpeaking(true);
          if (requestId) {
            activePlaybackRequestIdRef.current = requestId;
            markLatencyRecordFirst(requestId, "ttsStartedAt");
          }
          ttsController.current.speak(cleanText);
        }
        streamingTtsBuffer.current = "";
      } else if (!(modelConfig.ttsEnabled && currentTtsProvider2 !== "browser")) {
        isAiSpeakingRef.current = false;
        setIsAiSpeaking(false);
        if (requestId) {
          finalizeLatencyRecord(requestId, "assistant-final-no-tts");
        }
        resumeVoiceOrchestration(200, "assistant-final");
      }

      if (modelConfig.ttsEnabled && currentTtsProvider2 !== "browser") {
        // 眼镜音频模式不经 audioPlayback 本地播放，hasPendingPcmAudio 判断不了它，维持原行为
        if (useGlassesAudioRef.current || audioPlayback.hasPendingPcmAudio()) {
          isAiSpeakingRef.current = true;
          setIsAiSpeaking(true);
        } else {
          // 本轮 TTS 已播完或根本没有音频：立即复位，避免回声门控长期压麦、流程卡死
          isAiSpeakingRef.current = false;
          setIsAiSpeaking(false);
          if (requestId) {
            finalizeLatencyRecord(requestId, "assistant-final-no-tts");
          }
          resumeVoiceOrchestration(200, "assistant-final");
        }
      }
    },
    onTtsError: (payload) => {
      const requestId = payload?.request_id as string | undefined;
      const chunkCount = Number(payload?.chunk_count ?? 0);
      const fallbackText = stripMarkdown((payload?.text as string | undefined) || lastAssistantTextRef.current || "");
      console.warn('[TTS] 后端 TTS 失败，准备降级播放', payload);

      // 已播出部分音频：后端修复后会补发 tts_pcm_end 走正常收尾路径，
      // 由 onPlaybackComplete 复位；这里不能抢着复位（可能正播到一半）
      if (chunkCount > 0) {
        return;
      }

      // 完全没播出任何内容，且无法降级（TTS 关闭 / 控制器缺失 / 无可降级文本）：
      // 之后不会有任何收尾事件来救，必须就地复位，否则 isAiSpeaking 卡 true、回声门控长期压麦
      if (!modelConfig.ttsEnabled || !ttsController.current || !fallbackText) {
        isAiSpeakingRef.current = false;
        setIsAiSpeaking(false);
        if (requestId) {
          finalizeLatencyRecord(requestId, "tts-error-no-audio");
        }
        resumeVoiceOrchestration(200, "tts-error");
        return;
      }

      // 眼镜音频模式下不降级到浏览器 TTS
      if (useGlassesAudioRef.current) {
        console.log('[TTS] 眼镜音频模式，跳过浏览器降级播放');
        return;
      }

      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        activePlaybackRequestIdRef.current = requestId;
        markLatencyRecordFirst(requestId, "ttsStartedAt");
      }

      isAiSpeakingRef.current = true;
      setIsAiSpeaking(true);
      audioPlayback.stopAll();
      ttsController.current.speak(fallbackText);
    },
    onTtsAudio: (wavB64, _chunkIndex, _totalChunks, payload) => {
      if (!wavB64) return;
      const requestId = payload?.request_id as string | undefined;
      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        activePlaybackRequestIdRef.current = requestId;
        markLatencyRecordFirst(requestId, "ttsPayloadReadyAt");
        markLatencyRecordFirst(requestId, "ttsStartedAt");
      }
      // 不再打印块级"收到音频块"日志（仅保留播放完成汇总）
      isAiSpeakingRef.current = true;
      setIsAiSpeaking(true);
      if (deviceMgr.useGlassesAudio) {
        console.log('[TTS] 眼镜音频模式，跳过本地播放');
        return;
      }
      try {
        audioPlayback.enqueueWavBase64(wavB64);
      } catch (e) {
        console.error("TTS audio playback error:", e);
      }
    },
    onTtsPcm: (pcmB64, sampleRate, chunkIndex, payload) => {
      if (!pcmB64) return;
      const requestId = payload?.request_id as string | undefined;
      const ttsText = typeof payload?.tts_text === "string" ? payload.tts_text : "";
      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        activePlaybackRequestIdRef.current = requestId;
        markLatencyRecordFirst(requestId, "ttsPayloadReadyAt");
        markLatencyRecordFirst(requestId, "ttsStartedAt");
      }
      // 不再打印块级"收到 PCM 块"日志（仅保留播放完成汇总）
      isAiSpeakingRef.current = true;
      setIsAiSpeaking(true);
      if (deviceMgr.useGlassesAudio) {
        console.log('[TTS] 眼镜音频模式，跳过本地播放');
        return;
      }
      void audioPlayback.playPcmChunk(pcmB64, sampleRate, requestId, chunkIndex, ttsText).catch((e) => {
        console.error("TTS PCM playback error:", e);
      });
    },
    onTtsPcmEnd: (totalChunks, _sampleRate, payload) => {
      const requestId = payload?.request_id as string | undefined;
      if (requestId) {
        latencyTracker.setLatestRequestId(requestId);
        activePlaybackRequestIdRef.current = requestId;
      }
      console.log(`[TTS] PCM 流结束, chunks=${totalChunks}${requestId ? `, request_id=${requestId}` : ""}`);
      // 传入 request_id：收尾事件需与具体流绑定（挂起流/非活跃流的收尾不再误标当前流）
      audioPlayback.endPcmStream(requestId);
    },
    onSttPartial: (text) => {
      setPartialStt(text);
      handleSttResult(text, true);
      // 状态占位文案（"正在识别..."等）只作提示，不写入输入框覆盖实时转写文本
      if (isSttStatusText(text)) {
        return;
      }
      // 所有模式都把中间结果实时写进输入框
      // 防陈旧 partial：final 后 800ms 内的迟到 partial 跳过写框（避免写脏已清空的输入框）
      if (Date.now() - sttFinalAtRef.current < 800) {
        return;
      }
      // 防覆盖用户手打草稿：仅当输入框为空或内容就是上次写入的中间结果时才更新
      if (input === "" || input === sttPartialBoxRef.current) {
        setInput(text);
        sttPartialBoxRef.current = text;
      }
    },
    onSttFinal: (text) => {
      sttFinalAtRef.current = Date.now();
      setPartialStt("");
      // 清理输入框中残留的中间结果文本：若用户未手动改动（内容仍为上次写入的中间结果）则清空。
      // 但实时转写会话活跃期内（1.5s 内有显示文本）不清——唤醒词收集期多个短语累加显示为一条指令，
      // 过早清空会把已积累的文字抹掉；会话结束由 sendResult/消息发送接管输入框。
      const liveDisplayActive = Date.now() - liveSttAtRef.current < 1500;
      if (!liveDisplayActive && input === sttPartialBoxRef.current) {
        setInput("");
      }
      if (!liveDisplayActive) {
        sttPartialBoxRef.current = "";
      }
      handleSttResult(text, false);
      if (text.includes('未识别') || text.includes('识别失败')) {
        console.log('[onSttFinal] 跳过无效识别结果:', text);
        return;
      }
      if (!continuousModeRef.current && !wakeWordEnabled) {
        // 唤醒词刚关闭后 2 秒内的 STT 结果是残余音频，忽略
        if (wakeWordDisabledAtRef.current && Date.now() - wakeWordDisabledAtRef.current < 2000) {
          console.log('[onSttFinal] 唤醒词刚关闭，忽略残余 STT 结果:', text);
          return;
        }
        setTimeout(() => {
          const trimmed = text.trim();
          if (trimmed) {
            console.log('[onSttFinal] 非持续非唤醒词模式，发送消息:', trimmed);
            sendTextMessage(trimmed);
          }
        }, 100);
      } else {
        console.log('[onSttFinal] 消息由 useVoiceOrchestration 处理');
      }
    },
    onInterrupted: () => {
      const requestId = latencyTracker.getLatestRequestId();
      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }
      if (streamingUpdateThrottle.current) {
        clearTimeout(streamingUpdateThrottle.current);
        streamingUpdateThrottle.current = null;
      }
      pendingStreamingUpdate.current = null;
      setAiResponseState('idle');
      isAiSpeakingRef.current = false;
      setIsAiSpeaking(false);
      streamingStartedRef.current = false;
      reasoningStartedRef.current = false;
      if (reasoningThrottle.current) {
        clearTimeout(reasoningThrottle.current);
        reasoningThrottle.current = null;
      }
      pendingReasoningUpdate.current = null;
      ttsController.current?.stop();
      audioPlayback.stopAll();
      // 打断后清空流式 TTS 缓冲，防止残留文本被追加进下一条回答开头（串话）
      streamingTtsBuffer.current = "";
      if (requestId) {
        finalizeLatencyRecord(requestId, "interrupted");
      }

      setMessages((m) => {
        const last = m[m.length - 1];
        if (last?.role === "assistant_stream") {
          const mm = [...m];
          mm[mm.length - 1] = { role: "assistant", text: last.text + " [已打断]" };
          return mm;
        }
        return m;
      });
      resumeVoiceOrchestration(200, "assistant-interrupted");
    },
    onError: (message) => {
      const requestId = latencyTracker.getLatestRequestId();
      if (aiResponseTimeoutRef.current) {
        clearTimeout(aiResponseTimeoutRef.current);
        aiResponseTimeoutRef.current = null;
      }
      setAiResponseState('idle');
      streamingStartedRef.current = false;
      reasoningStartedRef.current = false;
      if (reasoningThrottle.current) {
        clearTimeout(reasoningThrottle.current);
        reasoningThrottle.current = null;
      }
      pendingReasoningUpdate.current = null;
      setMessages((m) => [...m, { role: "system", text: `错误：${message}`, timestamp: Date.now() }]);
      if (requestId) {
        finalizeLatencyRecord(requestId, "error");
      }
    },
    onLatency: (payload) => {
      const requestId = payload?.request_id as string | undefined;
      const stage = payload?.stage as string | undefined;
      const at = typeof payload?.at === "number" ? payload.at : undefined;
      if (!requestId || !stage || !at) {
        return;
      }
      latencyTracker.markStage(requestId, stage as LatencyStage, at);
    },
    onConnected: () => {
      setTimeout(() => {
        if (wsRef.current?.isConnected()) {
          // hello 不携带任何 api key：key 权威在后端 .env.local（显式保存走 set_model_provider /
          // set_stt_provider / set_tts_provider 的同帧通道），hello 带 key 曾把前端残留的旧 key
          // 被动回写进后端造成串 key
          const helloPayload = {
            client: "web",
            lang: "zh",
            vision: { enabled: modelConfig.visionEnabled, fps: modelConfig.visionFps, maxWidth: 640, jpegQuality: 0.7 },
            stt: { enabled: true, sampleRate: 16000 },
            vlm_provider: modelConfig.vlmProvider,
            vlm_model: modelConfig.vlmModel,
            stt_provider: modelConfig.sttProvider,
            stt_model: modelConfig.sttModel,
            tts_provider: modelConfig.ttsProvider,
            tts_model: modelConfig.ttsModel,
            tts_voice: modelConfig.ttsVoice,
            custom_prompt: modelConfig.customPrompt,
            llm_temperature: modelConfig.llmTemperature,
            llm_max_tokens: modelConfig.llmMaxTokens,
            llm_top_p: modelConfig.llmTopP,
            llm_sample_count: modelConfig.llmSampleCount,
            llm_frequency_penalty: modelConfig.llmFrequencyPenalty,
            llm_response_format: modelConfig.llmResponseFormat,
            llm_enable_thinking: modelConfig.llmEnableThinking,
            danger_alert_enabled: modelConfig.dangerAlertEnabled,
            oil_smoke_alert_enabled: modelConfig.oilSmokeAlertEnabled,
          };
          sendWsEvent("hello", helloPayload);
        }
      }, 500);
      handleWsConnected();
    },
    onDisconnected: () => {
      // 断线后活跃 PCM 流的 tts_pcm_end 可能永远不来 → 僵尸流（onPlaybackComplete
      // 不触发、isAiSpeaking 卡 true、下一条新流被挂起成僵尸）：
      // 全量清空播放器（含挂起流与墓碑）并复位上层状态；reconnectReset 不触发
      // onPlaybackComplete，复位由这里自己做
      audioPlayback.reconnectReset();
      streamingTtsBuffer.current = "";
      activePlaybackRequestIdRef.current = null;
      isAiSpeakingRef.current = false;
      setIsAiSpeaking(false);
      resumeVoiceOrchestration(200, "ws-disconnected");
      handleWsDisconnected();
    },
    onCookingStatus: (payload) => {
      console.log('[做饭状态更新]', payload);
      cooking.updateFromBackend(payload);
    },
    onTimerBeep: async (payload) => {
      console.log('[倒计时提示音]', payload.message);

      try {
        const audio = new Audio('/djs.mp3');
        console.log('[倒计时提示音] Audio 对象已创建');

        await new Promise<void>((resolve, reject) => {
          audio.onended = () => {
            console.log('[倒计时提示音] 播放结束');
            resolve();
          };
          audio.onerror = (e) => {
            console.error('[倒计时提示音] 播放错误', e);
            reject(e);
          };
          audio.play().catch(e => {
            console.error('[倒计时提示音] play() 失败', e);
            reject(e);
          });
        });
      } catch (e) {
        console.error('[倒计时提示音] 播放失败', e);
      }
    },
    onTimerDone: (payload) => {
      console.log('[倒计时到期]', payload);
      const currentTimers = cooking.timers;
      const updatedTimers = currentTimers.filter(t => t.id !== payload.id);
      if (updatedTimers.length !== currentTimers.length) {
        console.log(`[倒计时到期] 自动清理倒计时: ${payload.description}`);
        // updateFromBackend 已改为部分更新语义（未提供字段保留原值），此处只传 timers 即可；
        // 仍透传当前完整做饭状态作为双保险：即使某一侧改动未同步，
        // 倒计时到期也不会把已打勾步骤（completed_steps）、推荐菜谱（recipes）、食材清零。
        cooking.updateFromBackend({
          enabled: cooking.isActive,
          state: cooking.cookingState,
          recipe: cooking.currentRecipe,
          step: cooking.currentStep,
          servings: cooking.currentServings,
          awaiting_servings: cooking.cookingState === "AWAITING_SERVINGS",
          ingredients: cooking.userIngredients,
          recipes: cooking.recipes,
          completed_steps: cooking.completedSteps,
          timers: updatedTimers,
        });
      }
    },
    onWakeWordDetected: (payload) => {
      console.log('[后端唤醒词] 检测到唤醒词:', payload.text);
      if (payload.detected && wakeWordEnabled) {
        triggerWakeWordFromBackend();
      }
    },
    onSmokeDetected: (payload) => {
      console.log('[烟雾检测]', payload.message);
      alert(payload.message);
    },
    onPotDangerDetected: (payload) => {
      console.log('[锅盖/泡沫高能警报]', payload.message, payload.danger_type);

      const alarmState = { isPlaying: true, audioCtx: null as AudioContext | null, currentSource: null as AudioBufferSourceNode | null };

      const playAlarmAudio = async () => {
        try {
          const response = await fetch('/bj.mp3');
          const arrayBuffer = await response.arrayBuffer();
          alarmState.audioCtx = new AudioContext();
          const audioBuffer = await alarmState.audioCtx.decodeAudioData(arrayBuffer);

          const playLoop = () => {
            if (!alarmState.isPlaying || !alarmState.audioCtx) return;
            alarmState.currentSource = alarmState.audioCtx.createBufferSource();
            alarmState.currentSource.buffer = audioBuffer;
            alarmState.currentSource.connect(alarmState.audioCtx.destination);
            alarmState.currentSource.onended = () => {
              alarmState.currentSource = null;
              if (alarmState.isPlaying) {
                playLoop();
              }
            };
            alarmState.currentSource.start(0);
          };

          playLoop();
        } catch (e) {
          console.error('[锅盖/泡沫高能警报] 加载音频失败', e);
        }
      };

      const stopAlarmAudio = () => {
        alarmState.isPlaying = false;
        if (alarmState.currentSource) {
          try {
            alarmState.currentSource.stop();
            alarmState.currentSource.disconnect();
          } catch (e) {
            // ignore
          }
          alarmState.currentSource = null;
        }
        if (alarmState.audioCtx) {
          try {
            alarmState.audioCtx.close();
          } catch (e) {
            // ignore
          }
          alarmState.audioCtx = null;
        }
      };

      playAlarmAudio();
      confirm(`⚠️ ${payload.message}\n\n请立即处理！点击"确定"关闭警报。`);
      stopAlarmAudio();
      console.log('[锅盖/泡沫高能警报] 用户已确认，警报结束');
    },
    onDangerDetected: (payload) => {
      // 警报语音保护期：8 秒内抑制回采触发的自动打断，让警报语音完整播完
      dangerAlarmGraceUntilRef.current = Date.now() + 8000;
      // 浏览器自动播放策略：音频通道需一次用户点击才激活。未激活时提示用户
      // 点击页面（点击后积压的警报音频会自动补播），避免第一次警报无声
      if (typeof audioPlayback.isPcmContextRunning === "function" && !audioPlayback.isPcmContextRunning()) {
        showToast('🔇 警报声音需要激活：请点击页面任意位置（已积压的警报会补播）', 'warning');
      }
      console.log('[统一危险检测]', payload.message, payload.danger_types, '高能:', payload.is_high_energy);

      // 统一走屏幕正中的大号警报弹窗（8 秒自动消失）；
      // 高能危险（锅盖跳动/泡沫溢出）附加循环警笛，弹窗关闭时停止
      const isHigh = !!payload.is_high_energy;
      if (isHigh) {
        startSiren();
      }
      showDangerAlert(payload.message || '检测到危险情况，请注意安全', payload.danger_types || [], isHigh);
    },
    onOilSmokeDetected: () => {
      // 后端已改为纯语音询问，不再发 oil_smoke_detected 事件触发弹窗
      // 保留空壳避免 useWebSocket 类型报错
    },
    onSmokeFanQuestion: () => {
      // 后端已改为纯语音询问，不再发 smoke_fan_question 事件触发弹窗
    },
    onGlassesPreview: (payload) => {
      if (payload.jpeg_b64) {
        deviceMgr.setGlassesCameraConnected(true);
        if (deviceMgr.glassesImgRef.current) {
          deviceMgr.glassesImgRef.current.src = `data:image/jpeg;base64,${payload.jpeg_b64}`;
        }
      }
    },
    onGlassesStatus: (payload) => {
      deviceMgr.setGlassesCameraConnected(payload.connected);
    },
    onFanControl: async (payload) => {
      console.log('[风扇控制]', payload.action);
      if (payload.action === 'on') {
        await fanControl.controlFan('on');
      } else if (payload.action === 'off') {
        await fanControl.controlFan('off');
      } else if (payload.action === 'status') {
        const status = await fanControl.fetchFanStatus();
        console.log('[风扇状态]', status);
      }
    },
    onVoiceMode: (payload) => {
      // 后端/MCP/LLM 工具下发的语音模式指令：与手动点「持续识别」按钮完全同路径，
      // setContinuousMode 触发 useVoiceOrchestration reconcile（销毁唤醒监听/起持续识别通话）
      console.log('[语音模式指令]', payload);
      if (typeof payload?.continuous === 'boolean') {
        setContinuousMode(payload.continuous);
      }
    },
    onServingsCalcStart: (payload) => {
      const labels = Array.isArray(payload.items) ? payload.items.map(String) : [];
      servingsCalcActiveRef.current = true;
      setServingsCalc({ requestId: payload.request_id || "", labels, results: labels.map(() => null) });
    },
    onServingsCalcItem: (payload) => {
      setServingsCalc((prev) => {
        if (!prev) return prev;
        const results = [...prev.results];
        const idx = typeof payload.index === "number" ? payload.index : results.length;
        while (results.length <= idx) results.push(null);
        results[idx] = payload.text || "";
        return { ...prev, results };
      });
    },
    onServingsCalcEnd: () => {
      // 保持卡片可见，直到下一条 AI 正文开始流式输出（onAssistantDelta 里收起）
    }
  });

  // ===== Set refs =====
  canSendRef.current = canSend;
  sendWsEventRef.current = sendWsEvent;
  connectionStateRef.current = connectionState;

  useEffect(() => {
    sendCookingActionRef.current = (action, payload = {}) => {
      sendWsEvent("cooking", { action, ...payload });
    };
    return () => {
      sendCookingActionRef.current = null;
    };
  }, [sendWsEvent, sendCookingActionRef]);

  useEffect(() => {
    // 强制重连（关闭旧连接后按最新会话重连），用于断线恢复与会话切换
    reconnectWsRef.current = reconnectWs;
  }, [reconnectWs, reconnectWsRef]);

  useEffect(() => {
    connectWs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ===== Continuous recorder =====
  useEffect(() => {
    continuousRecorder.current = createContinuousRecorder({
      onChunk: (seq, pcm16_b64) => {
        audioSeqCounter.current = seq + 1;
        if (wsRef.current) {
          wsRef.current.send("audio_stream", {
            seq,
            pcm16_b64,
            is_last: false,
            stt_provider: modelConfig.sttProvider,
            stt_model: modelConfig.sttModel
          });
        }
      },
      onRecordingStart: () => {
        setIsListening(true);
        setPartialStt("（正在听...）");
      },
      onRecordingEnd: () => {
        setIsListening(false);
        setPartialStt("");
        if (wsRef.current) {
          wsRef.current.send("audio_stream_end", {
            last_seq: audioSeqCounter.current - 1,
            stt_provider: modelConfig.sttProvider,
            stt_model: modelConfig.sttModel
          });
        }
      },
      chunkMs: 500,
      targetSampleRate: 16000,
      volumeThreshold: 40,
      silenceSeconds: 3,
      autoStopOnSilence: true,
      debug: true,
    });
    return () => {
      continuousRecorder.current?.stop();
      continuousRecorder.current = null;
    };
  }, [modelConfig.apiKeys, modelConfig.sttModel, modelConfig.sttProvider]);

  // ===== onSendText =====
  const onSendText = useCallback((files?: FileData[]) => {
    const text = input.trim();
    if (!text && (!files || files.length === 0)) return;
    setSpeechRecognitionState('idle');
    setPartialStt('');
    setInput("");
    sendTextMessage(text, files);
  }, [input, sendTextMessage]);

  return {
    messages,
    setMessages,
    input,
    setInput,
    partialStt,
    setPartialStt,
    aiResponseState,
    setAiResponseState,
    speechRecognitionState,
    isAiSpeaking,
    isListening,
    interruptMessage,
    latestVisionObservation,
    isCookingScene,
    cookingSuggestion,
    wakeWordEnabled,
    setWakeWordEnabled,
    wakeWordDetected,
    wakeWordStatusText,
    wakeWordLiveTranscript,
    continuousMode,
    setContinuousMode,
    continuousSpeechPaused,
    setContinuousSpeechPaused,
    isTestingTts,
    showModelSelector,
    setShowModelSelector,
    showPromptSettings,
    setShowPromptSettings,
    callActive,
    hangupCall,
    chatPanelWidth,
    setChatPanelWidth,
    viewportWidth,
    isRecording,
    toasts,
    showToast,
    dismissToast,
    dangerAlert,
    dismissDangerAlert,
    servingsCalc,
    sendInterrupt,
    sendTextMessage,
    onSendText,
    interruptAndContinue,
    testTts,
    wsRef,
    canSend,
    connectionState,
    sendWsEvent,
    connectWs,
    isAiSpeakingRef,
  };
}
