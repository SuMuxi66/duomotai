/**
 * 统一配置管理模块
 *
 * 优先级: 用户配置(localStorage) > 系统配置(env)
 *
 * - 系统配置: 从 .env 文件读取，作为默认值
 * - 用户配置: 从 localStorage 读取，用户可在界面修改
 * - 用户配置存在时优先使用，否则使用系统配置
 * - API Base 自动获取本机 IP，无需手动配置
 */

import { getApiBaseUrl } from './network';

const env = (import.meta as any).env;

// 配置键名
const STORAGE_KEYS = {
  sttProvider: "multimodal_stt_provider",
  sttModel: "multimodal_stt_model",
  ttsProvider: "multimodal_tts_provider",
  ttsModel: "multimodal_tts_model",
  ttsVoice: "multimodal_tts_voice",
  llmTemperature: "multimodal_llm_temperature",
  llmMaxTokens: "multimodal_llm_max_tokens",
  llmTopP: "multimodal_llm_top_p",
  llmSampleCount: "multimodal_llm_sample_count",
  llmFrequencyPenalty: "multimodal_llm_frequency_penalty",
  llmResponseFormat: "multimodal_llm_response_format",
  llmEnableThinking: "multimodal_llm_enable_thinking",
  vlmModel: "multimodal_vlm_model",
  visionEnabled: "multimodal_vision_enabled",
  visionFps: "multimodal_vision_fps",
  ttsEnabled: "multimodal_tts_enabled",
  themeMode: "multimodal_theme_mode",
};

/**
 * 系统默认配置（从 env 读取）
 */
function getSystemDefaults() {
  // 如果环境变量设置了 API_BASE，使用它；否则自动获取
  const envApiBase = env.VITE_API_BASE;
  const autoApiBase = typeof window !== 'undefined' ? getApiBaseUrl() : '';
  
  return {
    apiBase: envApiBase || autoApiBase || "",

    // API key 不在前端配置/持久化：权威在后端 .env.local，经 GET /config 的 api_keys 下发

    // 功能默认值
    visionEnabled: (env.VITE_DEFAULT_VISION_ENABLED ?? "true") === "true",
    visionFps: parseFloat(env.VITE_DEFAULT_VISION_FPS || "0.2"),
    ttsEnabled: (env.VITE_DEFAULT_TTS_ENABLED ?? "true") === "true",

    // STT 配置
    sttProvider: env.VITE_DEFAULT_STT_PROVIDER || "browser",
    sttModel: env.VITE_DEFAULT_STT_MODEL || "Web Speech API",

    // TTS 配置
    ttsProvider: env.VITE_DEFAULT_TTS_PROVIDER || "xiaomi",
    ttsModel: env.VITE_DEFAULT_TTS_MODEL || "mimo-v2.5-tts",
    ttsVoice: env.VITE_DEFAULT_TTS_VOICE || "mimo_default",

    // 模型配置（文字与视觉统一走 VLM 端点）
    vlmProvider: env.VITE_DEFAULT_VLM_PROVIDER || "modelscope",
    vlmModel: env.VITE_DEFAULT_VLM_MODEL || "Qwen/Qwen3.8-27B",
    llmTemperature: parseFloat(env.VITE_DEFAULT_LLM_TEMPERATURE || "0.7"),
    llmMaxTokens: parseInt(env.VITE_DEFAULT_LLM_MAX_TOKENS || "2048", 10),
    llmTopP: parseFloat(env.VITE_DEFAULT_LLM_TOP_P || "0.95"),
    llmSampleCount: parseInt(env.VITE_DEFAULT_LLM_SAMPLE_COUNT || "1", 10),
    llmFrequencyPenalty: parseFloat(env.VITE_DEFAULT_LLM_FREQUENCY_PENALTY || "0"),
    llmResponseFormat: (env.VITE_DEFAULT_LLM_RESPONSE_FORMAT || "md") as "md" | "json",
    llmEnableThinking: env.VITE_DEFAULT_LLM_ENABLE_THINKING === "true",
  };
}

