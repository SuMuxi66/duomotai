import { useRef, useState, useCallback, useEffect } from "react";
import { WSClient, Envelope, getBackendUrl } from "../lib/ws";
import { API_BASE } from "../lib/constants";

 export type WebSocketConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "closed" | "error";

export interface UseWebSocketOptions {
  onVisionObservation?: (payload: { text: string; user_question: string }) => void;
  onVisionAnalysis?: (payload: { analysis: string }) => void;
  onAssistantReasoningDelta?: (reasoning: string, payload?: any) => void;
  onAssistantDelta?: (delta: string, payload?: any) => void;
  onAssistantFinal?: (text: string, payload?: any) => void;
  onTtsAudio?: (wavB64: string, chunkIndex: number, totalChunks: number, payload?: any) => void;
  onTtsPcm?: (pcmB64: string, sampleRate: number, chunkIndex: number, payload?: any) => void;
  onTtsPcmEnd?: (totalChunks: number, sampleRate: number, payload?: any) => void;
  onTtsError?: (payload?: any) => void;
  onSttPartial?: (text: string) => void;
  onSttFinal?: (text: string) => void;
  onInterrupted?: () => void;
  onError?: (message: string) => void;
  onLatency?: (payload: any) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onCookingStatus?: (payload: {
    enabled: boolean;
    state: string;
    recipe: any;
    step: number;
    ingredients: string[];
    recipes?: any[];
    completed_steps?: number[];
    timers?: any[];
  }) => void;
  onTimerDone?: (payload: { id: string; description: string; total_seconds: number }) => void;
  onTimerBeep?: (payload: { message: string; timer_id: string }) => void;
  onWakeWordDetected?: (payload: { detected: boolean; text: string }) => void;
  onSmokeDetected?: (payload: { message: string; timestamp: number }) => void;
  onPotDangerDetected?: (payload: { message: string; danger_type: string[]; timestamp: number }) => void;
  onDangerDetected?: (payload: { message: string; danger_types: string[]; severity: string; is_high_energy: boolean; timestamp: number }) => void;
  onOilSmokeDetected?: (payload: { message: string; oil_smoke_level: string; confidence: number; timestamp: number }) => void;
  onSmokeFanQuestion?: (payload: { message: string; danger_types: string[]; timestamp: number }) => void;
  onFanControl?: (payload: { action: string }) => void;
  onVoiceMode?: (payload: { continuous: boolean; source?: string }) => void;
  onGlassesPreview?: (payload: { jpeg_b64: string; frame_id: number; timestamp: number }) => void;
  onGlassesStatus?: (payload: { connected: boolean; message?: string }) => void;
  onServingsCalcStart?: (payload: { request_id: string; items: string[] }) => void;
  onServingsCalcItem?: (payload: { request_id: string; index: number; text: string }) => void;
  onServingsCalcEnd?: (payload: { request_id: string }) => void;
}

