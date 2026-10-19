/**
 * AsrPanel — real-time ASR transcript (scrolling).
 *
 * Shows finalized lines as a scrolling log and the current partial in a
 * highlighted "live" row. Auto-scrolls to the bottom on new content.
 */
import React, { useEffect, useRef } from "react";
import { Mic } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { MonitorCard } from "./MonitorCard";
import type { AsrTranscript } from "./types";

export interface AsrPanelProps {
  finalLines: AsrTranscript[];
  partial: string;
}

export const AsrPanel: React.FC<AsrPanelProps> = ({ finalLines, partial }) => {
  const { theme } = useTheme();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [finalLines, partial]);

  return (
    <MonitorCard
      title="ASR 实时转写"
      icon={<Mic size={14} />}
      right={
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: theme.textMuted,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: theme.secondary,
              boxShadow: `0 0 8px ${theme.secondary}`,
              animation: "blink 1.4s ease-in-out infinite",
            }}
          />
          listening
        </span>
      }
    >
      <div
        ref={scrollRef}
        style={{
          height: 180,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 6,
          paddingRight: 4,
          fontFamily: "'SF Mono', 'SF Pro Display', monospace",
          fontSize: 13,
          lineHeight: 1.5,
        }}
      >
        {finalLines.length === 0 && !partial && (
          <div style={{ color: theme.textMuted, fontStyle: "italic" }}>等待语音输入…</div>
        )}
        {finalLines.map((line, i) => (
          <div key={`${line.timestamp}-${i}`} style={{ display: "flex", gap: 8 }}>
            <span
              style={{
                color: theme.textMuted,
                flexShrink: 0,
                fontSize: 11,
                paddingTop: 2,
              }}
            >
              {formatTs(line.timestamp)}
            </span>
            <span style={{ color: theme.textSecondary }}>{line.text}</span>
          </div>
        ))}
        {partial && (
          <div style={{ display: "flex", gap: 8 }}>
            <span
              style={{
                color: theme.secondary,
                flexShrink: 0,
                fontSize: 11,
                paddingTop: 2,
              }}
            >
              …
            </span>
            <span
              style={{
                color: theme.secondary,
                background: theme.secondaryLight,
                borderRadius: 4,
                padding: "0 4px",
              }}
            >
              {partial}
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 13,
                  marginLeft: 2,
                  background: theme.secondary,
                  verticalAlign: "-2px",
                  animation: "blink 1s step-start infinite",
                }}
              />
            </span>
          </div>
        )}
      </div>
      <div
        style={{
          marginTop: spacing.sm,
          paddingTop: spacing.sm,
          borderTop: `1px solid ${theme.borderLight}`,
          fontSize: 11,
          color: theme.textMuted,
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>已转写 {finalLines.length} 句</span>
        <span>{partial.length} 字 pending</span>
      </div>
    </MonitorCard>
  );
};

function formatTs(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
