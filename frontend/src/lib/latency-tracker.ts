export type LatencyScenario = "text-only" | "vision-assisted";

export type LatencyRecord = {
  requestId: string;
  prompt: string;
  scenario: LatencyScenario;
  ttsProvider: string;
  startedAt: number;
  visionStartedAt?: number;
  visionFinishedAt?: number;
  temperatureStartedAt?: number;
  temperatureFinishedAt?: number;
  sentAt?: number;
  serverReceivedAt?: number;
  llmStartedAt?: number;
  firstTokenAt?: number;
  finalTextAt?: number;
  ttsStartedAt?: number;
  ttsPayloadReadyAt?: number;
  ttsPlaybackFinishedAt?: number;
  completedAt?: number;
};

export type LatencyStage =
  | "server_received"
  | "llm_start"
  | "llm_first_delta"
  | "llm_final"
  | "tts_start"
  | "tts_payload_ready";

type LatencyTimestampField =
  | "visionStartedAt"
  | "visionFinishedAt"
  | "temperatureStartedAt"
  | "temperatureFinishedAt"
  | "sentAt"
  | "serverReceivedAt"
  | "llmStartedAt"
  | "firstTokenAt"
  | "finalTextAt"
  | "ttsStartedAt"
  | "ttsPayloadReadyAt"
  | "ttsPlaybackFinishedAt"
  | "completedAt";

type StartLatencyRecordInput = {
  prompt: string;
  scenario: LatencyScenario;
  ttsProvider: string;
  startedAt?: number;
  requestId?: string;
};

type LatencySummary = {
  requestId: string;
  reason: string;
  prompt: string;
  scenario: LatencyScenario;
  ttsProvider: string;
  totalMs: number;
  prepareMs: number | null;
  visionMs: number | null;
  temperatureMs: number | null;
  firstTokenMs: number | null;
  finalTextMs: number | null;
  ttsReadyMs: number | null;
  ttsPlaybackMs: number | null;
  fullChainMs: number;
};

type BrowserWindow = Window & {
  __MULTIMODAL_LATENCY_REPORTS__?: LatencySummary[];
};

export function createLatencyRequestId() {
  return `lat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

const MAX_RECORDS = 100;
const MAX_REPORTS = 50;

export function createLatencyTracker() {
  const records: Record<string, LatencyRecord> = {};
  let latestRequestId: string | null = null;

  function getRecord(requestId: string | null | undefined) {
    if (!requestId) {
      return undefined;
    }
    return records[requestId];
  }

  function setLatestRequestId(requestId: string | null) {
    latestRequestId = requestId;
  }

  function getLatestRequestId() {
    return latestRequestId;
  }

  function startRecord({
    prompt,
    scenario,
    ttsProvider,
    startedAt = Date.now(),
    requestId = createLatencyRequestId(),
  }: StartLatencyRecordInput) {
    const keys = Object.keys(records);
    if (keys.length >= MAX_RECORDS) {
      const oldest = keys.sort((a, b) => records[a].startedAt - records[b].startedAt)[0];
      delete records[oldest];
    }
    records[requestId] = {
      requestId,
      prompt,
      scenario,
      ttsProvider,
      startedAt,
    };
    latestRequestId = requestId;
    return requestId;
  }

  function mark(requestId: string, patch: Partial<LatencyRecord>) {
    const current = records[requestId];
    if (!current) {
      return;
    }
    records[requestId] = { ...current, ...patch };
  }

  function markFirst(requestId: string, field: LatencyTimestampField, at = Date.now()) {
    const current = records[requestId];
    if (!current || current[field]) {
      return;
    }
    records[requestId] = { ...current, [field]: at };
  }

  function finalize(requestId: string, reason: string) {
    const record = records[requestId];
    if (!record || record.completedAt) {
      return null;
    }

    const completedAt = Date.now();
    const nextRecord = { ...record, completedAt };
    records[requestId] = nextRecord;

    const summary: LatencySummary = {
      requestId,
      reason,
      prompt: nextRecord.prompt,
      scenario: nextRecord.scenario,
      ttsProvider: nextRecord.ttsProvider,
      totalMs: completedAt - nextRecord.startedAt,
      prepareMs: nextRecord.sentAt ? nextRecord.sentAt - nextRecord.startedAt : null,
      visionMs:
        nextRecord.visionStartedAt && nextRecord.visionFinishedAt
          ? nextRecord.visionFinishedAt - nextRecord.visionStartedAt
          : null,
      temperatureMs:
        nextRecord.temperatureStartedAt && nextRecord.temperatureFinishedAt
          ? nextRecord.temperatureFinishedAt - nextRecord.temperatureStartedAt
          : null,
      firstTokenMs:
        nextRecord.firstTokenAt && nextRecord.sentAt ? nextRecord.firstTokenAt - nextRecord.sentAt : null,
      finalTextMs:
        nextRecord.finalTextAt && nextRecord.sentAt ? nextRecord.finalTextAt - nextRecord.sentAt : null,
      ttsReadyMs:
        nextRecord.ttsPayloadReadyAt && nextRecord.sentAt ? nextRecord.ttsPayloadReadyAt - nextRecord.sentAt : null,
      ttsPlaybackMs:
        nextRecord.ttsStartedAt ? completedAt - nextRecord.ttsStartedAt : null,
      fullChainMs: completedAt - nextRecord.startedAt,
    };

    console.log("[LatencySummary]", summary);
    if (typeof window !== "undefined") {
      const reports = (((window as BrowserWindow).__MULTIMODAL_LATENCY_REPORTS__) ||= []);
      reports.push(summary);
      if (reports.length > MAX_REPORTS) {
        reports.splice(0, reports.length - MAX_REPORTS);
      }
    }
    return summary;
  }

  function markStage(requestId: string, stage: LatencyStage, at: number) {
    latestRequestId = requestId;
    if (stage === "server_received") {
      mark(requestId, { serverReceivedAt: at });
    } else if (stage === "llm_start") {
      mark(requestId, { llmStartedAt: at });
    } else if (stage === "llm_first_delta") {
      markFirst(requestId, "firstTokenAt");
    } else if (stage === "llm_final") {
      markFirst(requestId, "finalTextAt");
    } else if (stage === "tts_start") {
      markFirst(requestId, "ttsStartedAt");
    } else if (stage === "tts_payload_ready") {
      markFirst(requestId, "ttsPayloadReadyAt");
    }
  }

  function clear() {
    for (const key of Object.keys(records)) {
      delete records[key];
    }
    latestRequestId = null;
  }

  function destroy() {
    clear();
    if (typeof window !== "undefined") {
      const reports = (window as BrowserWindow).__MULTIMODAL_LATENCY_REPORTS__;
      if (reports) {
        reports.length = 0;
      }
    }
  }

  return {
    clear,
    destroy,
    finalize,
    getLatestRequestId,
    getRecord,
    mark,
    markFirst,
    markStage,
    setLatestRequestId,
    startRecord,
  };
}
