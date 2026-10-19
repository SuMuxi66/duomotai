/**
 * CountdownList - 倒计时列表组件
 *
 * 包含标题栏（可折叠）和按类别分组的倒计时卡片列表。
 */
import { Timer } from "lucide-react";
import type { Theme } from "@/lib/theme";
import { radius, transition } from "@/styles";
import { CountdownCard } from "./CountdownCard";
import {
  type CountdownTimer,
  getCategoryIcon,
  getCategoryColor,
  getCategoryLabel,
} from "./CountdownForm";

interface CountdownListProps {
  timers: CountdownTimer[];
  onCancelTimer: (timerId: string) => void;
  theme: Theme;
}

export function CountdownList({
  timers,
  onCancelTimer,
  theme,
}: CountdownListProps) {
  // 按类别分组
  const groupedTimers = timers.reduce((acc, timer) => {
    const category = timer.category || "general";
    if (!acc[category]) acc[category] = [];
    acc[category].push(timer);
    return acc;
  }, {} as Record<string, CountdownTimer[]>);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "6px",
        padding: "10px",
        background: theme.bg,
        borderRadius: radius.lg,
        border: `1px solid ${theme.borderLight}`,
        boxShadow: `0 4px 20px rgba(0, 0, 0, 0.1)`,
        overflow: "auto",
      }}
    >
      {Object.entries(groupedTimers).map(([category, categoryTimers]) => (
        <div key={category} style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {/* 类别标题 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 0",
              borderBottom: `1px solid ${theme.borderLight}`,
            }}
          >
            {getCategoryIcon(category, 12)}
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: getCategoryColor(category, theme),
                textTransform: "capitalize",
              }}
            >
              {getCategoryLabel(category)}
            </span>
            <span style={{ fontSize: 11, color: theme.textMuted }}>
              ({categoryTimers.length})
            </span>
          </div>

          {/* 该类别下的倒计时 */}
          {categoryTimers.map((timer) => (
            <CountdownCard
              key={timer.id}
              timer={timer}
              onCancel={() => onCancelTimer(timer.id)}
              theme={theme}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

interface CountdownHeaderProps {
  count: number;
  theme: Theme;
  isExpanded: boolean;
  onToggle: () => void;
  onCancelAll: () => void;
}

export function CountdownHeader({ count, theme, isExpanded, onToggle, onCancelAll }: CountdownHeaderProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 14px",
        background: theme.bg,
        borderRadius: radius.lg,
        border: `1px solid ${theme.borderLight}`,
        boxShadow: `0 4px 20px rgba(0, 0, 0, 0.1)`,
        cursor: "pointer",
      }}
      onClick={onToggle}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <Timer size={16} color={theme.primary} />
        <span style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>
          倒计时 ({count})
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        {count > 0 && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onCancelAll();
            }}
            style={{
              fontSize: 11,
              color: theme.textMuted,
              background: "transparent",
              border: "none",
              cursor: "pointer",
              padding: "2px 6px",
              borderRadius: radius.sm,
              transition: `all ${transition.base}`,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = theme.error;
              e.currentTarget.style.background = theme.errorLight;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = theme.textMuted;
              e.currentTarget.style.background = "transparent";
            }}
          >
            全部取消
          </button>
        )}
        <span
          style={{
            fontSize: 12,
            color: theme.textMuted,
            transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
            transition: `transform ${transition.base}`,
          }}
        >
          ▼
        </span>
      </div>
    </div>
  );
}
