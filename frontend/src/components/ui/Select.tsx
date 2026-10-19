/**
 * Select - 下拉选择组件
 *
 * 重构 CustomSelect，改用设计令牌（radius/shadow/surface/transition）。
 * API 兼容 CustomSelect，可直接替换。
 */
import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { CustomIcon, type CustomIconName } from "../CustomIcon";
import {
  radius,
  spacing,
  transition,
  zIndex,
  getShadow,
  getSurface,
} from "@/styles";
import type { Theme } from "@/lib/theme";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  iconName?: CustomIconName;
  theme?: Theme;
  /** 自定义宽度 */
  width?: number | string;
}

export const Select: React.FC<SelectProps> = ({
  value,
  onChange,
  options,
  placeholder = "选择",
  disabled = false,
  iconName,
  theme: themeProp,
  width,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dropdownPosition, setDropdownPosition] = useState({
    top: 0,
    left: 0,
    width: 0,
  });

  const selectedOption = options.find((opt) => opt.value === value);
  const displayLabel = selectedOption?.label || placeholder;

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      const dropdownHeight = Math.min(options.length * 40 + 8, 280);
      const spaceBelow = window.innerHeight - rect.bottom;
      const top =
        spaceBelow < dropdownHeight && rect.top > dropdownHeight
          ? rect.top - dropdownHeight - 4
          : rect.bottom + 4;
      setDropdownPosition({
        top,
        left: rect.left,
        width: rect.width,
      });
    }
  }, [isOpen, options.length]);

  const toggleOpen = () => {
    if (!disabled) setIsOpen((prev) => !prev);
  };

  const handleSelect = (e: React.MouseEvent, val: string) => {
    e.stopPropagation();
    onChange(val);
    setIsOpen(false);
  };

  const triggerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: `${spacing.sm}px`,
    padding: `${spacing.sm}px ${spacing.md}px`,
    height: 44,
    background: getSurface("secondary", theme.mode),
    border: `1px solid ${theme.borderLight}`,
    borderRadius: radius.md,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    boxShadow: getShadow("base", theme.mode),
    transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
    color: theme.text,
    fontSize: 13,
    width,
    boxSizing: "border-box",
    userSelect: "none",
  };

  const dropdownStyle: React.CSSProperties = {
    position: "fixed",
    top: dropdownPosition.top,
    left: dropdownPosition.left,
    width: dropdownPosition.width,
    maxHeight: 280,
    overflowY: "auto",
    background: getSurface("tertiary", theme.mode),
    border: `1px solid ${theme.borderLight}`,
    borderRadius: radius.md,
    boxShadow: getShadow("hover", theme.mode),
    zIndex: zIndex.dropdown,
    padding: `${spacing.xs}px`,
  };

  const optionBaseStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    padding: `${spacing.sm}px ${spacing.md}px`,
    cursor: "pointer",
    borderRadius: radius.sm,
    transition: `background ${transition.fast}`,
    fontSize: 13,
    color: theme.text,
    userSelect: "none",
  };

  return (
    <div ref={containerRef} style={{ position: "relative", display: "inline-block", width }}>
      <div
        style={triggerStyle}
        onClick={toggleOpen}
        onMouseEnter={(e) => {
          if (!disabled) e.currentTarget.style.borderColor = theme.primary;
        }}
        onMouseLeave={(e) => {
          if (!disabled) e.currentTarget.style.borderColor = theme.borderLight;
        }}
      >
        {iconName && <CustomIcon name={iconName} size={16} color={theme.textMuted} />}
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: selectedOption ? theme.text : theme.textMuted,
          }}
        >
          {displayLabel}
        </span>
        <ChevronDown
          size={14}
          color={theme.textMuted}
          style={{
            transform: isOpen ? "rotate(180deg)" : "none",
            transition: `transform ${transition.base}`,
            flexShrink: 0,
          }}
        />
      </div>

      {isOpen &&
        createPortal(
          <div style={dropdownStyle}>
            {options.map((opt) => (
              <div
                key={opt.value}
                style={{
                  ...optionBaseStyle,
                  background:
                    opt.value === value ? theme.primaryLight : "transparent",
                  fontWeight: opt.value === value ? 600 : 400,
                }}
                onMouseEnter={(e) => {
                  if (opt.value !== value) {
                    e.currentTarget.style.background = theme.borderLight;
                  }
                }}
                onMouseLeave={(e) => {
                  if (opt.value !== value) {
                    e.currentTarget.style.background = "transparent";
                  }
                }}
                onClick={(e) => handleSelect(e, opt.value)}
              >
                {opt.label}
              </div>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
};
