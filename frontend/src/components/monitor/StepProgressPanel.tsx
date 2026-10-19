/**
 * StepProgressPanel — current cooking step + overall progress bar.
 */
import React from "react";
import { ListOrdered } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { MonitorCard } from "./MonitorCard";
import type { StepProgress } from "./types";

export interface StepProgressPanelProps {
  data: StepProgress | null;
}

export const StepProgressPanel: React.FC<StepProgressPanelProps> = ({ data }) => {
  const { theme } = useTheme();
  const total = data?.total_steps ?? 0;
  const current = data?.current_step ?? 0;
  const pct = data ? Math.round(data.progress * 100) : 0;

  return (
    <MonitorCard
      title="步骤进度"
      icon={<ListOrdered size={14} />}
      right={
        <span
          style={{
            fontSize: 11,
            color: theme.textMuted,
            fontFamily: "'SF Mono', monospace",
          }}
        >
          {current}/{total || "—"}
        </span>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
        <div
          style={{
            fontSize: 17,
            fontWeight: 600,
            color: theme.text,
            minHeight: 24,
          }}
        >
          {data?.step_title ?? "等待开始…"}
        </div>

        {/* step pips */}
        <div style={{ display: "flex", gap: 6 }}>
          {Array.from({ length: Math.max(total, 1) }).map((_, i) => {
            const idx = i + 1;
            const done = data && idx < current;
            const active = data && idx === current;
            const color = active
              ? theme.primary
              : done
                ? theme.success
                : theme.borderLight;
            return (
              <div
                key={i}
                style={{
                  flex: 1,
                  height: 6,
                  borderRadius: 3,
                  background: color,
                  boxShadow: active ? `0 0 10px ${theme.primary}` : "none",
                  transition: "background 300ms ease",
                }}
              />
            );
          })}
        </div>

        {/* percentage bar */}
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
            <span>总进度</span>
            <span style={{ fontFamily: "'SF Mono', monospace" }}>{pct}%</span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 4,
              background: theme.borderLight,
              overflow: "hidden",
              position: "relative",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: `linear-gradient(90deg, ${theme.success}, ${theme.primary})`,
                transition: "width 500ms ease",
                boxShadow: `0 0 10px ${theme.primary}88`,
              }}
            />
          </div>
        </div>
      </div>
    </MonitorCard>
  );
};
