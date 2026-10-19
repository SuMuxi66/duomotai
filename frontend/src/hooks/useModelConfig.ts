import { useState, useEffect, useCallback, useRef } from "react";
import { usePersistedState } from "./usePersistedState";
import { useProviderCatalog } from "./useProviderCatalog";
import { systemDefaults } from "../lib/config";
import { API_BASE } from "../lib/constants";
import { STORAGE_KEYS, loadFromStorage, saveToStorage } from "../lib/storage";
import { getDefaultTtsModel, getDefaultTtsVoice, isValidTtsVoiceForProvider } from "../lib/tts-config";
import type { ApiKeys, LlmResponseFormat } from "../types";
import type { ProviderId } from "../components/ProviderIcon";

export interface UseModelConfigOptions {
  /** 使用 ref 避免循环依赖（在 useWebSocket 返回后由 App 设置） */
  canSendRef: React.MutableRefObject<boolean>;
  sendWsEventRef: React.MutableRefObject<((type: string, payload: any) => void) | null>;
}

export interface UseModelConfigReturn {
  // Vision
  visionEnabled: boolean;
  setVisionEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  visionFps: number;
  setVisionFps: React.Dispatch<React.SetStateAction<number>>;
  // TTS
  ttsEnabled: boolean;
  setTtsEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  // Providers/Models（文字与视觉统一走 VLM 端点）
  vlmProvider: ProviderId | string;
  setVlmProvider: React.Dispatch<React.SetStateAction<ProviderId | string>>;
  vlmModel: string;
  setVlmModel: React.Dispatch<React.SetStateAction<string>>;
  vlmModelMap: Record<string, string>;
  sttProvider: ProviderId;
  setSttProvider: React.Dispatch<React.SetStateAction<ProviderId>>;
  sttModel: string;
  setSttModel: React.Dispatch<React.SetStateAction<string>>;
  sttModelMap: Record<string, string>;
  ttsProvider: ProviderId;
  setTtsProvider: React.Dispatch<React.SetStateAction<ProviderId>>;
  ttsModel: string;
  setTtsModel: React.Dispatch<React.SetStateAction<string>>;
  ttsModelMap: Record<string, string>;
  ttsVoice: string;
  setTtsVoice: React.Dispatch<React.SetStateAction<string>>;
  // LLM generation params
  llmTemperature: number;
  setLlmTemperature: React.Dispatch<React.SetStateAction<number>>;
  llmMaxTokens: number;
  setLlmMaxTokens: React.Dispatch<React.SetStateAction<number>>;
  llmTopP: number;
  setLlmTopP: React.Dispatch<React.SetStateAction<number>>;
  llmSampleCount: number;
  setLlmSampleCount: React.Dispatch<React.SetStateAction<number>>;
  llmFrequencyPenalty: number;
  setLlmFrequencyPenalty: React.Dispatch<React.SetStateAction<number>>;
  llmResponseFormat: LlmResponseFormat;
  setLlmResponseFormat: React.Dispatch<React.SetStateAction<LlmResponseFormat>>;
  llmEnableThinking: boolean;
  setLlmEnableThinking: React.Dispatch<React.SetStateAction<boolean>>;
  // Alert settings
  dangerAlertEnabled: boolean;
  setDangerAlertEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  oilSmokeAlertEnabled: boolean;
  setOilSmokeAlertEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  // Camera source（网络摄像头/本机 USB，权威在后端 .env.local 的 CAMERA_SOURCE_MODE，经 /config 下发）
  cameraSourceMode: "network" | "local";
  setCameraSourceMode: React.Dispatch<React.SetStateAction<"network" | "local">>;
  // 画面旋转角度（后端 CAMERA_ROTATION 经 /config 下发，源头旋转，本前端只读显 + 按钮递增）
  cameraRotation: number;
  setCameraRotation: React.Dispatch<React.SetStateAction<number>>;
  // API keys
  apiKeys: ApiKeys;
  setApiKeys: React.Dispatch<React.SetStateAction<ApiKeys>>;
  // Custom prompt
  customPrompt: string;
  setCustomPrompt: React.Dispatch<React.SetStateAction<string>>;
  // Model selector handlers
  handleVlmProviderChange: (provider: ProviderId | string, extras?: { base_url?: string; api_format?: string; api_key?: string; vlm_model?: string }) => void;
  handleVlmModelChange: (model: string) => void;
  /** 端侧唤醒模型（KWS）服务地址 */
  edgeKwsUrl: string;
  setEdgeKwsUrl: (url: string) => void;
  /** 端侧流式 ASR 服务地址 */
  edgeAsrUrl: string;
  setEdgeAsrUrl: (url: string) => void;
  /**
   * 云端优先（端侧兜底）识别策略，唤醒词与持续识别/通话模式共用同一开关。
   * true=云端优先（准确率优先），false=纯端侧（低延迟，端侧不在场自动回退云端）。
   */
  sttPreferCloud: boolean;
  setSttPreferCloud: (preferCloud: boolean) => void;
  handleTtsProviderChange: (v: ProviderId, apiKeyOverride?: string) => void;
  handleTtsVoiceChange: (voice: string) => void;
  handleTtsModelChange: (model: string) => void;
  handleSttProviderChange: (v: ProviderId, apiKeyOverride?: string) => void;
  handleSttModelChange: (model: string) => void;
  handleApiKeyChange: (provider: ProviderId, apiKey: string) => void;
  syncFromBackendConfig: (config: Record<string, any>) => void;
  handleLlmGenerationChange: (config: {
    temperature?: number;
    maxTokens?: number;
    topP?: number;
    sampleCount?: number;
    frequencyPenalty?: number;
    responseFormat?: LlmResponseFormat;
    enableThinking?: boolean;
  }) => void;
  handleDangerAlertChange: (enabled: boolean) => void;
  handleOilSmokeAlertChange: (enabled: boolean) => void;
}

