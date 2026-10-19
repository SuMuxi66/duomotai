/**
 * 危险警报大弹窗：屏幕正中、全屏暗色遮罩、大字号警报信息
 * - 自动 8 秒后消失（底部进度条可视化倒计时）
 * - 点击遮罩/按钮可立即关闭
 * - 高能危险（锅盖跳动/泡沫溢出）由外层附加警笛音效
 */
import { useEffect, useRef } from "react";
import { Theme } from "../lib/theme";

export interface DangerAlertData {
  message: string;
  types: string[];
  highEnergy: boolean;
  ts: number;
}

interface DangerAlarmOverlayProps {
  alert: DangerAlertData;
  onDismiss: () => void;
  theme: Theme;
}

const AUTO_DISMISS_MS = 8000;

export function DangerAlarmOverlay({ alert, onDismiss, theme }: DangerAlarmOverlayProps) {
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    timerRef.current = window.setTimeout(() => onDismiss(), AUTO_DISMISS_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [alert.ts, onDismiss]);

  const isDark = theme.mode === "dark";
  const cardBg = isDark ? "#2a1518" : "#fff5f5";
  const border = "rgba(239, 68, 68, 0.65)";
  const accent = "#ef4444";

  return (
    <>
      <style>{`
        @keyframes dangerAlarmPop {
          0% { transform: scale(0.72); opacity: 0; }
          60% { transform: scale(1.04); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes dangerAlarmPing {
          0% { transform: scale(1); opacity: 0.55; }
          75%, 100% { transform: scale(1.5); opacity: 0; }
        }
        @keyframes dangerAlarmIcon {
          0%, 100% { transform: rotate(-6deg) scale(1); }
          50% { transform: rotate(6deg) scale(1.08); }
        }
        @keyframes dangerAlarmBar {
          from { width: 100%; }
          to { width: 0%; }
        }
        @keyframes dangerAlarmBackdrop {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
      <div
        onClick={onDismiss}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 3000,
          background: "rgba(2, 6, 16, 0.55)",
          backdropFilter: "blur(4px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          animation: "dangerAlarmBackdrop 0.25s ease-out",
          cursor: "pointer",
        }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "relative",
            width: "min(560px, 88vw)",
            background: cardBg,
            borderRadius: "24px",
            border: `2px solid ${border}`,
            boxShadow: `0 24px 80px rgba(239, 68, 68, 0.35), 0 8px 32px rgba(0,0,0,0.4)`,
            padding: "40px 40px 30px",
            textAlign: "center",
            cursor: "default",
            animation: "dangerAlarmPop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)",
            overflow: "hidden",
          }}
        >
          {/* 顶部脉冲光环 */}
          <div
            style={{
              position: "absolute",
              top: 26,
              left: "50%",
              marginLeft: -44,
              width: 88,
              height: 88,
              borderRadius: "50%",
              background: "rgba(239, 68, 68, 0.35)",
              animation: "dangerAlarmPing 1.6s cubic-bezier(0, 0, 0.2, 1) infinite",
              pointerEvents: "none",
            }}
          />
          {/* 警报图标 */}
          <div
            style={{
              position: "relative",
              width: 88,
              height: 88,
              margin: "0 auto 18px",
              borderRadius: "50%",
              background: `radial-gradient(circle at 35% 30%, #fca5a5, ${accent} 70%)`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 44,
              boxShadow: `0 10px 30px rgba(239, 68, 68, 0.5), inset 0 -4px 10px rgba(0,0,0,0.18)`,
              animation: "dangerAlarmIcon 1s ease-in-out infinite",
            }}
          >
            🚨
          </div>

          {/* 标题标签 */}
          <div
            style={{
              display: "inline-block",
              fontSize: 12,
              fontWeight: 800,
              letterSpacing: 4,
              color: accent,
              border: `1px solid ${border}`,
              borderRadius: 999,
              padding: "4px 16px",
              marginBottom: 14,
              background: isDark ? "rgba(239,68,68,0.08)" : "rgba(239,68,68,0.05)",
            }}
          >
            安全警报 · SAFETY ALERT
          </div>

          {/* 主信息 */}
          <div
            style={{
              fontSize: 26,
              fontWeight: 800,
              color: isDark ? "#fecaca" : "#7f1d1d",
              lineHeight: 1.4,
              marginBottom: 6,
            }}
          >
            {alert.message}
          </div>

          {/* 危险类型标签 */}
          {alert.types.length > 0 && (
            <div style={{ display: "flex", gap: 8, justifyContent: "center", margin: "12px 0 4px", flexWrap: "wrap" }}>
              {alert.types.map((t) => (
                <span
                  key={t}
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color: isDark ? "#fca5a5" : accent,
                    background: isDark ? "rgba(239,68,68,0.12)" : "rgba(239,68,68,0.08)",
                    border: `1px solid ${border}`,
                    borderRadius: 999,
                    padding: "4px 14px",
                  }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}

          {/* 关闭按钮 */}
          <button
            onClick={onDismiss}
            style={{
              marginTop: 22,
              padding: "12px 44px",
              fontSize: 16,
              fontWeight: 800,
              color: "#fff",
              background: `linear-gradient(180deg, #f87171, ${accent})`,
              border: "none",
              borderRadius: 14,
              cursor: "pointer",
              boxShadow: `0 8px 24px rgba(239, 68, 68, 0.4)`,
            }}
          >
            我知道了
          </button>

          {/* 自动关闭倒计时进度条 */}
          <div
            style={{
              position: "absolute",
              left: 0,
              bottom: 0,
              height: 5,
              width: "100%",
              background: "rgba(239,68,68,0.15)",
            }}
          >
            <div
              style={{
                height: "100%",
                background: accent,
                borderRadius: 999,
                animation: `dangerAlarmBar ${AUTO_DISMISS_MS}ms linear forwards`,
              }}
            />
          </div>
          <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 12 }}>
            点击任意处关闭 · {AUTO_DISMISS_MS / 1000} 秒后自动消失
          </div>
        </div>
      </div>
    </>
  );
}
