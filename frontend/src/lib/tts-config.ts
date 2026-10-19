export const TTS_PROVIDER_MODELS: Record<string, string[]> = {
  browser: ["SpeechSynthesis API"],
  local_cosyvoice: ["CosyVoice-300M-SFT"],
  siliconflow: ["fnlp/MOSS-TTSD-v0.5", "IndexTeam/IndexTTS-2"],
  dashscope: ["qwen3-tts-instruct-flash-realtime"],
  xiaomi: ["mimo-v2.5-tts"],
};

export const TTS_PROVIDER_VOICES: Record<string, string[]> = {
  browser: ["default"],
  local_cosyvoice: ["中文女", "中文男", "英文女", "英文男", "日语女", "韩语女", "粤语女"],
  dashscope: ["Cherry", "Ethan", "Chelsie", "Serena"],
  xiaomi: ["mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]
};

export const DEFAULT_TTS_VOICE = "fnlp/MOSS-TTSD-v0.5:claire";

export function getDefaultTtsModel(provider: string): string {
  return TTS_PROVIDER_MODELS[provider]?.[0] || "";
}

export function getDefaultTtsVoice(provider: string): string {
  return TTS_PROVIDER_VOICES[provider]?.[0] || DEFAULT_TTS_VOICE;
}

export function isValidTtsVoiceForProvider(provider: string, voice: string): boolean {
  if (!voice) {
    return false;
  }
  const voices = TTS_PROVIDER_VOICES[provider];
  if (voices) {
    return voices.includes(voice);
  }
  return voice === "default" || voice.startsWith("fnlp/MOSS-TTSD-v0.5:");
}