export interface UseWebSocketReturn {
  wsRef: React.MutableRefObject<WSClient | null>;
  isConnected: boolean;
  canSend: boolean;
  connectionState: WebSocketConnectionState;
  send: (type: string, payload: any) => void;
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
}

 const sharedWsState: {
  client: WSClient | null;
  refCount: number;
  releaseTimer: ReturnType<typeof setTimeout> | null;
 } = {
  client: null,
  refCount: 0,
  releaseTimer: null,
 };

 function retainSharedClient() {
  sharedWsState.refCount += 1;
  if (sharedWsState.releaseTimer) {
    clearTimeout(sharedWsState.releaseTimer);
    sharedWsState.releaseTimer = null;
  }
 }

 function releaseSharedClient() {
  sharedWsState.refCount = Math.max(0, sharedWsState.refCount - 1);
  if (sharedWsState.refCount > 0 || sharedWsState.releaseTimer) {
    return;
  }
  sharedWsState.releaseTimer = setTimeout(() => {
    sharedWsState.releaseTimer = null;
    if (sharedWsState.refCount === 0 && sharedWsState.client) {
      sharedWsState.client.close();
      sharedWsState.client = null;
    }
  }, 800);
 }

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const optionsRef = useRef(options);
  const wsLogTimesRef = useRef<Record<string, number>>({});
  const hasRetainedSharedClientRef = useRef(false);
  
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const wsRef = useRef<WSClient | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] = useState<WebSocketConnectionState>("idle");
  const logWsEvent = useCallback((key: string, message: string, throttleMs = 1200) => {
    const now = Date.now();
    const lastAt = wsLogTimesRef.current[key] ?? 0;
    if (now - lastAt < throttleMs) {
      return;
    }
    wsLogTimesRef.current[key] = now;
    console.log(message);
  }, []);

  // 消息去重机制 - 防止 StrictMode 导致的消息重复处理
  const processedMessageIdsRef = useRef<Set<string>>(new Set());
  
  // 基于时间窗口的去重 - 防止短时间内相同内容的消息
  const recentMessagesTimeWindowRef = useRef<Map<string, number>>(new Map());
  const TIME_WINDOW_MS = 2000; // 2秒时间窗口
  
  // 用于追踪清理定时器，组件卸载时清理
  const cleanupTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  
  /** 稳定的消息ID生成：优先使用 request_id，否则用排序键的 JSON 序列化 */
  function getMessageId(evt: Envelope): string {
    const rid = (evt.payload as any)?.request_id;
    if (rid) return `${evt.type}-${rid}`;
    // 稳定序列化：按 key 排序避免键顺序问题
    const sorted = Object.keys(evt.payload || {}).sort().reduce((acc, k) => {
      acc[k] = (evt.payload as any)[k];
      return acc;
    }, {} as Record<string, any>);
    return `${evt.type}-${JSON.stringify(sorted)}`;
  }
  
  const handleMessage = useCallback((evt: Envelope) => {
    if (!["assistant_delta", "tts_pcm", "stt_partial"].includes(evt.type)) {
      logWsEvent(`ws-${evt.type}`, `[WS 收到消息] ${evt.type}`, evt.type === "status" ? 1500 : 300);
    }
    
    const messageId = getMessageId(evt);
    if (processedMessageIdsRef.current.has(messageId)) {
      console.log(`[WS 去重] 跳过重复消息: ${evt.type}`);
      return;
    }
    
    // 对于需要严格去重的消息类型（assistant_final, stt_final, timer_done 等），记录消息ID
    if (["assistant_final", "stt_final", "timer_done", "timer_completed", "cooking_status", "error"].includes(evt.type)) {
      processedMessageIdsRef.current.add(messageId);
      // 限制集合大小，防止内存泄漏
      if (processedMessageIdsRef.current.size > 1000) {
        const iterator = processedMessageIdsRef.current.values();
        const firstValue = iterator.next().value;
        if (firstValue) {
          processedMessageIdsRef.current.delete(firstValue);
        }
      }
      
      // 时间窗口去重 - 仅对带 text 的消息生效。
      // cooking_status 等无 text 的状态事件共用空 key，2 秒窗口内的关键状态更新
      // （如倒计时完成 timers=[]）会被整体吞掉，导致倒计时卡片滞留在 00:00
      if (typeof evt.payload.text === 'string') {
        const contentKey = `${evt.type}-${evt.payload.text}`;
        const now = Date.now();
        const lastTime = recentMessagesTimeWindowRef.current.get(contentKey);

        if (lastTime && (now - lastTime) < TIME_WINDOW_MS) {
          console.log(`[WS 时间窗口去重] 跳过短时间内重复的 ${evt.type} 消息`);
          return;
        }

        // 记录当前消息时间
        recentMessagesTimeWindowRef.current.set(contentKey, now);

        // 清理过期的记录（超过5秒）
        const timer = setTimeout(() => {
          recentMessagesTimeWindowRef.current.delete(contentKey);
        }, 5000);
        cleanupTimersRef.current.push(timer);
      }
    }
    
    const ops = optionsRef.current;

    switch (evt.type) {
      case "vision_observation":
        ops.onVisionObservation?.(evt.payload);
        break;
      case "vision_analysis":
        ops.onVisionAnalysis?.(evt.payload);
        break;
      case "assistant_reasoning_delta":
        ops.onAssistantReasoningDelta?.(evt.payload.reasoning || evt.payload.delta || "", evt.payload);
        break;
      case "assistant_delta":
        ops.onAssistantDelta?.(evt.payload.delta || "", evt.payload);
        break;
      case "assistant_final":
        ops.onAssistantFinal?.(evt.payload.text || "", evt.payload);
        break;
      case "tts_audio":
        ops.onTtsAudio?.(evt.payload.wav_b64, evt.payload.chunk_index || 0, evt.payload.total_chunks || 1, evt.payload);
        break;
      case "tts_pcm":
        ops.onTtsPcm?.(evt.payload.pcm_b64 || "", evt.payload.sample_rate || 24000, evt.payload.chunk_index || 0, evt.payload);
        break;
      case "tts_pcm_end":
        ops.onTtsPcmEnd?.(evt.payload.total_chunks || 0, evt.payload.sample_rate || 24000, evt.payload);
        break;
      case "tts_error":
        ops.onTtsError?.(evt.payload);
        break;
      case "stt_partial":
        ops.onSttPartial?.(evt.payload.text || "");
        break;
      case "stt_final":
        ops.onSttFinal?.(evt.payload.text || "");
        break;
      case "interrupted":
        ops.onInterrupted?.();
        break;
      case "interrupt_ack":
        ops.onInterrupted?.();
        break;
      case "error":
        ops.onError?.(evt.payload.message);
        break;
      case "latency":
        ops.onLatency?.(evt.payload);
        break;
      case "cooking_status":
        ops.onCookingStatus?.(evt.payload);
        break;
      case "timer_done":
        ops.onTimerDone?.(evt.payload);
        break;
      case "timer_beep":
        ops.onTimerBeep?.(evt.payload);
        break;
      case "timer_completed":
        // 后端倒计时完成事件：归一化后复用 timer_done 的本地清理逻辑
        ops.onTimerDone?.({
          id: evt.payload?.timer?.id,
          description: evt.payload?.timer?.description,
          total_seconds: evt.payload?.timer?.total_seconds,
        });
        break;
      case "wake_word_detected":
        ops.onWakeWordDetected?.(evt.payload);
        break;
      case "smoke_detected":
        ops.onSmokeDetected?.(evt.payload);
        break;
      case "pot_danger_detected":
        ops.onPotDangerDetected?.(evt.payload);
        break;
      case "danger_detected":
        ops.onDangerDetected?.(evt.payload);
        break;
      case "oil_smoke_detected":
        ops.onOilSmokeDetected?.(evt.payload);
        break;
      case "smoke_fan_question":
        ops.onSmokeFanQuestion?.(evt.payload);
        break;
      case "fan_control":
        ops.onFanControl?.(evt.payload);
        break;
      case "voice_mode":
        ops.onVoiceMode?.(evt.payload);
        break;
      case "glasses_preview":
        ops.onGlassesPreview?.(evt.payload);
        break;
      case "servings_calc_start":
        ops.onServingsCalcStart?.(evt.payload);
        break;
      case "servings_calc_item":
        ops.onServingsCalcItem?.(evt.payload);
        break;
      case "servings_calc_end":
        ops.onServingsCalcEnd?.(evt.payload);
        break;
      case "glasses_status":
        ops.onGlassesStatus?.(evt.payload);
        break;
    }
  }, [logWsEvent]);

  const bindClient = useCallback((client: WSClient) => {
    client.setOnEvent(handleMessage);
    client.setHandlers({
      onConnecting: (isReconnect) => {
        setConnectionState(isReconnect ? "reconnecting" : "connecting");
      },
      onOpen: () => {
        setIsConnected(true);
        setConnectionState("connected");
        optionsRef.current.onConnected?.();
      },
      onClose: (_event, manualClose) => {
        setIsConnected(false);
        setConnectionState(manualClose ? "closed" : "reconnecting");
        optionsRef.current.onDisconnected?.();
      },
      onError: () => {
        setConnectionState("error");
      },
      onReconnectScheduled: () => {
        setConnectionState("reconnecting");
      },
    });
  }, [handleMessage]);

  const connect = useCallback(() => {
    if (!hasRetainedSharedClientRef.current) {
      retainSharedClient();
      hasRetainedSharedClientRef.current = true;
    }

    if (!sharedWsState.client) {
      sharedWsState.client = new WSClient(handleMessage);
    }

    bindClient(sharedWsState.client);
    wsRef.current = sharedWsState.client;

    const wsUrl = getBackendUrl(API_BASE, true);
    console.log(`[WS] 连接到: ${wsUrl}`);
    sharedWsState.client.connect(wsUrl);
  }, [bindClient, handleMessage]);

  const disconnect = useCallback(() => {
    if (sharedWsState.client) {
      sharedWsState.client.close();
      sharedWsState.client = null;
    }
    wsRef.current = null;
    setIsConnected(false);
    setConnectionState("closed");
  }, []);

  // 强制重连（关闭现有连接后按最新会话重新连接，用于切换会话/断线恢复）
  const reconnect = useCallback(() => {
    if (sharedWsState.client) {
      sharedWsState.client.close();
      sharedWsState.client = null;
    }
    wsRef.current = null;
    connect();
  }, [connect]);

  const send = useCallback((type: string, payload: any) => {
    if (wsRef.current) {
      wsRef.current.send(type, payload);
    }
  }, []);

  // cleanup 在组件卸载时执行，ws.ts 的 close 方法已经修复可以自动重连
  useEffect(() => {
    return () => {
      // 清理所有待执行的清理定时器
      cleanupTimersRef.current.forEach(timer => clearTimeout(timer));
      cleanupTimersRef.current = [];
      
      if (hasRetainedSharedClientRef.current) {
        hasRetainedSharedClientRef.current = false;
        releaseSharedClient();
      }
    };
  }, []);

  useEffect(() => {
    if (sharedWsState.client) {
      bindClient(sharedWsState.client);
      wsRef.current = sharedWsState.client;
      if (sharedWsState.client.isConnected()) {
        setIsConnected(true);
        setConnectionState("connected");
      } else if (sharedWsState.client.isConnecting()) {
        setConnectionState(sharedWsState.client.hasConnectedOnce ? "reconnecting" : "connecting");
      }
    }
  }, [bindClient]);

  return {
    wsRef,
    isConnected,
    canSend: isConnected,
    connectionState,
    send,
    connect,
    disconnect,
    reconnect
  };
}
