/**
 * LatencyPanel — end-to-end latency: big number + per-stage breakdown
 * + a sparkline of recent e2e samples.
 */
import React, { useMemo } from "react";
import { Activity } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { MonitorCard } from "./MonitorCard";
import type { Latency } from "./types";

export interface LatencyPanelProps {
  history: Latency[];
}

const STAGES: Array<{ key: keyof Pick<Latency, "vlm_ms" | "stt_ms" | "llm_ms" | "tts_ms">; label: string }> = [
  { key: "vlm_ms", label: "VLM" },
  { key: "stt_ms", label: "STT" },
  { key: "llm_ms", label: "LLM" },
  { key: "tts_ms", label: "TTS" },
];

const W = 260;
const H = 56;

export const LatencyPanel: React.FC<LatencyPanelProps> = ({ history }) => {
  const { theme } = useTheme();
  const latest = history[history.length - 1];
  const e2e = latest?.e2e_ms ?? 0;

  const { points, areaPath, maxMs } = useMemo(() => {
    if (history.length < 2) return { points: "", areaPath: "", maxMs: 0 };
    const vals = history.map(h => h.e2e_ms);
    const max = Math.max(...vals, 1);
    const stepX = W / (Math.max(history.length - 1, 1));
    const coords = vals.map((v, i) => {
      const x = i * stepX;
      const y = H - (v / max) * (H - 4) - 2;
      return [x, y] as const;
    });
    const pts = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const area =
      `M 0 ${H} ` +
      coords.map(([x, y]) => `L ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ") +
      ` L ${W} ${H} Z`;
    return { points: pts, areaPath: area, maxMs: max };
  }, [history]);

  const e2eColor = e2e > 1000 ? theme.error : e2e > 600 ? theme.warning : theme.success;

  return (
    <MonitorCard
      title="端到端延迟"
      icon={<Activity size={14} />}
      accent={e2eColor}
      right={
        <span
          style={{
            fontSize: 11,
            color: theme.textMuted,
            fontFamily: "'SF Mono', monospace",
          }}
        >
          {history.length} samples
        </span>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span
            style={{
              fontSize: 30,
              fontWeight: 700,
              color: e2eColor,
              fontFamily: "'SF Mono', monospace",
              lineHeight: 1,
              transition: "color 300ms ease",
              textShadow: `0 0 16px ${e2eColor}55`,
            }}
          >
            {e2e}
          </span>
          <span style={{ fontSize: 12, color: theme.textMuted }}>ms</span>
          <span
            style={{
              marginLeft: "auto",
              fontSize: 10,
              color: theme.textMuted,
              fontFamily: "'SF Mono', monospace",
            }}
          >
            peak {maxMs}ms
          </span>
        </div>

        {/* sparkline */}
        <svg
          width="100%"
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          style={{ display: "block" }}
        >
          <defs>
            <linearGradient id="lat-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={e2eColor} stopOpacity={0.35} />
              <stop offset="100%" stopColor={e2eColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          {areaPath && <path d={areaPath} fill="url(#lat-area)" stroke="none" />}
          {points && (
            <polyline
              points={points}
              fill="none"
              stroke={e2eColor}
              strokeWidth={1.5}
              strokeLinejoin="round"
              strokeLinecap="round"
              style={{ filter: `drop-shadow(0 0 4px ${e2eColor}88)` }}
            />
          )}
        </svg>

        {/* per-stage breakdown */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 6,
          }}
        >
          {STAGES.map(s => {
            const v = latest?.[s.key] ?? 0;
            return (
              <div
                key={s.key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  padding: "6px 4px",
                  borderRadius: 6,
                  background: theme.bgTertiary,
                }}
              >
                <span style={{ fontSize: 10, color: theme.textMuted, letterSpacing: 0.5 }}>
                  {s.label}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: theme.text,
                    fontFamily: "'SF Mono', monospace",
                  }}
                >
                  {v}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </MonitorCard>
  );
};
