/**
 * IconButton - 圆形图标按钮
 *
 * 统一 48px 圆形按钮，支持 active 状态高亮。
 */
import React, { useRef } from "react";
import { useTheme } from "@/hooks/useTheme";
import { transition, getShadow } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface IconButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  icon: React.ReactNode;
  /** 按钮尺寸（直径 px），默认 48 */
  size?: number;
  /** 激活状态（高亮显示） */
  active?: boolean;
  /** 激活时的背景色，默认使用主题 primaryLight */
  activeBg?: string;
  /** 自定义背景色 */
  background?: string;
  /** 自定义图标颜色 */
  color?: string;
  disabled?: boolean;
  theme?: Theme;
  type?: "button" | "submit" | "reset";
  /** 标题（tooltip） */
  title?: string;
}

export const IconButton: React.FC<IconButtonProps> = ({
  icon,
  size = 48,
  active = false,
  activeBg,
  background,
  color,
  disabled = false,
  theme: themeProp,
  type = "button",
  title,
  style,
  onMouseEnter,
  onMouseLeave,
  ...rest
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;
  const baseShadowRef = useRef<string>("");

  const activeColor = activeBg ?? theme.primaryLight;
  const iconColor = color ?? (active ? theme.primary : theme.textMuted);
  const bgColor =
    background ?? (active ? activeColor : getShadow("base", theme.mode) ? "transparent" : "transparent");

  const composedStyle: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: bgColor,
    border: `1px solid ${active ? theme.primary : theme.borderLight}`,
    color: iconColor,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    boxShadow: active ? "none" : getShadow("base", theme.mode),
    transition: `transform ${transition.fast}, background ${transition.base}, box-shadow ${transition.base}, border-color ${transition.base}`,
    flexShrink: 0,
    userSelect: "none",
    ...style,
  };

  const handleMouseEnter = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled) return;
    e.currentTarget.style.transform = "scale(1.08)";
    if (!active) {
      e.currentTarget.style.boxShadow = getShadow("hover", theme.mode);
      e.currentTarget.style.background = activeColor;
    }
    onMouseEnter?.(e);
  };

  const handleMouseLeave = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled) return;
    e.currentTarget.style.transform = "scale(1)";
    if (!active) {
      e.currentTarget.style.boxShadow = baseShadowRef.current || getShadow("base", theme.mode);
      e.currentTarget.style.background = background ?? "transparent";
    }
    onMouseLeave?.(e);
  };

  // 缓存初始阴影
  if (!baseShadowRef.current) {
    baseShadowRef.current = getShadow("base", theme.mode);
  }

  return (
    <button
      type={type}
      style={composedStyle}
      disabled={disabled}
      title={title}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      {...rest}
    >
      {icon}
    </button>
  );
};
