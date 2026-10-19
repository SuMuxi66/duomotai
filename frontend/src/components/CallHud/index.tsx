import { useEffect, useRef, useState } from "react";
import { PhoneOff } from "lucide-react";
import type { Theme } from "../../lib/theme";

interface CallHudProps {
  theme: Theme;
  onHangup: () => void;
}

function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60).toString().padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/**
 * 通话模式胶囊浮窗（唤端即接听，挂断/长静默才退出）
 * 单行胶囊：通话中状态点 + 通话计时 + 挂断按钮，不展示详细文字内容
 */
export function CallHud({ theme, onHangup }: CallHudProps) {
  const startRef = useRef<number>(Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    startRef.current = Date.now();
    setElapsed(0);
    const id = window.setInterval(() => setElapsed(Date.now() - startRef.current), 500);
    return () => window.clearInterval(id);
  }, []);

  const panelBg = theme.mode === "dark" ? "rgba(18,18,18,0.74)" : "rgba(255,255,255,0.82)";

  return (
    <div
      style={{
        position: "fixed",
        top: 16,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 50,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          pointerEvents: "auto",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 8px 8px 16px",
          borderRadius: 999,
          background: panelBg,
          border: `1px solid ${theme.border}`,
          boxShadow: "0 10px 44px rgba(0,0,0,0.20)",
          backdropFilter: "blur(22px) saturate(160%)",
          WebkitBackdropFilter: "blur(22px) saturate(160%)",
        }}
      >
        <span
          style={{
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: theme.success,
            boxShadow: `0 0 0 4px ${theme.mode === "dark" ? "rgba(135,169,135,0.28)" : "rgba(16,185,129,0.22)"}`,
            animation: "callpulse 1.6s ease-in-out infinite",
          }}
        />
        <span style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>通话中</span>
        <span style={{ fontSize: 13, color: theme.textMuted, fontVariantNumeric: "tabular-nums", marginRight: 4 }}>
          {formatDuration(elapsed)}
        </span>
        <button
          onClick={onHangup}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: "none",
            cursor: "pointer",
            borderRadius: 999,
            padding: "7px 14px",
            fontSize: 13,
            fontWeight: 600,
            color: "#fff",
            background: theme.error,
            boxShadow: `0 4px 14px ${theme.mode === "dark" ? "rgba(193,126,126,0.35)" : "rgba(239,68,68,0.35)"}`,
          }}
        >
          <PhoneOff size={14} /> 挂断
        </button>
      </div>

      <style>{`
        @keyframes callpulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.3); opacity: 0.65; } }
      `}</style>
    </div>
  );
}