/**
 * 从 localStorage 读取用户配置
 */
function getUserConfig() {
  const userConfig: Record<string, any> = {};
  try {
    Object.entries(STORAGE_KEYS).forEach(([key, storageKey]) => {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) {
        try {
          userConfig[key] = JSON.parse(stored);
        } catch {
          userConfig[key] = stored;
        }
      }
    });
  } catch (e) {
    console.error("[Config] 读取用户配置失败:", e);
  }
  return userConfig;
}

/**
 * 合并配置：用户配置优先于系统配置
 */
function mergeConfig(systemDefaults: ReturnType<typeof getSystemDefaults>, userConfig: Record<string, any>) {
  return {
    ...systemDefaults,
    // 用户配置覆盖系统配置
    ...(userConfig.sttProvider !== undefined && { sttProvider: userConfig.sttProvider }),
    ...(userConfig.sttModel !== undefined && { sttModel: userConfig.sttModel }),
    ...(userConfig.ttsProvider !== undefined && { ttsProvider: userConfig.ttsProvider }),
    ...(userConfig.ttsModel !== undefined && { ttsModel: userConfig.ttsModel }),
    ...(userConfig.ttsVoice !== undefined && { ttsVoice: userConfig.ttsVoice }),
    ...(userConfig.llmTemperature !== undefined && { llmTemperature: userConfig.llmTemperature }),
    ...(userConfig.llmMaxTokens !== undefined && { llmMaxTokens: userConfig.llmMaxTokens }),
    ...(userConfig.llmTopP !== undefined && { llmTopP: userConfig.llmTopP }),
    ...(userConfig.llmSampleCount !== undefined && { llmSampleCount: userConfig.llmSampleCount }),
    ...(userConfig.llmFrequencyPenalty !== undefined && { llmFrequencyPenalty: userConfig.llmFrequencyPenalty }),
    ...(userConfig.llmResponseFormat !== undefined && { llmResponseFormat: userConfig.llmResponseFormat }),
    ...(userConfig.llmEnableThinking !== undefined && { llmEnableThinking: userConfig.llmEnableThinking }),
    ...(userConfig.vlmModel !== undefined && { vlmModel: userConfig.vlmModel }),
    ...(userConfig.visionEnabled !== undefined && { visionEnabled: userConfig.visionEnabled }),
    ...(userConfig.visionFps !== undefined && { visionFps: userConfig.visionFps }),
    ...(userConfig.ttsEnabled !== undefined && { ttsEnabled: userConfig.ttsEnabled }),
    ...(userConfig.themeMode !== undefined && { themeMode: userConfig.themeMode }),
  };
}

// 导出类型
export type AppConfig = ReturnType<typeof mergeConfig>;

// 系统默认配置
export const systemDefaults = getSystemDefaults();

// 当前配置（用户配置 + 系统默认）
let currentConfig = mergeConfig(systemDefaults, getUserConfig());

/**
 * 获取当前配置
 */
export function getConfig(): AppConfig {
  return currentConfig;
}

/**
 * 刷新配置（重新从 localStorage 读取）
 */
export function refreshConfig() {
  currentConfig = mergeConfig(systemDefaults, getUserConfig());
  return currentConfig;
}

/**
 * 保存用户配置到 localStorage
 */
export function setUserConfig(key: keyof typeof STORAGE_KEYS, value: any) {
  const storageKey = STORAGE_KEYS[key as keyof typeof STORAGE_KEYS];
  if (!storageKey) {
    console.warn(`[Config] 未知的配置键: ${key}`);
    return;
  }
  try {
    localStorage.setItem(storageKey, JSON.stringify(value));
    refreshConfig();
  } catch (e) {
    console.error(`[Config] 保存配置失败 ${key}:`, e);
  }
}

/**
 * 清除用户配置，恢复系统默认
 */
export function resetUserConfig() {
  try {
    Object.values(STORAGE_KEYS).forEach(key => {
      localStorage.removeItem(key);
    });
    refreshConfig();
    console.log("[Config] 已恢复系统默认配置");
  } catch (e) {
    console.error("[Config] 重置配置失败:", e);
  }
}

