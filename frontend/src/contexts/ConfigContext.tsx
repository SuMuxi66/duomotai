import { createContext, useContext, useState, useCallback, useMemo } from "react";
import { ProviderId } from "../components/ProviderIcon";
import { usePersistedState } from "../hooks/usePersistedState";
import { STORAGE_KEYS, loadFromStorage } from "../lib/storage";
import { systemDefaults } from "../lib/config";
import { ApiKeys } from "../types";

interface ConfigContextValue {
  vlmProvider: ProviderId;
  vlmModel: string;
  sttProvider: ProviderId;
  sttModel: string;
  ttsProvider: ProviderId;
  ttsModel: string;
  ttsVoice: string;
  visionEnabled: boolean;
  visionFps: number;
  ttsEnabled: boolean;
  llmTemperature: number;
  llmMaxTokens: number;
  llmTopP: number;
  llmSampleCount: number;
  llmFrequencyPenalty: number;
  llmResponseFormat: "md" | "json";
  llmEnableThinking: boolean;
  customPrompt: string;
  apiKeys: ApiKeys;
  vlmModelMap: Record<string, string>;
  sttModelMap: Record<string, string>;
  ttsModelMap: Record<string, string>;
  setVlmProvider: (v: ProviderId) => void;
  setVlmModel: (v: string) => void;
  setSttProvider: (v: ProviderId) => void;
  setSttModel: (v: string) => void;
  setTtsProvider: (v: ProviderId) => void;
  setTtsModel: (v: string) => void;
  setTtsVoice: (v: string) => void;
  setVisionEnabled: (v: boolean) => void;
  setVisionFps: (v: number) => void;
  setTtsEnabled: (v: boolean) => void;
  setLlmTemperature: (v: number) => void;
  setLlmMaxTokens: (v: number) => void;
  setLlmTopP: (v: number) => void;
  setLlmSampleCount: (v: number) => void;
  setLlmFrequencyPenalty: (v: number) => void;
  setLlmResponseFormat: (v: "md" | "json") => void;
  setLlmEnableThinking: (v: boolean) => void;
  setCustomPrompt: (v: string) => void;
  setApiKey: (provider: ProviderId, key: string) => void;
  setVlmModelMap: (v: Record<string, string>) => void;
  setSttModelMap: (v: Record<string, string>) => void;
  setTtsModelMap: (v: Record<string, string>) => void;
}

