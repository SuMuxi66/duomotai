import { useState, useEffect } from "react";
import { Theme } from "../../lib/theme";
import { CookingStatusProps, CookingTimer } from "./types";
import { X, ChefHat, Timer } from "lucide-react";

/**
 * 格式化剩余时间
 */
function formatRemaining(endTime: number): string {
  const remaining = Math.max(0, Math.round(endTime - Date.now() / 1000));
  if (remaining <= 0) return "00:00";
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  if (mins >= 60) {
    const hours = Math.floor(mins / 60);
    const remainMins = mins % 60;
    return `${hours}:${String(remainMins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

/**
 * 单个倒计时卡片
 */
function TimerCard({ timer, theme }: { timer: CookingTimer; theme: Theme }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const remaining = Math.max(0, Math.round(timer.end_time - Date.now() / 1000));
  const progress = timer.total_seconds > 0 ? Math.min(1, remaining / timer.total_seconds) : 0;
  const isUrgent = remaining > 0 && remaining <= 30;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: "10px",
      padding: "10px 12px",
      background: isUrgent ? `${theme.error}10` : theme.bgTertiary,
      borderRadius: "10px",
      border: `1px solid ${isUrgent ? theme.error + "40" : theme.borderLight}`,
    }}>
      <Timer size={16} color={isUrgent ? theme.error : theme.primary} strokeWidth={1.5} />
      <span style={{
        fontSize: "12px",
        color: theme.text,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        flex: 1,
        minWidth: 0,
      }}>
        {timer.description}
      </span>
      <span style={{
        fontSize: "13px",
        fontWeight: 700,
        fontVariantNumeric: "tabular-nums",
        color: isUrgent ? theme.error : theme.primary,
        flexShrink: 0,
      }}>
        {formatRemaining(timer.end_time)}
      </span>
      {/* 进度条 */}
      <div style={{
        width: "32px",
        height: "4px",
        background: theme.borderLight,
        borderRadius: "2px",
        overflow: "hidden",
        flexShrink: 0,
      }}>
        <div style={{
          height: "100%",
          width: `${progress * 100}%`,
          background: isUrgent ? theme.error : theme.primary,
          borderRadius: "2px",
          transition: "width 1s linear",
        }} />
      </div>
    </div>
  );
}

/**
 * 做饭状态栏组件
 * 显示做饭模式和当前进度
 */
export function CookingStatus({ recipeName, currentStep, totalSteps, timers, onCancelTimer, onExit, theme }: CookingStatusProps) {
  // 计算进度百分比
  const progressPercent = totalSteps > 0 ? Math.round((currentStep / totalSteps) * 100) : 0;

  return (
    <div style={{
      background: theme.bgSecondary,
      borderRadius: "16px",
      border: `1px solid ${theme.borderLight}`,
      padding: "14px 16px",
      display: "flex",
      flexDirection: "column",
      gap: "10px",
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "16px",
      }}>
        {/* 做饭图标 */}
        <div style={{
          width: "40px",
          height: "40px",
          borderRadius: "10px",
          background: theme.primaryLight,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}>
          <ChefHat size={20} color={theme.primary} strokeWidth={1.5} />
        </div>

        {/* 进度信息 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "8px",
          }}>
            <span style={{
              fontSize: "14px",
              fontWeight: 600,
              color: theme.text,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}>
              {recipeName}
            </span>
            <span style={{
              fontSize: "12px",
              color: theme.textMuted,
              flexShrink: 0,
            }}>
              {currentStep} / {totalSteps} 步
            </span>
          </div>

          {/* 进度条 */}
          <div style={{
            height: "6px",
            background: theme.borderLight,
            borderRadius: "3px",
            overflow: "hidden",
          }}>
            <div style={{
              height: "100%",
              width: `${progressPercent}%`,
              background: theme.buttonPrimary,
              borderRadius: "3px",
              transition: "width 0.3s ease",
            }} />
          </div>
        </div>

        {/* 退出按钮 */}
        <button
          onClick={onExit}
          style={{
            width: "36px",
            height: "36px",
            borderRadius: "10px",
            background: theme.errorLight,
            border: "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            transition: "all 0.2s ease",
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = theme.error;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = theme.errorLight;
          }}
          title="退出做饭模式"
        >
          <X size={18} color={theme.mode === "dark" ? theme.error : "#dc2626"} strokeWidth={1.5} />
        </button>
      </div>

      {/* 倒计时列表 */}
      {timers.length > 0 && (
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: "6px",
          paddingTop: "6px",
          borderTop: `1px solid ${theme.borderLight}`,
        }}>
          {timers.map((timer) => (
            <TimerCard key={timer.id} timer={timer} theme={theme} />
          ))}
          {timers.length > 0 && (
            <button
              onClick={onCancelTimer}
              style={{
                fontSize: "11px",
                color: theme.textMuted,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: "2px 0",
                textAlign: "left",
              }}
            >
              取消全部倒计时
            </button>
          )}
        </div>
      )}
    </div>
  );
}
