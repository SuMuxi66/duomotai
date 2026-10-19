import React from "react";
import { Key, Eye, EyeOff, Trash2 } from "lucide-react";
import { radius, spacing, transition } from "@/styles";
import type { Theme } from "@/lib/theme";

interface ApiKeyInputProps {
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
  providerName: string;
  theme: Theme;
  showKey: boolean;
  onToggleShow: () => void;
}

export function ApiKeyInput({
  value,
  onChange,
  onClear,
  providerName,
  theme,
  showKey,
  onToggleShow,
}: ApiKeyInputProps) {
  const isLight = theme.mode === "light";

  const wrapperStyle: React.CSSProperties = {
    marginTop: `${spacing.lg}px`,
    padding: `${spacing.lg}px`,
    borderRadius: `${radius.md}px`,
    background: isLight ? "rgba(248,250,252,0.6)" : theme.bgTertiary,
    border: `1px solid ${theme.borderLight}`,
    display: "flex",
    flexDirection: "column",
    gap: `${spacing.md}px`,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "10px 12px 10px 38px",
    borderRadius: `${radius.md}px`,
    border: `1px solid ${theme.border}`,
    background: theme.bgTertiary,
    color: theme.text,
    fontSize: "13px",
    outline: "none",
    boxSizing: "border-box",
    transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
  };

  const iconBtnStyle = (color?: string): React.CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    gap: `${spacing.xs}px`,
    padding: `${spacing.sm}px ${spacing.md}px`,
    borderRadius: `${radius.sm}px`,
    border: "none",
    background: "transparent",
    color: color || theme.textSecondary,
    fontSize: "12px",
    fontWeight: 500,
    cursor: "pointer",
    transition: `background ${transition.fast}, color ${transition.fast}`,
  });

  return (
    <div style={wrapperStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: `${spacing.sm}px`,
            fontSize: "13px",
            fontWeight: 600,
            color: theme.textSecondary,
          }}
        >
          <Key size={14} color={theme.textMuted} />
          API Key
        </label>
        <div style={{ display: "flex", gap: `${spacing.xs}px` }}>
          <button
            type="button"
            onClick={onToggleShow}
            style={iconBtnStyle()}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = theme.borderLight;
              e.currentTarget.style.color = theme.text;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = theme.textSecondary;
            }}
          >
            {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
            <span>{showKey ? "隐藏" : "显示"}</span>
          </button>
          <button
            type="button"
            onClick={onClear}
            style={iconBtnStyle(theme.error)}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = theme.errorLight;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
            }}
          >
            <Trash2 size={14} />
            <span>清除</span>
          </button>
        </div>
      </div>
      <div style={{ position: "relative" }}>
        <Key
          size={14}
          color={theme.textMuted}
          style={{
            position: "absolute",
            left: 12,
            top: "50%",
            transform: "translateY(-50%)",
            pointerEvents: "none",
          }}
        />
        <input
          type={showKey ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={`输入 ${providerName} 的 API Key`}
          style={inputStyle}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = theme.primary;
            e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.primaryGlow}`;
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = theme.border;
            e.currentTarget.style.boxShadow = "none";
          }}
        />
      </div>
    </div>
  );
}
