/**
 * Badge - 状态标签组件
 *
 * 用于显示状态信息（成功/错误/警告/信息）。
 */
import React from "react";
import { useTheme } from "@/hooks/useTheme";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";

export type BadgeVariant = "success" | "error" | "warning" | "info" | "default";
export type BadgeSize = "sm" | "md";

export interface BadgeProps {
  variant?: BadgeVariant;
  size?: BadgeSize;
  children: React.ReactNode;
  theme?: Theme;
  /** 是否显示圆点 */
  dot?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = "default",
  size = "md",
  children,
  theme: themeProp,
  dot = false,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;

  const colors: Record<BadgeVariant, { bg: string; color: string; dotColor: string }> = {
    success: { bg: theme.success + "20", color: theme.success, dotColor: theme.success },
    error: { bg: theme.error + "20", color: theme.error, dotColor: theme.error },
    warning: { bg: theme.warning + "20", color: theme.warning, dotColor: theme.warning },
    info: { bg: theme.primaryLight, color: theme.primary, dotColor: theme.primary },
    default: { bg: theme.borderLight, color: theme.textSecondary, dotColor: theme.textMuted },
  };

  const c = colors[variant];
  const fontSize = size === "sm" ? 11 : 12;
  const padVertical = size === "sm" ? 2 : 4;
  const padHorizontal = size === "sm" ? spacing.sm : spacing.md;

  const composedStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: `${spacing.xs}px`,
    padding: `${padVertical}px ${padHorizontal}px`,
    background: c.bg,
    color: c.color,
    borderRadius: radius.sm,
    fontSize,
    fontWeight: 500,
    lineHeight: 1.4,
    whiteSpace: "nowrap",
    userSelect: "none",
  };

  return (
    <span style={composedStyle}>
      {dot && (
        <span
          style={{
            width: size === "sm" ? 6 : 8,
            height: size === "sm" ? 6 : 8,
            borderRadius: "50%",
            background: c.dotColor,
            flexShrink: 0,
          }}
        />
      )}
      {children}
    </span>
  );
};
