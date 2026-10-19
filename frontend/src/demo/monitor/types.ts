export type DangerLevel = "low" | "medium" | "high";

export interface VlmRecognition {
  scene: string;
  items: string[];
  danger_level: DangerLevel;
  confidence: number;
  timestamp: number;
}

export interface AsrTranscript {
  text: string;
  is_final: boolean;
  timestamp: number;
}

export interface StepProgress {
  current_step: number;
  total_steps: number;
  step_title: string;
  progress: number;
  timestamp: number;
}

export interface DeviceStatus {
  fan_on: boolean;
  temp_sensor_active: boolean;
  weight_sensor_active: boolean;
  gas_alarm: boolean;
  temperature_c?: number;
  weight_g?: number;
  timestamp: number;
}

export interface Latency {
  e2e_ms: number;
  vlm_ms: number;
  stt_ms: number;
  llm_ms: number;
  tts_ms: number;
  timestamp: number;
}

export type TelemetryEventType =
  | "vlm_recognition"
  | "asr_transcript"
  | "step_progress"
  | "device_status"
  | "latency";

export interface TelemetryEvent {
  type: TelemetryEventType;
  payload:
    | VlmRecognition
    | AsrTranscript
    | StepProgress
    | DeviceStatus
    | Latency;
}
