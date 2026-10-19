/**
 * Toggle - 开关组件
 *
 * 受控开关，支持禁用状态。
 */
import React from "react";
import { useTheme } from "@/hooks/useTheme";
import { transition } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** 开关尺寸（宽度），默认 44 */
  size?: number;
  theme?: Theme;
  /** 可选标签 */
  label?: string;
}

export const Toggle: React.FC<ToggleProps> = ({
  checked,
  onChange,
  disabled = false,
  size = 44,
  theme: themeProp,
  label,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;

  const height = Math.round(size * 0.6);
  const thumbSize = height - 6;
  const translateX = checked ? size - height + 3 : 3;

  const trackStyle: React.CSSProperties = {
    width: size,
    height,
    borderRadius: height / 2,
    background: checked ? theme.primary : theme.borderLight,
    position: "relative",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: `background ${transition.base}`,
    flexShrink: 0,
  };

  const thumbStyle: React.CSSProperties = {
    position: "absolute",
    top: 3,
    left: 0,
    width: thumbSize,
    height: thumbSize,
    borderRadius: "50%",
    background: "#fff",
    transform: `translateX(${translateX}px)`,
    transition: `transform ${transition.base}`,
    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
  };

  const handleClick = () => {
    if (!disabled) onChange(!checked);
  };

  if (label) {
    return (
      <label
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          color: theme.text,
          fontSize: 13,
          userSelect: "none",
        }}
      >
        <div style={trackStyle} onClick={handleClick}>
          <div style={thumbStyle} />
        </div>
        {label}
      </label>
    );
  }

  return (
    <div style={trackStyle} onClick={handleClick} role="switch" aria-checked={checked}>
      <div style={thumbStyle} />
    </div>
  );
};
