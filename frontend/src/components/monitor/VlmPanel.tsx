/**
 * VlmPanel — current VLM recognition: scene + danger level + confidence.
 */
import React from "react";
import { Eye, AlertTriangle } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { MonitorCard } from "./MonitorCard";
import type { VlmRecognition } from "./types";

const DANGER_META: Record<
  VlmRecognition["danger_level"],
  { label: string; color: string }
> = {
  low: { label: "安全", color: "#10b981" },
  medium: { label: "注意", color: "#f59e0b" },
  high: { label: "危险", color: "#ef4444" },
};

export interface VlmPanelProps {
  data: VlmRecognition | null;
}

export const VlmPanel: React.FC<VlmPanelProps> = ({ data }) => {
  const { theme } = useTheme();
  const danger = data?.danger_level ?? "low";
  const meta = DANGER_META[danger];
  const confidence = data ? Math.round(data.confidence * 100) : 0;

  return (
    <MonitorCard
      title="VLM 视觉识别"
      icon={<Eye size={14} />}
      accent={meta.color}
      right={
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "2px 10px",
            borderRadius: 999,
            background: meta.color + "22",
            color: meta.color,
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {danger === "high" && <AlertTriangle size={12} />}
          {meta.label}
        </span>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        <div
          style={{
            fontSize: 22,
            fontWeight: 700,
            color: theme.text,
            lineHeight: 1.25,
            minHeight: 28,
            transition: "color 200ms ease",
          }}
        >
          {data?.scene ?? "等待识别…"}
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, minHeight: 24 }}>
          {(data?.items ?? []).map((it, i) => (
            <span
              key={`${it}-${i}`}
              style={{
                padding: "2px 10px",
                borderRadius: 999,
                background: theme.primaryLight,
                color: theme.primary,
                fontSize: 12,
              }}
            >
              {it}
            </span>
          ))}
          {!data && (
            <span style={{ fontSize: 12, color: theme.textMuted }}>—</span>
          )}
        </div>

        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 11,
              color: theme.textMuted,
              marginBottom: 4,
              letterSpacing: 0.5,
            }}
          >
            <span>置信度</span>
            <span style={{ fontFamily: "'SF Mono', monospace" }}>{confidence}%</span>
          </div>
          <div
            style={{
              height: 6,
              borderRadius: 3,
              background: theme.borderLight,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${confidence}%`,
                background: `linear-gradient(90deg, ${theme.primary}, ${meta.color})`,
                transition: "width 400ms ease",
                boxShadow: `0 0 8px ${meta.color}88`,
              }}
            />
          </div>
        </div>
      </div>
    </MonitorCard>
  );
};
