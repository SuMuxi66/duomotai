/**
 * CookingTimerBadge - 倒计时徽章组件
 *
 * 显示烹饪倒计时，支持 TTS 播报和浏览器通知。
 */
import { useEffect, useState } from "react";
import { Timer } from "lucide-react";
import type { Theme } from "@/lib/theme";

export interface CookingTimerInfo {
  id: string;
  description: string;
  total_seconds: number;
  end_time: number;
}

// 已完成的倒计时 ID 集合（用于防止重复触发）
const completedTimerIds = new Set<string>();

export interface CookingTimerBadgeProps {
  timers: CookingTimerInfo[];
  onCancel?: () => void;
  theme: Theme;
  ttsEnabled?: boolean;
}

export function CookingTimerBadge({ timers, onCancel, theme, ttsEnabled }: CookingTimerBadgeProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  // 监听倒计时完成并触发 TTS
  useEffect(() => {
    const now = Date.now() / 1000;
    timers.forEach(timer => {
      if (timer.end_time <= now && !completedTimerIds.has(timer.id)) {
        completedTimerIds.add(timer.id);

        // 触发 TTS 语音播报
        if (ttsEnabled && 'speechSynthesis' in window) {
          const speakText = `${timer.description}，倒计时结束`;
          console.log('[CookingTimerBar] TTS 播报:', speakText);

          const utterance = new SpeechSynthesisUtterance(speakText);
          utterance.lang = 'zh-CN';
          utterance.rate = 1.1;
          utterance.volume = 1;

          speechSynthesis.speak(utterance);
        }

        // 浏览器通知
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification('倒计时结束', {
            body: timer.description,
            icon: '/vite.svg',
          });
        }
      }
    });

    // 清理已不存在的倒计时 ID
    const currentIds = new Set(timers.map(t => t.id));
    completedTimerIds.forEach(id => {
      if (!currentIds.has(id)) {
        completedTimerIds.delete(id);
      }
    });
  }, [timers, ttsEnabled]);

  return (
    <div style={{
      padding: '10px 14px',
      marginBottom: '8px',
      background: `linear-gradient(135deg, rgba(251, 146, 60, 0.1), rgba(251, 146, 60, 0.05))`,
      border: `1px solid rgba(251, 146, 60, 0.2)`,
      borderRadius: '16px',
      display: 'flex',
      flexDirection: 'column',
      gap: '6px',
      animation: 'fade-in 0.3s ease-out'
    }}>
      {timers.map((timer) => {
        const remaining = Math.max(0, Math.round(timer.end_time - Date.now() / 1000));
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;
        const timeStr = mins >= 60
          ? `${Math.floor(mins / 60)}:${String(mins % 60).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
          : `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        const isUrgent = remaining > 0 && remaining <= 30;
        const progress = timer.total_seconds > 0 ? Math.min(1, remaining / timer.total_seconds) : 0;

        return (
          <div key={timer.id} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Timer size={14} color={isUrgent ? theme.error : '#fb923c'} strokeWidth={1.5} />
            <span style={{ fontSize: '12px', color: theme.text, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {timer.description}
            </span>
            <span style={{ fontSize: '14px', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: isUrgent ? theme.error : '#fb923c' }}>
              {timeStr}
            </span>
            <div style={{ width: '28px', height: '3px', background: theme.borderLight, borderRadius: '2px', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${progress * 100}%`, background: isUrgent ? theme.error : '#fb923c', borderRadius: '2px', transition: 'width 1s linear' }} />
            </div>
          </div>
        );
      })}
      {onCancel && (
        <button onClick={onCancel} style={{
          fontSize: '11px', color: theme.textMuted, background: 'transparent', border: 'none',
          cursor: 'pointer', padding: '2px 0', textAlign: 'right',
        }}>
          取消全部倒计时
        </button>
      )}
    </div>
  );
}