/**
 * 打印当前配置（用于调试）
 */
export function printConfig() {
  const cfg = getConfig();
  console.log("[Config] 当前配置:");
  console.log(`  - STT: ${cfg.sttProvider} / ${cfg.sttModel}`);
  console.log(`  - TTS: ${cfg.ttsProvider} / ${cfg.ttsModel}`);
  console.log(`  - API Base: ${cfg.apiBase}`);
  console.log(`  - 使用用户配置: ${Object.keys(getUserConfig()).length > 0 ? "是" : "否"}`);
}

// 导出快捷访问
export const config = getConfig();
export const API_BASE = config.apiBase;
export const STORAGE = STORAGE_KEYS;

/**
 * 从后端同步配置
 * 使用相对路径，让 Vite 代理处理请求
 */
export async function syncConfigFromBackend(): Promise<boolean> {
  try {
    const response = await fetch(`/config`);
    if (!response.ok) {
      console.error("[Config] 从后端获取配置失败:", response.status);
      return false;
    }
    
    const backendConfig = await response.json();
    console.log("[Config] 从后端获取配置:", backendConfig);
    
    // 更新localStorage中的配置
    if (backendConfig.stt_provider) {
      localStorage.setItem(STORAGE_KEYS.sttProvider, JSON.stringify(backendConfig.stt_provider));
    }
    if (backendConfig.stt_model) {
      localStorage.setItem(STORAGE_KEYS.sttModel, JSON.stringify(backendConfig.stt_model));
    }
    if (backendConfig.tts_provider) {
      localStorage.setItem(STORAGE_KEYS.ttsProvider, JSON.stringify(backendConfig.tts_provider));
    }
    if (backendConfig.tts_model) {
      localStorage.setItem(STORAGE_KEYS.ttsModel, JSON.stringify(backendConfig.tts_model));
    }
    if (backendConfig.tts_voice) {
      localStorage.setItem(STORAGE_KEYS.ttsVoice, JSON.stringify(backendConfig.tts_voice));
    }
    if (backendConfig.vision_enabled !== undefined) {
      localStorage.setItem(STORAGE_KEYS.visionEnabled, JSON.stringify(backendConfig.vision_enabled));
    }
    if (backendConfig.vision_fps !== undefined) {
      localStorage.setItem(STORAGE_KEYS.visionFps, JSON.stringify(backendConfig.vision_fps));
    }
    if (backendConfig.vlm_model) {
      localStorage.setItem(STORAGE_KEYS.vlmModel, JSON.stringify(backendConfig.vlm_model));
    }
    if (backendConfig.llm_temperature !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmTemperature, JSON.stringify(backendConfig.llm_temperature));
    }
    if (backendConfig.llm_max_tokens !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmMaxTokens, JSON.stringify(backendConfig.llm_max_tokens));
    }
    if (backendConfig.llm_top_p !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmTopP, JSON.stringify(backendConfig.llm_top_p));
    }
    if (backendConfig.llm_sample_count !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmSampleCount, JSON.stringify(backendConfig.llm_sample_count));
    }
    if (backendConfig.llm_frequency_penalty !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmFrequencyPenalty, JSON.stringify(backendConfig.llm_frequency_penalty));
    }
    if (backendConfig.llm_response_format) {
      localStorage.setItem(STORAGE_KEYS.llmResponseFormat, JSON.stringify(backendConfig.llm_response_format));
    }
    if (backendConfig.llm_enable_thinking !== undefined) {
      localStorage.setItem(STORAGE_KEYS.llmEnableThinking, JSON.stringify(backendConfig.llm_enable_thinking));
    }
    if (backendConfig.vlm_model) {
      localStorage.setItem(STORAGE_KEYS.vlmModel, JSON.stringify(backendConfig.vlm_model));
    }
    
    // 刷新配置
    refreshConfig();
    console.log("[Config] 配置同步完成");
    return true;
  } catch (e) {
    console.error("[Config] 同步配置失败:", e);
    return false;
  }
}
