/**
 * ServingsCalcCard - 智能食材计算进度卡片
 *
 * 后端 servings_calc_start/item/end 事件驱动：
 * 每个待计算项先显示转圈圈，后端每算完一项揭示一项，其余继续转圈。
 */
import React from "react";
import type { Theme } from "@/lib/theme";

export interface ServingsCalcProgress {
  requestId: string;
  /** 待计算项标签（原每份用量行） */
  labels: string[];
  /** 计算结果：null=计算中（转圈），string=已完成 */
  results: (string | null)[];
}

export const ServingsCalcCard: React.FC<{
  theme: Theme;
  progress: ServingsCalcProgress;
}> = ({ theme, progress }) => {
  const isDark = theme.mode === "dark";

  return (
    <div style={{ padding: "0 12px 10px" }}>
      <div
        style={{
          padding: "12px 14px",
          borderRadius: 14,
          background: isDark ? "rgba(23, 32, 51, 0.72)" : "rgba(248, 250, 252, 0.95)",
          border: `1px solid ${theme.borderLight}`,
          boxShadow: isDark ? "none" : "0 4px 16px rgba(15, 23, 42, 0.06)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            fontWeight: 700,
            color: theme.text,
            marginBottom: 10,
          }}
        >
          🧮 智能计算的食材用量：
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {progress.labels.map((label, i) => {
            const result = progress.results[i];
            if (result === null || result === undefined) {
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 12.5,
                    lineHeight: 1.5,
                    color: theme.textMuted,
                  }}
                >
                  <span
                    className="servings-calc-spinner"
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: "50%",
                      border: `2px solid ${theme.borderLight}`,
                      borderTopColor: theme.primary,
                      animation: "servings-calc-spin 0.8s linear infinite",
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {label}
                  </span>
                </div>
              );
            }
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  fontSize: 12.5,
                  lineHeight: 1.5,
                }}
              >
                <span style={{ color: theme.success, flexShrink: 0, fontWeight: 700 }}>✓</span>
                <span style={{ color: theme.textSecondary, whiteSpace: "pre-wrap" }}>{result}</span>
              </div>
            );
          })}
        </div>
      </div>

      <style>{`
        @keyframes servings-calc-spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};
