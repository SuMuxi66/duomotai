/**
 * Input - 输入框组件（PromptInput 风格）
 *
 * 特性：
 * - 自动伸缩高度（textarea，随内容增长，至 maxHeight 后滚动）
 * - Enter 发送，Shift+Enter 换行
 * - 支持禁用状态
 * - 使用设计令牌统一样式
 */
import React, { useRef, useLayoutEffect, useCallback } from "react";
import { useTheme } from "@/hooks/useTheme";
import { radius, spacing, transition } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface InputProps {
  value: string;
  onChange: (value: string) => void;
  onSend?: () => void;
  placeholder?: string;
  disabled?: boolean;
  /** 最大高度（px），超出后滚动，默认 120 */
  maxHeight?: number;
  /** 最小高度（px），默认 40 */
  minHeight?: number;
  /** 自动聚焦 */
  autoFocus?: boolean;
  theme?: Theme;
  /** 自定义 aria-label */
  ariaLabel?: string;
}

export const Input: React.FC<InputProps> = ({
  value,
  onChange,
  onSend,
  placeholder = "",
  disabled = false,
  maxHeight = 120,
  minHeight = 40,
  autoFocus = false,
  theme: themeProp,
  ariaLabel,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 自动伸缩高度
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight);
    el.style.height = `${newHeight}px`;
  }, [value, minHeight, maxHeight]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (disabled) return;
        if (onSend && value.trim()) {
          onSend();
        }
      }
    },
    [disabled, onSend, value]
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
    },
    [onChange]
  );

  const composedStyle: React.CSSProperties = {
    width: "100%",
    minHeight,
    maxHeight,
    padding: `${spacing.sm}px ${spacing.md}px`,
    background: theme.inputBg,
    color: theme.inputText,
    border: `1px solid ${theme.inputBorder}`,
    borderRadius: radius.md,
    fontSize: 14,
    lineHeight: 1.5,
    resize: "none",
    outline: "none",
    cursor: disabled ? "not-allowed" : "text",
    opacity: disabled ? 0.5 : 1,
    transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
    fontFamily: "inherit",
    boxSizing: "border-box",
    overflowY: "auto",
  };

  const handleFocus = (e: React.FocusEvent<HTMLTextAreaElement>) => {
    e.currentTarget.style.borderColor = theme.primary;
    e.currentTarget.style.boxShadow = `0 0 0 2px ${theme.primaryLight}`;
  };

  const handleBlur = (e: React.FocusEvent<HTMLTextAreaElement>) => {
    e.currentTarget.style.borderColor = theme.inputBorder;
    e.currentTarget.style.boxShadow = "none";
  };

  return (
    <textarea
      ref={textareaRef}
      value={value}
      onChange={handleChange}
      onKeyDown={handleKeyDown}
      onFocus={handleFocus}
      onBlur={handleBlur}
      placeholder={placeholder}
      disabled={disabled}
      autoFocus={autoFocus}
      rows={1}
      style={composedStyle}
      aria-label={ariaLabel}
    />
  );
};
