export type SpeechRecognitionState = 'idle' | 'waiting' | 'listening' | 'analyzing';
export type AIResponseState = 'idle' | 'thinking' | 'responding';
export type STTPipeline = 'none' | 'push_to_talk';

export interface Msg {
  role: 'user' | 'assistant' | 'assistant_stream' | 'vision' | 'system';
  text: string;
  requestId?: string;
  reasoning?: string;
  reasoningExpanded?: boolean;
  reasoningDurationMs?: number;
  timestamp?: number;
}

export type LlmResponseFormat = 'md' | 'json';

export interface LlmGenerationConfig {
  temperature: number;
  maxTokens: number;
  topP: number;
  sampleCount: number;
  frequencyPenalty: number;
  responseFormat: LlmResponseFormat;
}

export interface ChatSessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  title?: string | null;
  preview?: string;
  message_count: number;
}

export interface DeviceInfo {
  audioDevices: MediaDeviceInfo[];
  audioOutputDevices: MediaDeviceInfo[];
  hasAudioOutput: boolean;
  videoDevices: MediaDeviceInfo[];
}

export interface ApiKeys {
  modelscope: string;
  siliconflow: string;
  dashscope: string;
  xiaomi: string;
  deepseek: string;
  zhipu: string;
  sensenova: string;
}
