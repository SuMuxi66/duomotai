export const CONFIG_VERSION = "20260414-llm-generation";

export const STORAGE_KEYS = {
  configVersion: "multimodal_config_version",
  vlmProvider: "multimodal_vlm_provider",
  vlmModel: "multimodal_vlm_model",
  llmTemperature: "multimodal_llm_temperature",
  llmMaxTokens: "multimodal_llm_max_tokens",
  llmTopP: "multimodal_llm_top_p",
  llmSampleCount: "multimodal_llm_sample_count",
  llmFrequencyPenalty: "multimodal_llm_frequency_penalty",
  llmResponseFormat: "multimodal_llm_response_format",
  llmEnableThinking: "multimodal_llm_enable_thinking",
  vlmModelMap: "multimodal_vlm_model_map",
  sttProvider: "multimodal_stt_provider",
  sttModel: "multimodal_stt_model",
  sttModelMap: "multimodal_stt_model_map",
  edgeKwsUrl: "multimodal_edge_kws_url",
  edgeAsrUrl: "multimodal_edge_asr_url",
  /** 唤醒词模式识别策略：true=云端优先（端侧兜底），false=纯端侧（低延迟） */
  sttPreferCloud: "multimodal_stt_prefer_cloud",
  ttsProvider: "multimodal_tts_provider",
  ttsModel: "multimodal_tts_model",
  ttsModelMap: "multimodal_tts_model_map",
  ttsVoice: "multimodal_tts_voice",
  // API key 不持久化在前端：权威在后端 .env.local（GET /config 下发，显式保存走 WS 通道）
  visionEnabled: "multimodal_vision_enabled",
  visionFps: "multimodal_vision_fps",
  ttsEnabled: "multimodal_tts_enabled",
  chatPanelWidth: "multimodal_chat_panel_width",
  themeMode: "multimodal_theme_mode",
  customPrompt: "multimodal_custom_prompt",
  activeSessionId: "multimodal_active_session_id",
  cameraSource: "multimodal_camera_source",
  glassesCameraIp: "multimodal_glasses_camera_ip",
  customVlmProvider: "multimodal_custom_vlm_provider",
  customVlmProviders: "multimodal_custom_vlm_providers",
  vlmModelListCache: "multimodal_vlm_model_list_cache",
} as const;

export function loadFromStorage<T>(key: string, defaultValue: T): T {
  try {
    const stored = localStorage.getItem(key);
    if (stored !== null) {
      return JSON.parse(stored) as T;
    }
  } catch (e) {
    console.error(`Failed to load ${key} from storage:`, e);
  }
  return defaultValue;
}

export function loadRawFromStorage(key: string, defaultValue: string): string {
  try {
    const stored = localStorage.getItem(key);
    if (stored !== null) {
      return stored;
    }
  } catch (e) {
    console.error(`Failed to load ${key} from storage:`, e);
  }
  return defaultValue;
}

export function saveToStorage<T>(key: string, value: T): void {
  try {
    if (localStorage.getItem(STORAGE_KEYS.configVersion) === null) {
      localStorage.setItem(STORAGE_KEYS.configVersion, CONFIG_VERSION);
    }
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.error(`Failed to save ${key} to storage:`, e);
  }
}

export function loadDeviceFromStorage(key: string, defaultValue: string): string {
  return loadRawFromStorage(key, defaultValue);
}

export function saveRawToStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.error(`Failed to save ${key} to storage:`, e);
  }
}

export function checkAndResetConfig(): void {
  try {
    const storedVersionRaw = localStorage.getItem(STORAGE_KEYS.configVersion);
    const storedVersion = storedVersionRaw ? storedVersionRaw.replace(/^"|"$/g, "") : null;
    if (storedVersion !== CONFIG_VERSION) {
      console.log(`[Config] 配置版本变化 (${storedVersion} -> ${CONFIG_VERSION})，正在重置配置...`);
      Object.values(STORAGE_KEYS).forEach(key => {
        localStorage.removeItem(key);
      });
      localStorage.setItem(STORAGE_KEYS.configVersion, CONFIG_VERSION);
      console.log('[Config] 配置已重置为默认值');
    }
  } catch (e) {
    console.error('[Config] 检查配置版本失败:', e);
  }
}
