import type {
  AsrTranscript,
  DeviceStatus,
  Latency,
  StepProgress,
  TelemetryEvent,
  VlmRecognition,
} from "./types";

const VLM_SCENES: Array<{ scene: string; items: string[]; danger: VlmRecognition["danger_level"] }> = [
  { scene: "切菜中", items: ["菜刀", "砧板", "青菜"], danger: "low" },
  { scene: "热油起锅", items: ["炒锅", "食用油", "蒜末"], danger: "medium" },
  { scene: "翻炒中", items: ["炒锅", "食材", "锅铲"], danger: "low" },
  { scene: "装盘点缀", items: ["盘子", "菜品"], danger: "low" },
  { scene: "监测到锅盖剧烈跳动", items: ["锅盖", "蒸汽"], danger: "high" },
  { scene: "油烟浓度偏高", items: ["油烟", "炒锅"], danger: "medium" },
  { scene: "加水炖煮", items: ["汤锅", "清水"], danger: "low" },
];

const ASR_SENTENCES = [
  "小云，帮我看看锅里的水开了没有",
  "现在转到小火再煮三分钟",
  "帮我设一个五分钟的定时器",
  "下一步该做什么",
  "今天的菜看起来不错",
  "提醒我五分钟后翻面",
];

const STEP_TITLES = [
  "准备食材与厨具",
  "切配主料",
  "热油爆香",
  "下锅翻炒",
  "调味收汁",
  "装盘点缀",
];

const TOTAL_STEPS = STEP_TITLES.length;
const rand = (min: number, max: number) => Math.floor(min + Math.random() * (max - min + 1));
const pick = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];

export type TelemetrySink = (evt: TelemetryEvent) => void;

export function startMockTelemetry(sink: TelemetrySink): () => void {
  let stopped = false;
  const timers: ReturnType<typeof setInterval>[] = [];

  let step = 1;
  let asrIdx = 0;
  let asrRevealed = 0;

  const emit = (type: TelemetryEvent["type"], payload: TelemetryEvent["payload"]) => {
    if (!stopped) sink({ type, payload });
  };

  timers.push(
    setInterval(() => {
      const s = pick(VLM_SCENES);
      const payload: VlmRecognition = {
        scene: s.scene,
        items: s.items,
        danger_level: s.danger,
        confidence: Math.round(0.78 + Math.random() * 0.2 * 1000) / 1000,
        timestamp: Date.now(),
      };
      emit("vlm_recognition", payload);
    }, 2000)
  );

  timers.push(
    setInterval(() => {
      const sentence = ASR_SENTENCES[asrIdx];
      asrRevealed = Math.min(sentence.length, asrRevealed + rand(1, 3));
      if (asrRevealed >= sentence.length) {
        const payload: AsrTranscript = { text: sentence, is_final: true, timestamp: Date.now() };
        emit("asr_transcript", payload);
        asrIdx = (asrIdx + 1) % ASR_SENTENCES.length;
        asrRevealed = 0;
      } else {
        const payload: AsrTranscript = {
          text: sentence.slice(0, asrRevealed),
          is_final: false,
          timestamp: Date.now(),
        };
        emit("asr_transcript", payload);
      }
    }, 700)
  );

  timers.push(
    setInterval(() => {
      if (Math.random() < 0.35 && step < TOTAL_STEPS) step += 1;
      const payload: StepProgress = {
        current_step: step,
        total_steps: TOTAL_STEPS,
        step_title: STEP_TITLES[Math.min(step - 1, STEP_TITLES.length - 1)],
        progress: Math.round((step / TOTAL_STEPS) * 1000) / 1000,
        timestamp: Date.now(),
      };
      emit("step_progress", payload);
    }, 5000)
  );

  timers.push(
    setInterval(() => {
      const payload: DeviceStatus = {
        fan_on: Math.random() < 0.7,
        temp_sensor_active: Math.random() < 0.9,
        weight_sensor_active: Math.random() < 0.8,
        gas_alarm: Math.random() < 0.05,
        temperature_c: Math.round((95 + Math.random() * 90) * 10) / 10,
        weight_g: rand(120, 880),
        timestamp: Date.now(),
      };
      emit("device_status", payload);
    }, 3000)
  );

  timers.push(
    setInterval(() => {
      const vlm = rand(80, 240);
      const stt = rand(60, 180);
      const llm = rand(220, 640);
      const tts = rand(90, 260);
      const payload: Latency = {
        e2e_ms: vlm + stt + llm + tts + rand(20, 80),
        vlm_ms: vlm,
        stt_ms: stt,
        llm_ms: llm,
        tts_ms: tts,
        timestamp: Date.now(),
      };
      emit("latency", payload);
    }, 1000)
  );

  emit("step_progress", {
    current_step: step,
    total_steps: TOTAL_STEPS,
    step_title: STEP_TITLES[0],
    progress: Math.round((1 / TOTAL_STEPS) * 1000) / 1000,
    timestamp: Date.now(),
  });
  emit("device_status", {
    fan_on: true,
    temp_sensor_active: true,
    weight_sensor_active: true,
    gas_alarm: false,
    temperature_c: 142.5,
    weight_g: 360,
    timestamp: Date.now(),
  });

  return () => {
    stopped = true;
    timers.forEach(clearInterval);
  };
}
