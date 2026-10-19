/**
 * Button - 按钮组件
 *
 * 统一 hover/active 动画，使用设计令牌。
 * variant: primary / secondary / ghost / danger
 */
import React, { useRef, useCallback } from "react";
import { useTheme } from "@/hooks/useTheme";
import { radius, spacing, transition, getShadow, getSurface } from "@/styles";
import type { Theme } from "@/lib/theme";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: React.ReactNode;
  iconRight?: React.ReactNode;
  loading?: boolean;
  fullWidth?: boolean;
  theme?: Theme;
  /** 按钮类型，默认 "button" */
  type?: "button" | "submit" | "reset";
  children?: React.ReactNode;
}

const sizeConfig: Record<
  ButtonSize,
  { height: number; padding: string; fontSize: number; iconSize: number }
> = {
  sm: { height: 32, padding: `${spacing.sm}px ${spacing.md}px`, fontSize: 13, iconSize: 14 },
  md: { height: 40, padding: `${spacing.md}px ${spacing.lg}px`, fontSize: 14, iconSize: 16 },
  lg: { height: 48, padding: `${spacing.lg}px ${spacing.xl}px`, fontSize: 16, iconSize: 18 },
};

export const Button: React.FC<ButtonProps> = ({
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  loading = false,
  fullWidth = false,
  disabled,
  theme: themeProp,
  type = "button",
  children,
  style,
  onMouseEnter,
  onMouseLeave,
  onMouseDown,
  onMouseUp,
  ...rest
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;
  const btnRef = useRef<HTMLButtonElement>(null);
  const cfg = sizeConfig[size];

  // 使用 ref 缓存原始阴影，避免反复设置
  const baseShadow = useRef<string>("");

  const getVariantStyle = useCallback((): React.CSSProperties => {
    switch (variant) {
      case "primary":
        return {
          background: theme.buttonPrimary,
          color: theme.buttonPrimaryText,
          border: "none",
          boxShadow: getShadow("base", theme.mode),
        };
      case "danger":
        return {
          background: theme.error,
          color: "#fff",
          border: "none",
          boxShadow: getShadow("base", theme.mode),
        };
      case "ghost":
        return {
          background: "transparent",
          color: theme.textSecondary,
          border: "1px solid transparent",
          boxShadow: "none",
        };
      case "secondary":
      default:
        return {
          background: getSurface("secondary", theme.mode),
          color: theme.text,
          border: `1px solid ${theme.borderLight}`,
          boxShadow: getShadow("base", theme.mode),
        };
    }
  }, [variant, theme]);

  const variantStyle = getVariantStyle();

  const composedStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: `${spacing.sm}px`,
    height: cfg.height,
    padding: cfg.padding,
    fontSize: cfg.fontSize,
    fontWeight: 500,
    borderRadius: radius.md,
    cursor: disabled || loading ? "not-allowed" : "pointer",
    opacity: disabled || loading ? 0.5 : 1,
    transition: `transform ${transition.fast}, box-shadow ${transition.base}, background ${transition.base}`,
    userSelect: "none",
    width: fullWidth ? "100%" : undefined,
    ...variantStyle,
    ...style,
  };

  // 缓存 baseShadow 用于 hover/active 恢复
  if (!baseShadow.current && typeof variantStyle.boxShadow === "string") {
    baseShadow.current = variantStyle.boxShadow as string;
  }

  const handleMouseEnter = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || loading) return;
    if (variant !== "ghost") {
      e.currentTarget.style.boxShadow = getShadow("hover", theme.mode);
    }
    e.currentTarget.style.transform = "translateY(-1px)";
    onMouseEnter?.(e);
  };

  const handleMouseLeave = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || loading) return;
    e.currentTarget.style.boxShadow = baseShadow.current;
    e.currentTarget.style.transform = "translateY(0)";
    onMouseLeave?.(e);
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || loading) return;
    if (variant !== "ghost") {
      e.currentTarget.style.boxShadow = getShadow("pressed", theme.mode);
    }
    e.currentTarget.style.transform = "translateY(0)";
    onMouseDown?.(e);
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (disabled || loading) return;
    e.currentTarget.style.transform = "translateY(-1px)";
    onMouseUp?.(e);
  };

  return (
    <button
      ref={btnRef}
      type={type}
      style={composedStyle}
      disabled={disabled || loading}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
      {...rest}
    >
      {loading && (
        <span
          style={{
            display: "inline-block",
            width: cfg.iconSize,
            height: cfg.iconSize,
            border: "2px solid currentColor",
            borderTopColor: "transparent",
            borderRadius: "50%",
            animation: "spin 0.6s linear infinite",
          }}
        />
      )}
      {!loading && icon}
      {children}
      {!loading && iconRight}
    </button>
  );
};
