/**
 * CountdownCard - 单个倒计时卡片组件
 *
 * 使用 requestAnimationFrame 直接操作 DOM 更新时间显示，
 * 避免每帧触发 React 重渲染。
 */
import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import type { Theme } from "@/lib/theme";
import { transition } from "@/styles";
import {
  type CountdownTimer,
  formatRemainingTime,
  calculateProgress,
  getCategoryIcon,
  getCategoryColor,
} from "./CountdownForm";

interface CountdownCardProps {
  timer: CountdownTimer;
  onCancel: () => void;
  theme: Theme;
}

export function CountdownCard({ timer, onCancel, theme }: CountdownCardProps) {
  const timeRef = useRef<HTMLSpanElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const rafIdRef = useRef<number>();
  const completedRef = useRef<boolean>(false);

  useEffect(() => {
    const updateUI = () => {
      const remaining = Math.max(0, timer.end_time - Date.now() / 1000);
      const progress = timer.total_seconds > 0 ? remaining / timer.total_seconds : 0;
      const isUrgent = remaining > 0 && remaining <= 30;
      const isCompleted = remaining <= 0;

      // 更新时间显示
      if (timeRef.current) {
        const hours = Math.floor(remaining / 3600);
        const mins = Math.floor((remaining % 3600) / 60);
        const secs = Math.floor(remaining % 60);
        const timeStr = hours > 0
          ? `${hours}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
          : `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        timeRef.current.textContent = timeStr;
        timeRef.current.style.color = isUrgent ? theme.error : theme.primary;
      }

      // 更新进度条
      if (progressRef.current) {
        progressRef.current.style.width = `${progress * 100}%`;
        progressRef.current.style.background = isUrgent ? theme.error : theme.primary;
      }

      // 更新卡片样式（紧急状态）
      if (cardRef.current) {
        if (isUrgent) {
          cardRef.current.style.background = `${theme.error}10`;
          cardRef.current.style.borderColor = `${theme.error}40`;
        } else if (isCompleted) {
          cardRef.current.style.opacity = "0.6";
        }
      }

      // 倒计时完成，自动取消
      if (isCompleted && !completedRef.current) {
        completedRef.current = true;
        setTimeout(() => onCancel(), 3000);
        return;
      }

      if (!isCompleted) {
        rafIdRef.current = requestAnimationFrame(updateUI);
      }
    };

    rafIdRef.current = requestAnimationFrame(updateUI);
    return () => {
      if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
    };
  }, [timer.end_time, timer.total_seconds, theme, onCancel]);

  const remaining = Math.max(0, timer.end_time - Date.now() / 1000);
  const isUrgent = remaining > 0 && remaining <= 30;
  const categoryColor = getCategoryColor(timer.category, theme);

  return (
    <div
      ref={cardRef}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "10px 12px",
        background: isUrgent ? `${theme.error}10` : theme.bgTertiary,
        borderRadius: 10,
        border: `1px solid ${isUrgent ? `${theme.error}40` : theme.borderLight}`,
        transition: `background ${transition.slow}, border-color ${transition.slow}`,
        minWidth: "240px",
      }}
    >
      {/* 类别图标 */}
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          background: `${categoryColor}20`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: categoryColor,
          flexShrink: 0,
        }}
      >
        {getCategoryIcon(timer.category, 14)}
      </div>

      {/* 描述 */}
      <span
        style={{
          fontSize: 13,
          color: theme.text,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
          minWidth: 0,
        }}
        title={timer.description}
      >
        {timer.description}
      </span>

      {/* 倒计时显示 */}
      <span
        ref={timeRef}
        style={{
          fontSize: 14,
          fontWeight: 700,
          fontVariantNumeric: "tabular-nums",
          color: isUrgent ? theme.error : theme.primary,
          flexShrink: 0,
          minWidth: "50px",
          textAlign: "right",
        }}
      >
        {formatRemainingTime(timer.end_time)}
      </span>

      {/* 进度条 */}
      <div
        style={{
          width: "40px",
          height: "4px",
          background: theme.borderLight,
          borderRadius: "2px",
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <div
          ref={progressRef}
          style={{
            height: "100%",
            width: `${calculateProgress(timer) * 100}%`,
            background: isUrgent ? theme.error : theme.primary,
            borderRadius: "2px",
            transition: "width 0.1s linear",
          }}
        />
      </div>

      {/* 取消按钮 */}
      <button
        onClick={onCancel}
        style={{
          width: 22,
          height: 22,
          borderRadius: 5,
          background: "transparent",
          border: "none",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          color: theme.textMuted,
          flexShrink: 0,
          transition: `all ${transition.base}`,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = theme.errorLight;
          e.currentTarget.style.color = theme.error;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = theme.textMuted;
        }}
        title="取消倒计时"
      >
        <X size={14} strokeWidth={2} />
      </button>
    </div>
  );
}