const ConfigContext = createContext<ConfigContextValue | null>(null);

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const [vlmProvider, setVlmProvider] = usePersistedState<ProviderId>(STORAGE_KEYS.vlmProvider, systemDefaults.vlmProvider as ProviderId);
  const [vlmModel, setVlmModel] = usePersistedState(STORAGE_KEYS.vlmModel, systemDefaults.vlmModel);
  const [sttProvider, setSttProvider] = usePersistedState<ProviderId>(STORAGE_KEYS.sttProvider, "browser" as ProviderId);
  const [sttModel, setSttModel] = usePersistedState(STORAGE_KEYS.sttModel, "Web Speech API");
  const [ttsProvider, setTtsProvider] = usePersistedState<ProviderId>(STORAGE_KEYS.ttsProvider, systemDefaults.ttsProvider as ProviderId);
  const [ttsModel, setTtsModel] = usePersistedState(STORAGE_KEYS.ttsModel, systemDefaults.ttsModel);
  const [ttsVoice, setTtsVoice] = usePersistedState(STORAGE_KEYS.ttsVoice, systemDefaults.ttsVoice);
  const [visionEnabled, setVisionEnabled] = usePersistedState(STORAGE_KEYS.visionEnabled, systemDefaults.visionEnabled);
  const [visionFps, setVisionFps] = usePersistedState(STORAGE_KEYS.visionFps, systemDefaults.visionFps);
  const [ttsEnabled, setTtsEnabled] = usePersistedState(STORAGE_KEYS.ttsEnabled, systemDefaults.ttsEnabled);
  const [llmTemperature, setLlmTemperature] = usePersistedState(STORAGE_KEYS.llmTemperature, systemDefaults.llmTemperature);
  const [llmMaxTokens, setLlmMaxTokens] = usePersistedState(STORAGE_KEYS.llmMaxTokens, systemDefaults.llmMaxTokens);
  const [llmTopP, setLlmTopP] = usePersistedState(STORAGE_KEYS.llmTopP, systemDefaults.llmTopP);
  const [llmSampleCount, setLlmSampleCount] = usePersistedState(STORAGE_KEYS.llmSampleCount, systemDefaults.llmSampleCount);
  const [llmFrequencyPenalty, setLlmFrequencyPenalty] = usePersistedState(STORAGE_KEYS.llmFrequencyPenalty, systemDefaults.llmFrequencyPenalty);
  const [llmResponseFormat, setLlmResponseFormat] = usePersistedState<"md" | "json">(STORAGE_KEYS.llmResponseFormat, systemDefaults.llmResponseFormat);
  const [llmEnableThinking, setLlmEnableThinking] = usePersistedState<boolean>(STORAGE_KEYS.llmEnableThinking, systemDefaults.llmEnableThinking ?? false);
  const [customPrompt, setCustomPrompt] = usePersistedState(STORAGE_KEYS.customPrompt, "");
  // key 权威在后端（.env.local 经 GET /config 下发），前端不持久化（本 Context 当前无挂载方，仅为兼容保留）
  const [apiKeys, setApiKeys] = useState<ApiKeys>(() => ({
    modelscope: "",
    siliconflow: "",
    dashscope: "",
    xiaomi: "",
    deepseek: "",
    zhipu: "",
    sensenova: "",
  }));
  const [vlmModelMap, setVlmModelMap] = useState<Record<string, string>>(() => loadFromStorage(STORAGE_KEYS.vlmModelMap, {}));
  const [sttModelMap, setSttModelMap] = useState<Record<string, string>>(() => loadFromStorage(STORAGE_KEYS.sttModelMap, {}));
  const [ttsModelMap, setTtsModelMap] = useState<Record<string, string>>(() => loadFromStorage(STORAGE_KEYS.ttsModelMap, {}));

  const setApiKey = useCallback((provider: ProviderId, key: string) => {
    setApiKeys(prev => ({ ...prev, [provider]: key }));
  }, []);

  const contextValue = useMemo(() => ({
    vlmProvider, vlmModel, sttProvider, sttModel,
    ttsProvider, ttsModel, ttsVoice, visionEnabled, visionFps, ttsEnabled,
    llmTemperature, llmMaxTokens, llmTopP, llmSampleCount, llmFrequencyPenalty,
    llmResponseFormat, llmEnableThinking, customPrompt, apiKeys,
    vlmModelMap, sttModelMap, ttsModelMap,
    setVlmProvider, setVlmModel, setSttProvider, setSttModel,
    setTtsProvider, setTtsModel, setTtsVoice,
    setVisionEnabled, setVisionFps, setTtsEnabled,
    setLlmTemperature, setLlmMaxTokens, setLlmTopP, setLlmSampleCount,
    setLlmFrequencyPenalty, setLlmResponseFormat, setLlmEnableThinking,
    setCustomPrompt, setApiKey,
    setVlmModelMap, setSttModelMap, setTtsModelMap,
  }), [
    vlmProvider, vlmModel, sttProvider, sttModel,
    ttsProvider, ttsModel, ttsVoice, visionEnabled, visionFps, ttsEnabled,
    llmTemperature, llmMaxTokens, llmTopP, llmSampleCount, llmFrequencyPenalty,
    llmResponseFormat, llmEnableThinking, customPrompt, apiKeys,
    vlmModelMap, sttModelMap, ttsModelMap,
  ]);

  return (
    <ConfigContext.Provider value={contextValue}>
      {children}
    </ConfigContext.Provider>
  );
}

export function useConfig() {
  const ctx = useContext(ConfigContext);
  if (!ctx) throw new Error("useConfig must be used within ConfigProvider");
  return ctx;
}