/**
 * 模型/提供商配置 hook
 * - 管理 VLM/STT/TTS 提供商和模型选择
 * - 管理 LLM 生成参数
 * - 管理 API keys、自定义提示词、警报开关
 * - 提供配置同步到后端的 effects
 * - 提供 ModelSelector 的事件处理函数
 *
 * 使用 canSendRef/sendWsEventRef 避免与 useWebSocket 的循环依赖。
 */
export function useModelConfig(options: UseModelConfigOptions): UseModelConfigReturn {
  const { canSendRef, sendWsEventRef } = options;
  // 供应商预设清单来自后端 /providers/catalog（registry 单一数据源），失败回退静态清单
  const catalog = useProviderCatalog();

  const [visionEnabled, setVisionEnabled] = usePersistedState(
    STORAGE_KEYS.visionEnabled,
    systemDefaults.visionEnabled
  );
  const [visionFps, setVisionFps] = usePersistedState(
    STORAGE_KEYS.visionFps,
    systemDefaults.visionFps
  );

  const [ttsEnabled, setTtsEnabled] = usePersistedState(
    STORAGE_KEYS.ttsEnabled,
    systemDefaults.ttsEnabled
  );

  const [vlmProvider, setVlmProvider] = usePersistedState<ProviderId | string>(
    STORAGE_KEYS.vlmProvider,
    systemDefaults.vlmProvider as ProviderId
  );
  const [vlmModel, setVlmModel] = usePersistedState(STORAGE_KEYS.vlmModel, systemDefaults.vlmModel);
  // 唤醒词/持续识别共用的识别策略：云端优先（端侧兜底）＝默认开启；关闭则纯端侧低延迟。
  // 同时控制两条链路（2026-09-18 用户拍板）：唤醒词模式（createSttWakeWordListener 的 preferCloud）
  // 与持续识别/通话模式（createSttContinuousListener 的 preferCloud，start 时评估，
  // 切换后重开持续识别生效）。provider 选「端侧语音」或「浏览器」时该开关在 UI 上禁用。
  const [sttPreferCloud, setSttPreferCloud] = usePersistedState<boolean>(STORAGE_KEYS.sttPreferCloud, true);

  // 端侧语音服务地址（唤醒模型 KWS / 流式 ASR，运行在香橙派）
  const [edgeKwsUrl, setEdgeKwsUrl] = usePersistedState(STORAGE_KEYS.edgeKwsUrl, "ws://192.168.1.248:8767/kws");
  const [edgeAsrUrl, setEdgeAsrUrl] = usePersistedState(STORAGE_KEYS.edgeAsrUrl, "ws://192.168.1.248:8768/asr");

  // 香橙派 IP 由 DHCP 分配会漂移，写死地址易失效：挂载时问一次后端
  // （后端并发扫描局域网 8767/8768 并用 /health 校验），拿到就自动覆盖。
  // 仅当用户仍用内置默认值时才覆盖，避免冲掉设置面板里手填的地址。
  // 探测不到（found=false）且仍是内置默认地址 → 置空：语音链路据此直接走云端
  // （持续识别不再连 8768 白等超时、唤醒词走云端轮询兜底）；Pi 回来后刷新页面或手填地址即恢复。
  useEffect(() => {
    let cancelled = false;
    const isBuiltinDefault = (url?: string) =>
      !url || url.includes("192.168.1.127") || url.includes("192.168.1.248");
    (async () => {
      try {
        const resp = await fetch(`${API_BASE}/edge/voice`);
        if (!resp.ok || cancelled) return;
        const data = (await resp.json()) as { found?: boolean; kws_url?: string; asr_url?: string };
        if (cancelled) return;
        if (!data?.found) {
          // 后端扫描没找到香橙派：默认地址留着只会让前端每个会话都去连一个不存在的设备
          // （持续识别要 2×连接超时才降级云端，用户干等）。手填过地址的尊重用户，不清。
          if (isBuiltinDefault(edgeKwsUrl)) setEdgeKwsUrl("");
          if (isBuiltinDefault(edgeAsrUrl)) setEdgeAsrUrl("");
          return;
        }
        if (isBuiltinDefault(edgeKwsUrl) && data.kws_url) setEdgeKwsUrl(data.kws_url);
        if (isBuiltinDefault(edgeAsrUrl) && data.asr_url) setEdgeAsrUrl(data.asr_url);
      } catch {
        // 后端不可用或接口缺失时静默降级：沿用现有配置，不打扰用户
      }
    })();
    return () => {
      cancelled = true;
    };
    // 只在挂载时执行一次（闭包读取的是首次渲染值，正合"判断是否仍是默认值"的语义）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [vlmModelMap, setVlmModelMap] = useState<Record<string, string>>(() => loadFromStorage(STORAGE_KEYS.vlmModelMap, {}));
  const vlmModelMapRef = useRef(vlmModelMap);
  useEffect(() => { vlmModelMapRef.current = vlmModelMap; }, [vlmModelMap]);
  const vlmProviderRef = useRef<ProviderId | string>(vlmProvider);
  useEffect(() => { vlmProviderRef.current = vlmProvider; }, [vlmProvider]);

  const [sttModelMap, setSttModelMap] = useState<Record<string, string>>(() => loadFromStorage(STORAGE_KEYS.sttModelMap, {}));
  const sttModelMapRef = useRef(sttModelMap);
  useEffect(() => { sttModelMapRef.current = sttModelMap; }, [sttModelMap]);

  const [ttsProvider, setTtsProvider] = usePersistedState<ProviderId>(
    STORAGE_KEYS.ttsProvider,
    systemDefaults.ttsProvider as ProviderId
  );
  // TTS 模型缓存迁移：旧模型 mimo-v2-tts 已废弃，自动升级到新模型
  const migratedTtsModel = (() => {
    const stored = loadFromStorage<string>(STORAGE_KEYS.ttsModel, systemDefaults.ttsModel);
    if (stored === "mimo-v2-tts") {
      console.log("[useModelConfig] 检测到已废弃的 TTS 模型 mimo-v2-tts，自动迁移到 mimo-v2.5-tts");
      const updated = "mimo-v2.5-tts";
      saveToStorage(STORAGE_KEYS.ttsModel, updated);
      return updated;
    }
    return stored;
  })();
  const [ttsModel, setTtsModel] = useState<string>(migratedTtsModel);
  useEffect(() => { saveToStorage(STORAGE_KEYS.ttsModel, ttsModel); }, [ttsModel]);

  const migratedTtsModelMap = (() => {
    const map = loadFromStorage<Record<string, string>>(STORAGE_KEYS.ttsModelMap, {});
    const newMap = { ...map };
    let changed = false;
    for (const provider of Object.keys(newMap)) {
      if (newMap[provider] === "mimo-v2-tts") {
        newMap[provider] = "mimo-v2.5-tts";
        changed = true;
      }
    }
    if (changed) {
      console.log("[useModelConfig] 迁移 ttsModelMap 中的旧模型到 mimo-v2.5-tts");
      saveToStorage(STORAGE_KEYS.ttsModelMap, newMap);
    }
    return newMap;
  })();
  const [ttsModelMap, setTtsModelMap] = useState<Record<string, string>>(migratedTtsModelMap);
  const ttsModelMapRef = useRef(ttsModelMap);
  useEffect(() => { ttsModelMapRef.current = ttsModelMap; }, [ttsModelMap]);

  const [sttProvider, setSttProvider] = usePersistedState<ProviderId>(
    STORAGE_KEYS.sttProvider,
    "browser" as ProviderId
  );
  const [sttModel, setSttModel] = usePersistedState(STORAGE_KEYS.sttModel, "Web Speech API");

  const [ttsVoice, setTtsVoice] = usePersistedState(STORAGE_KEYS.ttsVoice, systemDefaults.ttsVoice);
  const ttsVoiceRef = useRef(ttsVoice);
  useEffect(() => { ttsVoiceRef.current = ttsVoice; }, [ttsVoice]);

  const [llmTemperature, setLlmTemperature] = usePersistedState(STORAGE_KEYS.llmTemperature, systemDefaults.llmTemperature);
  const [llmMaxTokens, setLlmMaxTokens] = usePersistedState(STORAGE_KEYS.llmMaxTokens, systemDefaults.llmMaxTokens);
  const [llmTopP, setLlmTopP] = usePersistedState(STORAGE_KEYS.llmTopP, systemDefaults.llmTopP);
  const [llmSampleCount, setLlmSampleCount] = usePersistedState(STORAGE_KEYS.llmSampleCount, systemDefaults.llmSampleCount);
  const [llmFrequencyPenalty, setLlmFrequencyPenalty] = usePersistedState(STORAGE_KEYS.llmFrequencyPenalty, systemDefaults.llmFrequencyPenalty);
  const [llmResponseFormat, setLlmResponseFormat] = usePersistedState<"md" | "json">(STORAGE_KEYS.llmResponseFormat, systemDefaults.llmResponseFormat);
  const [llmEnableThinking, setLlmEnableThinking] = usePersistedState<boolean>(STORAGE_KEYS.llmEnableThinking, systemDefaults.llmEnableThinking ?? false);

  // 危险报警默认开启（与后端 SessionState 默认一致），用户可在设置中关闭
  const [dangerAlertEnabled, setDangerAlertEnabled] = usePersistedState<boolean>('multimodal_danger_alert_enabled', true);
  const [oilSmokeAlertEnabled, setOilSmokeAlertEnabled] = usePersistedState<boolean>('multimodal_oil_smoke_alert_enabled', true);

  // 摄像头数据源模式：权威在后端（.env.local 的 CAMERA_SOURCE_MODE 经 /config 下发），
  // 前端持久化仅作加载时的初始显示，加载后由 /config 覆盖
  const [cameraSourceMode, setCameraSourceMode] = usePersistedState<'network' | 'local'>('multimodal_camera_source_mode', 'network');

  // 画面旋转角度：权威在后端（CAMERA_ROTATION，画面源头旋转），前端不持久化，
  // 初始 0 仅作占位，/config 返回后覆盖
  const [cameraRotation, setCameraRotation] = useState(0);

  const [customPrompt, setCustomPrompt] = usePersistedState(STORAGE_KEYS.customPrompt, "");

  // key 权威在后端（.env.local 经 GET /config 下发），前端不持久化：state 初始为空，加载后由 /config 覆盖。
  // custom provider 的 key 无 env 键位，仍存前端 customVlmProviders 配置内（经 set_model_provider 显式上报）。
  const [apiKeys, setApiKeys] = useState<ApiKeys>(() => ({
    modelscope: "",
    siliconflow: "",
    dashscope: "",
    xiaomi: "",
    deepseek: "",
    zhipu: "",
    sensenova: "",
  }));

  // 从后端加载配置：key 一律以后端为准（含空值——后端没存就不显示旧值，杜绝前端残留 key 回流）
  useEffect(() => {
    fetch('/config')
      .then(res => res.json())
      .then(data => {
        if (data.api_keys) {
          setApiKeys(prev => {
            const next = { ...prev };
            (Object.keys(data.api_keys) as Array<keyof ApiKeys>).forEach(k => {
              if (k in next) next[k] = data.api_keys[k] ?? "";
            });
            return next;
          });
        }
        if (data.camera_source_mode === 'network' || data.camera_source_mode === 'local') {
          setCameraSourceMode(data.camera_source_mode);
        }
        if (data.camera_rotation !== undefined) {
          setCameraRotation(((Number(data.camera_rotation) || 0) % 360 + 360) % 360);
        }
      })
      .catch(err => console.error('[Config] 加载后端配置失败:', err));
  }, []);

  // VLM 模型随提供商变化
  useEffect(() => {
    const map = vlmModelMapRef.current;
    if (map[vlmProvider]) {
      setVlmModel(map[vlmProvider]);
      return;
    }
    // 自定义 provider
    if (vlmProvider === "custom" || vlmProvider.startsWith("custom:")) {
      const customProviders = loadFromStorage<Array<{ id: string; model: string }>>(STORAGE_KEYS.customVlmProviders, []);
      const cfg = customProviders.find((c) => c.id === vlmProvider);
      if (cfg?.model) {
        setVlmModel(cfg.model);
      } else {
        setVlmModel("");
      }
      return;
    }
    // 预设供应商默认模型从后端目录取（目录到达时会重新解析一次）
    const providerData = catalog.vlmProviders.find((p) => p.id === vlmProvider);
    if (providerData && providerData.models.length > 0) {
      setVlmModel(providerData.models[0]);
    } else {
      setVlmModel("");
    }
  }, [vlmProvider, catalog.vlmProviders]);

  useEffect(() => {
    if (vlmProvider) {
      const currentModel = vlmModelMapRef.current[vlmProvider];
      if (currentModel !== vlmModel) {
        const newMap = { ...vlmModelMapRef.current, [vlmProvider]: vlmModel };
        setVlmModelMap(newMap);
        saveToStorage(STORAGE_KEYS.vlmModelMap, newMap);
      }
    }
  }, [vlmModel, vlmProvider]);



  // STT 模型随提供商变化
  useEffect(() => {
    const map = sttModelMapRef.current;
    if (map[sttProvider]) {
      setSttModel(map[sttProvider]);
    } else {
      const providerData = catalog.sttProviders.find(p => p.id === sttProvider);
      if (providerData && providerData.models.length > 0) {
        setSttModel(providerData.models[0]);
      } else {
        setSttModel("");
      }
    }
  }, [sttProvider, catalog.sttProviders]);

  useEffect(() => {
    if (sttProvider) {
      const currentModel = sttModelMapRef.current[sttProvider];
      if (currentModel !== sttModel) {
        const newMap = { ...sttModelMapRef.current, [sttProvider]: sttModel };
        setSttModelMap(newMap);
        saveToStorage(STORAGE_KEYS.sttModelMap, newMap);
      }
    }
  }, [sttModel, sttProvider]);

  // TTS 模型/语音随提供商变化
  useEffect(() => {
    const map = ttsModelMapRef.current;
    if (map[ttsProvider]) {
      setTtsModel(map[ttsProvider]);
    } else {
      setTtsModel(getDefaultTtsModel(ttsProvider));
    }
    if (!isValidTtsVoiceForProvider(ttsProvider, ttsVoice)) {
      setTtsVoice(getDefaultTtsVoice(ttsProvider));
    }
  }, [ttsProvider, ttsVoice]);

  useEffect(() => {
    if (ttsProvider) {
      const currentModel = ttsModelMapRef.current[ttsProvider];
      if (currentModel !== ttsModel) {
        const newMap = { ...ttsModelMapRef.current, [ttsProvider]: ttsModel };
        setTtsModelMap(newMap);
        saveToStorage(STORAGE_KEYS.ttsModelMap, newMap);
      }
    }
  }, [ttsModel, ttsProvider]);

  // 旧版本曾把 key 持久化在前端 localStorage（经 hello 被动回写后端，是"前端错误 key 覆盖后端"的通道）；
  // 现改为后端权威，加载时清理历史残留。只清预设 provider 的键——custom 的 key 存在
  // customVlmProviders 配置对象内，不受影响。
  useEffect(() => {
    ["modelscope", "dashscope", "siliconflow", "xiaomi", "deepseek", "zhipu", "sensenova", "qwen3_asr_realtime"].forEach((id) => {
      localStorage.removeItem(`multimodal_${id}_api_key`);
    });
  }, []);

  // 同步 ttsVoice 到后端
  useEffect(() => {
    if (!canSendRef.current) return;
    sendWsEventRef.current?.("control", {
      action: "set_tts_voice",
      tts_voice: ttsVoice
    });
  }, [ttsVoice, canSendRef, sendWsEventRef]);

  // ModelSelector 事件处理函数
  const handleVlmProviderChange = useCallback((provider: ProviderId | string, extras?: { base_url?: string; api_format?: string; api_key?: string; vlm_model?: string }) => {
    setVlmProvider(provider);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_model_provider",
        vlm_provider: provider,
        ...(extras || {}),
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 VLM 提供商');
    }
  }, [setVlmProvider, canSendRef, sendWsEventRef]);

  const handleVlmModelChange = useCallback((model: string) => {
    setVlmModel(model);
    // 显式选择的模型立即写入模型缓存 map（state + localStorage）：否则
    // vlmProvider 变化触发的 effect 会用 map 里的旧模型名覆盖刚选的模型
    if (vlmModelMapRef.current[vlmProviderRef.current] !== model) {
      const newMap = { ...vlmModelMapRef.current, [vlmProviderRef.current]: model };
      setVlmModelMap(newMap);
      saveToStorage(STORAGE_KEYS.vlmModelMap, newMap);
    }
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_model",
        vlm_model: model,
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 VLM 模型');
    }
  }, [setVlmModel, setVlmModelMap, canSendRef, sendWsEventRef]);

  // apiKeyOverride：ModelSelector 确认时传入输入框当前值，与 provider 同帧配对直达后端落盘，
  // 避免"先发 WS 带旧 apiKeys 快照、新 key 等下次 hello"的异步脱节（串 key 根因之一）
  const handleTtsProviderChange = useCallback((v: ProviderId, apiKeyOverride?: string) => {
    setTtsProvider(v);
    const nextVoice = isValidTtsVoiceForProvider(v, ttsVoiceRef.current) ? ttsVoiceRef.current : getDefaultTtsVoice(v);
    if (canSendRef.current) {
      const defaultModel = getDefaultTtsModel(v);
      sendWsEventRef.current?.("control", {
        action: "set_tts_provider",
        tts_provider: v,
        tts_model: defaultModel,
        tts_voice: nextVoice,
        tts_api_key: apiKeyOverride ?? ((apiKeys as unknown as Record<string, string>)[v] || "")
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 TTS 提供商');
    }
  }, [apiKeys, canSendRef, sendWsEventRef, setTtsProvider]);

  const handleTtsVoiceChange = useCallback((voice: string) => {
    setTtsVoice(voice);
  }, [setTtsVoice]);

  const handleTtsModelChange = useCallback((model: string) => {
    setTtsModel(model);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_tts_model",
        tts_model: model,
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 TTS 模型');
    }
  }, [canSendRef, sendWsEventRef, setTtsModel]);

  // apiKeyOverride：ModelSelector 确认时传入输入框当前值，与 provider 同帧配对直达后端落盘
  const handleSttProviderChange = useCallback((v: ProviderId, apiKeyOverride?: string) => {
    setSttProvider(v);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_stt_provider",
        stt_provider: v,
        stt_api_key: apiKeyOverride ?? undefined,
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 STT 提供商');
    }
  }, [canSendRef, sendWsEventRef, setSttProvider]);

  const handleSttModelChange = useCallback((model: string) => {
    setSttModel(model);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_stt_model",
        stt_model: model,
      });
    } else {
      console.error('[模型切换] WebSocket 未连接，无法切换 STT 模型');
    }
  }, [canSendRef, sendWsEventRef, setSttModel]);

  const handleApiKeyChange = useCallback((provider: ProviderId, apiKey: string) => {
    setApiKeys(prev => ({ ...prev, [provider]: apiKey }));
  }, [setApiKeys]);

  const handleLlmGenerationChange = useCallback((config: {
    temperature?: number;
    maxTokens?: number;
    topP?: number;
    sampleCount?: number;
    frequencyPenalty?: number;
    responseFormat?: LlmResponseFormat;
    enableThinking?: boolean;
  }) => {
    if (config.temperature !== undefined) setLlmTemperature(config.temperature);
    if (config.maxTokens !== undefined) setLlmMaxTokens(config.maxTokens);
    if (config.topP !== undefined) setLlmTopP(config.topP);
    if (config.sampleCount !== undefined) setLlmSampleCount(config.sampleCount);
    if (config.frequencyPenalty !== undefined) setLlmFrequencyPenalty(config.frequencyPenalty);
    if (config.responseFormat !== undefined) setLlmResponseFormat(config.responseFormat);
    if (config.enableThinking !== undefined) setLlmEnableThinking(config.enableThinking);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_llm_generation",
        llm_temperature: config.temperature ?? llmTemperature,
        llm_max_tokens: config.maxTokens ?? llmMaxTokens,
        llm_top_p: config.topP ?? llmTopP,
        llm_sample_count: config.sampleCount ?? llmSampleCount,
        llm_frequency_penalty: config.frequencyPenalty ?? llmFrequencyPenalty,
        llm_response_format: config.responseFormat ?? llmResponseFormat,
        llm_enable_thinking: config.enableThinking ?? llmEnableThinking,
      });
    }
  }, [canSendRef, sendWsEventRef, llmTemperature, llmMaxTokens, llmTopP, llmSampleCount, llmFrequencyPenalty, llmResponseFormat, llmEnableThinking, setLlmTemperature, setLlmMaxTokens, setLlmTopP, setLlmSampleCount, setLlmFrequencyPenalty, setLlmResponseFormat, setLlmEnableThinking]);

  const handleDangerAlertChange = useCallback((enabled: boolean) => {
    setDangerAlertEnabled(enabled);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_alert_settings",
        danger_alert_enabled: enabled,
        oil_smoke_alert_enabled: oilSmokeAlertEnabled,
      });
    }
  }, [canSendRef, sendWsEventRef, oilSmokeAlertEnabled, setDangerAlertEnabled]);

  const handleOilSmokeAlertChange = useCallback((enabled: boolean) => {
    setOilSmokeAlertEnabled(enabled);
    if (canSendRef.current) {
      sendWsEventRef.current?.("control", {
        action: "set_alert_settings",
        danger_alert_enabled: dangerAlertEnabled,
        oil_smoke_alert_enabled: enabled,
      });
    }
  }, [canSendRef, sendWsEventRef, dangerAlertEnabled, setOilSmokeAlertEnabled]);

  // 后端优先：根据 /config 返回的配置同步前端 state 和 localStorage
  const syncFromBackendConfig = useCallback((config: Record<string, any>) => {
    console.log('[useModelConfig] 从后端 /config 同步配置:', config);

    if (config.tts_provider) {
      setTtsProvider(config.tts_provider as ProviderId);
      saveToStorage(STORAGE_KEYS.ttsProvider, config.tts_provider);
    }
    if (config.tts_model) {
      setTtsModel(config.tts_model);
      saveToStorage(STORAGE_KEYS.ttsModel, config.tts_model);
    }
    if (config.tts_voice) {
      setTtsVoice(config.tts_voice);
      saveToStorage(STORAGE_KEYS.ttsVoice, config.tts_voice);
    }
    if (config.stt_provider) {
      setSttProvider(config.stt_provider as ProviderId);
      saveToStorage(STORAGE_KEYS.sttProvider, config.stt_provider);
    }
    if (config.stt_model) {
      setSttModel(config.stt_model);
      saveToStorage(STORAGE_KEYS.sttModel, config.stt_model);
    }
    // provider 与模型成对同步：/config 返回 vlm provider/model
    if (config.vlm_provider) {
      setVlmProvider(config.vlm_provider);
      saveToStorage(STORAGE_KEYS.vlmProvider, config.vlm_provider);
    }
    if (config.vlm_model) {
      setVlmModel(config.vlm_model);
      saveToStorage(STORAGE_KEYS.vlmModel, config.vlm_model);
    }
    if (config.vision_enabled !== undefined) {
      setVisionEnabled(Boolean(config.vision_enabled));
      saveToStorage(STORAGE_KEYS.visionEnabled, Boolean(config.vision_enabled));
    }
    if (config.vision_fps !== undefined) {
      setVisionFps(Number(config.vision_fps));
      saveToStorage(STORAGE_KEYS.visionFps, Number(config.vision_fps));
    }

    // 同步 API keys：后端为权威，无条件覆盖（含空值）；不再写入 localStorage
    const backendApiKeys = config.api_keys || {};
    if (Object.keys(backendApiKeys).length > 0) {
      setApiKeys(prev => {
        const next = { ...prev };
        (Object.keys(backendApiKeys) as Array<keyof ApiKeys>).forEach(k => {
          if (k in next) next[k] = backendApiKeys[k] ?? "";
        });
        return next;
      });
    }
  }, [setTtsProvider, setTtsModel, setTtsVoice, setSttProvider, setSttModel, setVlmModel, setVisionEnabled, setVisionFps, setApiKeys]);

  return {
    visionEnabled, setVisionEnabled,
    visionFps, setVisionFps,
    ttsEnabled, setTtsEnabled,
    vlmProvider, setVlmProvider,
    vlmModel, setVlmModel,
    vlmModelMap,
    sttProvider, setSttProvider,
    sttModel, setSttModel,
    sttModelMap,
    ttsProvider, setTtsProvider,
    ttsModel, setTtsModel,
    ttsModelMap,
    ttsVoice, setTtsVoice,
    llmTemperature, setLlmTemperature,
    llmMaxTokens, setLlmMaxTokens,
    llmTopP, setLlmTopP,
    llmSampleCount, setLlmSampleCount,
    llmFrequencyPenalty, setLlmFrequencyPenalty,
    llmResponseFormat, setLlmResponseFormat,
    llmEnableThinking, setLlmEnableThinking,
          dangerAlertEnabled, setDangerAlertEnabled,
          cameraSourceMode, setCameraSourceMode,
          cameraRotation, setCameraRotation,
    oilSmokeAlertEnabled, setOilSmokeAlertEnabled,
    apiKeys, setApiKeys,
    customPrompt, setCustomPrompt,
    handleVlmProviderChange,
    handleVlmModelChange,
    edgeKwsUrl,
    setEdgeKwsUrl,
    edgeAsrUrl,
    setEdgeAsrUrl,
    sttPreferCloud,
    setSttPreferCloud,
    handleTtsProviderChange,
    handleTtsVoiceChange,
    handleTtsModelChange,
    handleSttProviderChange,
    handleSttModelChange,
    handleApiKeyChange,
    syncFromBackendConfig,
    handleLlmGenerationChange,
    handleDangerAlertChange,
    handleOilSmokeAlertChange,
  };
}
