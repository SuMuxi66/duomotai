import type { ProviderId } from "@/components/ProviderIcon";
import type { Theme } from "@/lib/theme";

export interface ModelProvider {
  id: ProviderId;
  name: string;
  models: string[];
  hasApiKey: boolean;
  /** 是否支持从 /v1/models 自动拉取模型列表 */
  supportsFetch?: boolean;
  /** 是否为系统预设 */
  isPreset?: boolean;
}

export interface CustomProviderConfig {
  id: string;
  name: string;
  apiFormat: "openai" | "anthropic";
  baseUrl: string;
  apiKey: string;
  model: string;
}

export interface ModelSelectorProps {
  vlmProvider: ProviderId | string;
  vlmModel: string;
  visionFps: number;
  sttProvider: ProviderId;
  sttModel: string;
  ttsProvider: ProviderId;
  ttsModel: string;
  ttsVoice: string;
  llmTemperature: number;
  llmMaxTokens: number;
  llmTopP: number;
  llmSampleCount: number;
  llmFrequencyPenalty: number;
  llmResponseFormat: "md" | "json";
  llmEnableThinking: boolean;
  dangerAlertEnabled: boolean;
  oilSmokeAlertEnabled: boolean;
  /** 摄像头数据源模式：network=香橙派网络摄像头（默认），local=本机 USB 摄像头 */
  cameraSourceMode: "network" | "local";
  isOpen: boolean;
  onClose: () => void;
  theme: Theme;
  onTestTts?: () => void;
  isTestingTts?: boolean;
  onVlmProviderChange: (provider: ProviderId | string, extras?: { base_url?: string; api_format?: string; api_key?: string; vlm_model?: string }) => void;
  onVlmModelChange: (model: string) => void;
  edgeKwsUrl?: string;
  edgeAsrUrl?: string;
  onEdgeUrlsChange?: (kwsUrl: string, asrUrl: string) => void;
  /**
   * 云端优先（端侧兜底）识别策略，唤醒词与持续识别/通话模式共用：
   * true=云端优先——唤醒后双发（云端先回用云端，准确率优先）；持续识别直接走云端（实时 partial）。
   * false=纯端侧（低延迟），端侧设备不在场时自动回退云端 STT。
   */
  sttPreferCloud?: boolean;
  onSttPreferCloudChange?: (preferCloud: boolean) => void;
  onVisionFpsChange: (fps: number) => void;
  onSttProviderChange: (provider: ProviderId, apiKey?: string) => void;
  onSttModelChange: (model: string) => void;
  onTtsProviderChange: (provider: ProviderId, apiKey?: string) => void;
  onTtsModelChange: (model: string) => void;
  onTtsVoiceChange: (voice: string) => void;
  onApiKeyChange: (provider: ProviderId, apiKey: string) => void;
  onLlmGenerationChange: (config: Partial<{
    temperature?: number;
    maxTokens?: number;
    topP?: number;
    sampleCount?: number;
    frequencyPenalty?: number;
    responseFormat?: "md" | "json";
    enableThinking?: boolean;
  }>) => void;
  onDangerAlertChange: (enabled: boolean) => void;
  onOilSmokeAlertChange: (enabled: boolean) => void;
  onCameraSourceModeChange: (mode: "network" | "local") => void;
  apiKeys: any;
}

