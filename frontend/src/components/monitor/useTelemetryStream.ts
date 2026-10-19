/**
 * useTelemetryStream — connects to `/ws/telemetry` and exposes the
 * decoded telemetry state to the monitor panel.
 *
 * Strategy:
 *  1. Open a WebSocket to `/ws/telemetry` (proxied to the backend by
 *     Vite, same as the existing `/ws` route).
 *  2. If the socket doesn't reach OPEN within 2.5s — or connects but
 *     later drops — fall back to the built-in mock generator so the
 *     panel is always live during a demo.
 *  3. When the live socket re-opens, the mock generator is stopped.
 *
 * The hook is self-contained: panels just read the returned state.
 */
import { useEffect, useRef, useState } from "react";
import { getBackendUrl } from "@/lib/ws";
import { API_BASE } from "@/lib/constants";
import { startMockTelemetry } from "./mockTelemetry";
import type {
  AsrTranscript,
  DeviceStatus,
  Latency,
  StepProgress,
  TelemetryEvent,
  VlmRecognition,
} from "./types";

export type TelemetrySource = "connecting" | "ws" | "mock";

export interface TelemetryStreamState {
  source: TelemetrySource;
  vlm: VlmRecognition | null;
  asrFinal: AsrTranscript[];
  asrPartial: string;
  step: StepProgress | null;
  device: DeviceStatus | null;
  latency: Latency[];
}

const INITIAL: TelemetryStreamState = {
  source: "connecting",
  vlm: null,
  asrFinal: [],
  asrPartial: "",
  step: null,
  device: null,
  latency: [],
};

const MAX_ASR_LINES = 50;
const MAX_LATENCY_POINTS = 60;

export function useTelemetryStream(): TelemetryStreamState {
  const [state, setState] = useState<TelemetryStreamState>(INITIAL);

  // Refs avoid stale closures inside WS callbacks.
  const wsOpenedRef = useRef(false);
  const stopMockRef = useRef<(() => void) | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;
    wsOpenedRef.current = false;

    const applyEvent = (evt: TelemetryEvent) => {
      if (stoppedRef.current) return;
      switch (evt.type) {
        case "vlm_recognition":
          setState(s => ({ ...s, vlm: evt.payload as VlmRecognition }));
          break;
        case "asr_transcript": {
          const p = evt.payload as AsrTranscript;
          setState(s => {
            if (p.is_final) {
              return {
                ...s,
                asrFinal: [...s.asrFinal.slice(-(MAX_ASR_LINES - 1)), p],
                asrPartial: "",
              };
            }
            return { ...s, asrPartial: p.text };
          });
          break;
        }
        case "step_progress":
          setState(s => ({ ...s, step: evt.payload as StepProgress }));
          break;
        case "device_status":
          setState(s => ({ ...s, device: evt.payload as DeviceStatus }));
          break;
        case "latency":
          setState(s => ({
            ...s,
            latency: [...s.latency.slice(-(MAX_LATENCY_POINTS - 1)), evt.payload as Latency],
          }));
          break;
      }
    };

    const startMock = () => {
      if (stoppedRef.current) return;
      if (stopMockRef.current) return; // already running
      setState(s => (s.source === "mock" ? s : { ...s, source: "mock" }));
      stopMockRef.current = startMockTelemetry(applyEvent);
    };

    const stopMock = () => {
      stopMockRef.current?.();
      stopMockRef.current = null;
    };

    let ws: WebSocket | null = null;
    let fallbackTimer: ReturnType<typeof setTimeout> | null = null;

    try {
      const url = getBackendUrl(API_BASE) + "/telemetry";
      ws = new WebSocket(url);

      ws.onopen = () => {
        if (stoppedRef.current) return;
        wsOpenedRef.current = true;
        stopMock();
        setState(s => ({ ...s, source: "ws" }));
        if (fallbackTimer) {
          clearTimeout(fallbackTimer);
          fallbackTimer = null;
        }
      };

      ws.onmessage = m => {
        try {
          applyEvent(JSON.parse(m.data) as TelemetryEvent);
        } catch {
          /* ignore malformed frames */
        }
      };

      ws.onerror = () => {
        /* onclose will follow and trigger fallback */
      };

      ws.onclose = () => {
        if (stoppedRef.current) return;
        if (!wsOpenedRef.current) return; // never opened — fallback timer handles
        // Was live, now disconnected — switch to mock.
        startMock();
      };

      // Fallback: if WS doesn't open within 2.5s, give up and use mock.
      fallbackTimer = setTimeout(() => {
        if (stoppedRef.current) return;
        if (!wsOpenedRef.current) {
          startMock();
          // Give up on the pending socket so it doesn't open later and
          // fight the mock generator.
          try {
            ws?.close();
          } catch {
            /* noop */
          }
        }
      }, 2500);
    } catch {
      startMock();
    }

    return () => {
      stoppedRef.current = true;
      if (fallbackTimer) clearTimeout(fallbackTimer);
      stopMock();
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try {
          ws.close();
        } catch {
          /* noop */
        }
      }
    };
  }, []);

  return state;
}
