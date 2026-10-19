import { useEffect, useState, useRef } from "react";
import { Theme } from "../lib/theme";

export type ToastType = "info" | "success" | "warning" | "error";

export interface ToastMessage {
  id: number;
  text: string;
  type: ToastType;
}

interface ToastContainerProps {
  toasts: ToastMessage[];
  onDismiss: (id: number) => void;
  theme: Theme;
}

const typeStyles: Record<ToastType, { bg: string; border: string; icon: string }> = {
  info: { bg: "rgba(106, 159, 181, 0.12)", border: "rgba(106, 159, 181, 0.3)", icon: "ℹ️" },
  success: { bg: "rgba(34, 197, 94, 0.12)", border: "rgba(34, 197, 94, 0.3)", icon: "✅" },
  warning: { bg: "rgba(251, 146, 60, 0.12)", border: "rgba(251, 146, 60, 0.3)", icon: "⚠️" },
  error: { bg: "rgba(239, 68, 68, 0.12)", border: "rgba(239, 68, 68, 0.3)", icon: "❌" },
};

function ToastItem({ toast, onDismiss, theme }: { toast: ToastMessage; onDismiss: (id: number) => void; theme: Theme }) {
  const [exiting, setExiting] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    timerRef.current = window.setTimeout(() => {
      setExiting(true);
      setTimeout(() => onDismiss(toast.id), 300);
    }, 3500);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [toast.id, onDismiss]);

  const ts = typeStyles[toast.type] || typeStyles.info;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "12px 18px",
        background: theme.bgSecondary,
        border: `1px solid ${ts.border}`,
        borderRadius: "14px",
        boxShadow: theme.mode === "dark"
          ? "0 12px 32px rgba(0,0,0,0.4)"
          : "0 8px 24px rgba(15,23,42,0.12)",
        backdropFilter: "blur(16px)",
        opacity: exiting ? 0 : 1,
        transform: exiting ? "translateY(-12px) scale(0.95)" : "translateY(0) scale(1)",
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        pointerEvents: "auto",
        maxWidth: "480px",
        minWidth: "280px",
      }}
    >
      <span style={{ fontSize: "16px", flexShrink: 0 }}>{ts.icon}</span>
      <span style={{
        fontSize: "13px",
        color: theme.text,
        fontWeight: 500,
        lineHeight: 1.4,
      }}>
        {toast.text}
      </span>
    </div>
  );
}

export function ToastContainer({ toasts, onDismiss, theme }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div
      style={{
        position: "fixed",
        top: "20px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 999999,
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        pointerEvents: "none",
      }}
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} theme={theme} />
      ))}
    </div>
  );
}