// 静态回退清单：仅在后端 /providers/catalog 不可达时使用；正常以 registry 下发为准，
// 改动后端默认模型时同步这里，保证离线兜底不过期（2026-09-06 与 registry 对齐）
export const VLM_PROVIDERS: ModelProvider[] = [
  { id: "modelscope", name: "ModelScope", models: ["Qwen/Qwen3.8-27B"], hasApiKey: true, supportsFetch: true, isPreset: true },
  { id: "dashscope", name: "阿里云百炼", models: ["qwen3.8-27b"], hasApiKey: true, supportsFetch: true, isPreset: true },
  { id: "deepseek", name: "DeepSeek", models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"], hasApiKey: true, supportsFetch: true, isPreset: true },
  { id: "zhipu", name: "智谱GLM", models: ["glm-4.6v-flash"], hasApiKey: true, isPreset: true },
  { id: "sensenova", name: "SenseNova", models: ["sensenova-6.8-flash-lite"], hasApiKey: true, supportsFetch: true, isPreset: true },
  { id: "custom", name: "自定义 OpenAI/Anthropic", models: [], hasApiKey: true, supportsFetch: true, isPreset: true },
];

export const STT_PROVIDERS: ModelProvider[] = [
  { id: "browser", name: "浏览器内置", models: ["Web Speech API"], hasApiKey: false },
  { id: "siliconflow", name: "硅基流动", models: ["FunAudioLLM/SenseVoiceSmall"], hasApiKey: true, supportsFetch: true },
  { id: "qwen3_asr_realtime", name: "阿里云 Qwen3", models: ["qwen3-asr-flash-realtime-2026-02-10"], hasApiKey: false },
  // 端侧语音（香橙派）：唤醒词 KWS + 流式 ASR 全部运行在端侧设备（默认 ws://192.168.1.248:8767/8768），完全离线。
  // 链路分支见 useVoiceOrchestration：sttProvider !== "browser" 即走 createSttWakeWordListener（KWS/ASR 端侧）。
  { id: "edge", name: "端侧语音（香橙派）", models: ["FunASR 流式（香橙派端侧）"], hasApiKey: false },
];

export const TTS_VOICES: { id: string; name: string; language: string }[] = [
  { id: "default", name: "默认音色", language: "通用" },
  { id: "fnlp/MOSS-TTSD-v0.5:bella", name: "Bella（激情女声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:anna", name: "Anna（沉稳女声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:claire", name: "Claire（温柔女声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:diana", name: "Diana（欢快女声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:alex", name: "Alex（沉稳男声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:benjamin", name: "Benjamin（低沉男声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:charles", name: "Charles（磁性男声）", language: "中英" },
  { id: "fnlp/MOSS-TTSD-v0.5:david", name: "David（欢快男声）", language: "中英" },
];

export const TTS_PROVIDERS: ModelProvider[] = [
  { id: "browser", name: "浏览器内置", models: ["SpeechSynthesis API"], hasApiKey: false },
  { id: "siliconflow", name: "硅基流动", models: ["fnlp/MOSS-TTSD-v0.5", "IndexTeam/IndexTTS-2"], hasApiKey: true, supportsFetch: true },
  { id: "dashscope", name: "阿里云百炼", models: ["qwen3-tts-instruct-flash-realtime"], hasApiKey: true, supportsFetch: true },
  { id: "xiaomi", name: "小米MiMo", models: ["mimo-v2.5-tts"], hasApiKey: true, supportsFetch: true },
];

export const TTS_VOICES_DASHSCOPE: { id: string; name: string; language: string; models: string[] }[] = [
  { id: "Cherry", name: "Cherry（推荐）", language: "中文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Xiao Ming", name: "Xiao Ming", language: "中文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Xiao Yan", name: "Xiao Yan", language: "中文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Ernest", name: "Ernest", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Alice", name: "Alice", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Fred", name: "Fred", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Wenling", name: "Wenling", language: "中文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Bob", name: "Bob", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "Cathy", name: "Cathy", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
  { id: "David", name: "David", language: "英文", models: ["qwen3-tts-instruct-flash-realtime"] },
];

export const TTS_VOICES_XIAOMI: { id: string; name: string; language: string }[] = [
  { id: "mimo_default", name: "MiMo-默认", language: "跟随集群" },
  { id: "冰糖", name: "冰糖（女声）", language: "中文" },
  { id: "茉莉", name: "茉莉（女声）", language: "中文" },
  { id: "苏打", name: "苏打（男声）", language: "中文" },
  { id: "白桦", name: "白桦（男声）", language: "中文" },
  { id: "Mia", name: "Mia（女声）", language: "英文" },
  { id: "Chloe", name: "Chloe（女声）", language: "英文" },
  { id: "Milo", name: "Milo（男声）", language: "英文" },
  { id: "Dean", name: "Dean（男声）", language: "英文" },
];

export function getDefaultTtsVoice(provider: ProviderId): string {
  if (provider === "dashscope") return TTS_VOICES_DASHSCOPE[0]?.id || "Cherry";
  if (provider === "xiaomi") return TTS_VOICES_XIAOMI[0]?.id || "mimo_default";
  return TTS_VOICES[0]?.id || "default";
}

export function isValidTtsVoice(provider: ProviderId, voice: string): boolean {
  const options =
    provider === "dashscope"
      ? TTS_VOICES_DASHSCOPE
      : provider === "xiaomi"
        ? TTS_VOICES_XIAOMI
        : TTS_VOICES;
  return options.some((item) => item.id === voice);
}

export function getTtsVoiceOptions(
  provider: ProviderId,
  model: string,
): { value: string; label: string }[] {
  let voices: { id: string; name: string; language: string }[];
  if (provider === "dashscope") {
    voices = TTS_VOICES_DASHSCOPE.filter((v) => v.models.includes(model));
  } else if (provider === "xiaomi") {
    voices = TTS_VOICES_XIAOMI;
  } else {
    voices = TTS_VOICES;
  }
  return voices.map((v) => ({ value: v.id, label: `${v.name} (${v.language})` }));
}
