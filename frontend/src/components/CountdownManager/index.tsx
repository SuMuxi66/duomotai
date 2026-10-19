/**
 * 全局倒计时管理组件
 *
 * 特点：
 * 1. 显示所有场景的倒计时
 * 2. 前端使用 requestAnimationFrame 实现平滑倒计时更新
 * 3. 不依赖 tick 状态，避免不必要的重渲染
 * 4. 支持倒计时完成提醒
 * 5. 支持本地存储，刷新页面后倒计时仍然存在
 */

import { useEffect, useState } from "react";
import type { Theme } from "@/lib/theme";
import { zIndex } from "@/styles";
import { CountdownList, CountdownHeader } from "./CountdownList";

// 重导出供外部使用（保持向后兼容）
export { loadTimersFromStorage, saveTimersToStorage, type CountdownTimer } from "./CountdownForm";

interface CountdownManagerProps {
  timers: import("./CountdownForm").CountdownTimer[];
  onCancelTimer: (timerId: string) => void;
  onCancelAll: () => void;
  theme: Theme;
  position?: "top-right" | "bottom-right" | "bottom-left";
}

/**
 * 全局倒计时管理组件
 */
export function CountdownManager({
  timers,
  onCancelTimer,
  onCancelAll,
  theme,
  position = "bottom-right",
}: CountdownManagerProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const [completedTimers, setCompletedTimers] = useState<string[]>([]);

  // 监听倒计时完成
  useEffect(() => {
    const checkCompleted = () => {
      const now = Date.now() / 1000;
      const newlyCompleted = timers
        .filter((t) => t.end_time <= now && !completedTimers.includes(t.id))
        .map((t) => t.id);

      if (newlyCompleted.length > 0) {
        setCompletedTimers((prev) => [...prev, ...newlyCompleted]);

        // 播放提示音或显示通知
        newlyCompleted.forEach((timerId) => {
          const timer = timers.find((t) => t.id === timerId);
          if (timer) {
            // 浏览器通知
            if ("Notification" in window && Notification.permission === "granted") {
              new Notification("倒计时结束", {
                body: timer.description,
                icon: "/vite.svg",
              });
            }
            // 注意：TTS 语音播报由 CookingTimerBar 组件处理，避免冲突
          }
        });
      }
    };

    const interval = setInterval(checkCompleted, 1000);
    return () => clearInterval(interval);
  }, [timers, completedTimers]);

  // 请求通知权限
  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  if (timers.length === 0) {
    return null;
  }

  // 位置样式
  const positionStyles: Record<string, React.CSSProperties> = {
    "top-right": { top: "80px", right: "20px" },
    "bottom-right": { bottom: "20px", right: "20px" },
    "bottom-left": { bottom: "20px", left: "20px" },
  };

  return (
    <div
      style={{
        position: "fixed",
        ...positionStyles[position],
        zIndex: zIndex.modal - 1,
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        maxWidth: "320px",
        maxHeight: "60vh",
        pointerEvents: "auto",
      }}
    >
      <CountdownHeader
        count={timers.length}
        theme={theme}
        isExpanded={isExpanded}
        onToggle={() => setIsExpanded(!isExpanded)}
        onCancelAll={onCancelAll}
      />
      {isExpanded && (
        <CountdownList
          timers={timers}
          onCancelTimer={onCancelTimer}
          theme={theme}
        />
      )}
    </div>
  );
}

export default CountdownManager;
