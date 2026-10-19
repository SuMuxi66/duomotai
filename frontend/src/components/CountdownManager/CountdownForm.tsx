/**
 * CountdownForm - 倒计时数据类型、存储工具与格式化函数
 *
 * 提供 CountdownTimer 接口、localStorage 持久化、时间格式化等基础能力。
 */
import { ChefHat, Bell, Clock } from "lucide-react";
import type { Theme } from "@/lib/theme";

// 本地存储键名
export const STORAGE_KEY = "multimodal_countdown_timers";

// 倒计时数据接口
export interface CountdownTimer {
  id: string;
  description: string;
  total_seconds: number;
  end_time: number; // Unix timestamp (seconds)
  created_at: number;
  category: "cooking" | "general" | "reminder" | string;
  metadata?: Record<string, any>;
  remaining_seconds?: number;
}

/**
 * 从 localStorage 加载倒计时列表
 * 过滤掉已经过期的倒计时，并清理本地存储
 */
export function loadTimersFromStorage(): CountdownTimer[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const timers: CountdownTimer[] = JSON.parse(stored);
      const now = Date.now() / 1000;
      // 过滤掉已经过期超过 5 分钟的倒计时（给用户时间看到完成状态）
      const validTimers = timers.filter((t) => t.end_time > now - 300);

      // 如果有已过期被过滤掉的，更新本地存储
      if (validTimers.length < timers.length) {
        if (validTimers.length === 0) {
          localStorage.removeItem(STORAGE_KEY);
          console.log("[Countdown] 清理所有已过期倒计时");
        } else {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(validTimers));
          console.log(`[Countdown] 清理 ${timers.length - validTimers.length} 个已过期倒计时`);
        }
      }
      return validTimers;
    }
  } catch (e) {
    console.error("[Countdown] 从本地存储加载失败:", e);
  }
  return [];
}

/**
 * 保存倒计时列表到 localStorage
 */
export function saveTimersToStorage(timers: CountdownTimer[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(timers));
  } catch (e) {
    console.error("[Countdown] 保存到本地存储失败:", e);
  }
}

/**
 * 格式化剩余时间为 MM:SS 或 HH:MM:SS
 */
export function formatRemainingTime(endTime: number): string {
  const remaining = Math.max(0, Math.round(endTime - Date.now() / 1000));
  if (remaining <= 0) return "00:00";

  const hours = Math.floor(remaining / 3600);
  const mins = Math.floor((remaining % 3600) / 60);
  const secs = remaining % 60;

  if (hours > 0) {
    return `${hours}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

/**
 * 计算进度百分比
 */
export function calculateProgress(timer: CountdownTimer): number {
  const remaining = Math.max(0, timer.end_time - Date.now() / 1000);
  if (timer.total_seconds <= 0) return 0;
  return Math.min(1, remaining / timer.total_seconds);
}

/**
 * 获取类别图标
 */
export function getCategoryIcon(category: string, size: number = 14) {
  switch (category) {
    case "cooking":
      return <ChefHat size={size} />;
    case "reminder":
      return <Bell size={size} />;
    default:
      return <Clock size={size} />;
  }
}

/**
 * 获取类别颜色
 */
export function getCategoryColor(category: string, theme: Theme): string {
  switch (category) {
    case "cooking":
      return theme.primary;
    case "reminder":
      return theme.warning || "#f59e0b";
    default:
      return theme.textSecondary;
  }
}

/**
 * 获取类别显示名称
 */
export function getCategoryLabel(category: string): string {
  switch (category) {
    case "cooking":
      return "烹饪";
    case "reminder":
      return "提醒";
    default:
      return "其他";
  }
}
