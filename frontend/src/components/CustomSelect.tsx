import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { Theme } from "../lib/theme";
import { CustomIcon } from "./CustomIcon";

interface CustomSelectOption {
  value: string;
  label: string;
}

interface CustomSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: CustomSelectOption[];
  placeholder?: string;
  theme: Theme;
  disabled?: boolean;
  iconName?: "mic" | "camera" | "speaker";
}

export function CustomSelect({
  value,
  onChange,
  options,
  placeholder = "选择",
  theme,
  disabled = false,
  iconName
}: CustomSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dropdownPosition, setDropdownPosition] = useState({ top: 0, left: 0, width: 0 });
  const isDark = theme.mode === "dark";
  const controlSurface = isDark ? theme.bgTertiary : 'rgba(248, 250, 252, 0.95)';
  const controlBorder = isDark ? 'rgba(255, 255, 255, 0.06)' : theme.borderLight;
  const controlShadow = isDark
    ? '0 4px 14px rgba(0, 0, 0, 0.28)'
    : '0 4px 12px rgba(15, 23, 42, 0.12), inset 0 2px 0 rgba(255, 255, 255, 0.8)';
  const controlHoverShadow = isDark
    ? '0 8px 20px rgba(0, 0, 0, 0.38)'
    : '0 6px 16px rgba(15, 23, 42, 0.16), inset 0 2px 0 rgba(255, 255, 255, 0.8)';
  const controlPressedShadow = isDark
    ? '0 2px 8px rgba(0, 0, 0, 0.32)'
    : '0 2px 6px rgba(15, 23, 42, 0.08), inset 0 4px 8px rgba(0, 0, 0, 0.15)';

  const selectedOption = options.find(opt => opt.value === value);
  const displayLabel = selectedOption?.label || placeholder;

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen && containerRef.current) {
      const updatePosition = () => {
        const rect = containerRef.current!.getBoundingClientRect();
        setDropdownPosition({
          top: rect.bottom + window.scrollY + 6,
          left: rect.left + window.scrollX,
          width: rect.width,
        });
      };
      
      updatePosition();
      
      window.addEventListener('scroll', updatePosition);
      window.addEventListener('resize', updatePosition);
      
      return () => {
        window.removeEventListener('scroll', updatePosition);
        window.removeEventListener('resize', updatePosition);
      };
    }
  }, [isOpen]);

  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!disabled) {
      setIsOpen(!isOpen);
    }
  };

  const selectOption = (opt: CustomSelectOption) => {
    console.log('选择选项:', opt.value, opt.label);
    onChange(opt.value);
    setIsOpen(false);
  };

  const dropdown = isOpen ? (
    <div 
      style={{
        position: "fixed",
        top: dropdownPosition.top,
        left: dropdownPosition.left,
        width: dropdownPosition.width,
        background: isDark ? theme.bgSecondary : "#ffffff",
        borderRadius: "12px",
        border: "1px solid " + controlBorder,
        boxShadow: isDark ? "0 16px 36px rgba(0, 0, 0, 0.45)" : "0 8px 24px rgba(15, 23, 42, 0.15)",
        zIndex: 999999,
        overflow: "visible",
        minWidth: "140px",
        maxWidth: "200px",
        animation: "dropdownIn 0.18s cubic-bezier(0.4, 0, 0.2, 1)",
      }}
      onMouseDown={(e) => {
        e.stopPropagation();
        e.preventDefault();
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes dropdownIn {
          from {
            opacity: 0;
            transform: translateY(-4px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}} />
      {options.map((opt) => (
        <div
          key={opt.value}
          onClick={(e) => {
            e.stopPropagation();
            selectOption(opt);
          }}
          style={{
            padding: "10px 14px",
            fontSize: "12px",
            color: value === opt.value ? theme.primary : theme.text,
            background: value === opt.value
              ? theme.primaryLight
              : "transparent",
            cursor: "pointer",
            transition: "all 0.15s ease",
            fontWeight: value === opt.value ? 600 : 400,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          onMouseEnter={(e) => {
            if (value !== opt.value) {
              e.currentTarget.style.background = isDark ? theme.bgTertiary : "rgba(106, 159, 181, 0.06)";
            }
          }}
          onMouseLeave={(e) => {
            if (value !== opt.value) {
              e.currentTarget.style.background = "transparent";
            }
          }}
        >
          {opt.label}
        </div>
      ))}
    </div>
  ) : null;

  return (
    <>
      <div
          ref={containerRef}
          onClick={handleToggle}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "0 14px",
            width: '130px',
            height: '44px',
            background: disabled ? (isDark ? 'rgba(30, 41, 59, 0.5)' : 'rgba(248, 250, 252, 0.5)') : controlSurface,
            borderRadius: "16px",
            border: "1px solid " + (disabled ? (isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)') : controlBorder),
            cursor: disabled ? "not-allowed" : "pointer",
            opacity: disabled ? 0.5 : 1,
            userSelect: "none",
            transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
            boxShadow: disabled ? 'none' : controlShadow,
            transform: 'translateY(0)',
            position: 'relative',
            overflow: 'hidden'
          }}
          onMouseEnter={(e) => {
            if (!disabled) {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = controlHoverShadow;
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = disabled ? 'none' : controlShadow;
          }}
          onMouseDown={(e) => {
            if (!disabled) {
              e.currentTarget.style.transform = 'translateY(2px)';
              e.currentTarget.style.boxShadow = controlPressedShadow;
            }
          }}
          onMouseUp={(e) => {
            if (!disabled) {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = controlHoverShadow;
            }
          }}
        >
          {iconName && <CustomIcon name={iconName} size={14} color={theme.textMuted} />}
          <span style={{
            fontSize: "11px",
            color: selectedOption ? theme.text : theme.textMuted,
            fontWeight: selectedOption ? 500 : 400,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: "100px",
          }}>
            {displayLabel}
          </span>
          <div style={{
            width: 0,
            height: 0,
            borderLeft: "4px solid transparent",
            borderRight: "4px solid transparent",
            borderTop: `5px solid ${theme.textMuted}`,
            transition: "transform 0.2s ease",
            transform: isOpen ? "rotate(180deg)" : "rotate(0deg)",
          }} />
        </div>
        {dropdown && createPortal(dropdown, document.body)}
    </>
  );
}
