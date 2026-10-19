import React, { useEffect, useRef, useState } from "react";
import {
  Brain,
  Thermometer,
  Maximize2,
  Percent,
  Layers,
  Activity,
  FileText,
  Sparkles,
} from "lucide-react";
import { Box, Select, Toggle } from "@/components/ui";
import { radius, spacing, transition } from "@/styles";
import type { Theme } from "@/lib/theme";

interface GenerationParamsProps {
  temperature: number;
  maxTokens: number;
  topP: number;
  sampleCount: number;
  frequencyPenalty: number;
  responseFormat: "md" | "json";
  enableThinking: boolean;
  onTemperatureChange: (v: number) => void;
  onMaxTokensChange: (v: number) => void;
  onTopPChange: (v: number) => void;
  onSampleCountChange: (v: number) => void;
  onFrequencyPenaltyChange: (v: number) => void;
  onResponseFormatChange: (v: "md" | "json") => void;
  onEnableThinkingChange: (v: boolean) => void;
  theme: Theme;
}

interface NumberFieldConfig {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step: string;
  min: string;
  max: string;
  parse: (s: string) => number;
  defaultValue: number;
  icon: React.ReactNode;
}

export function GenerationParams({
  temperature,
  maxTokens,
  topP,
  sampleCount,
  frequencyPenalty,
  responseFormat,
  enableThinking,
  onTemperatureChange,
  onMaxTokensChange,
  onTopPChange,
  onSampleCountChange,
  onFrequencyPenaltyChange,
  onResponseFormatChange,
  onEnableThinkingChange,
  theme,
}: GenerationParamsProps) {
  const isLight = theme.mode === "light";
  const parseInt10 = (s: string) => parseInt(s, 10);

  // 数字字段用本地字符串中间态：清空时可继续输入新数字，避免被 defaultValue 立即填回
  const [rawValues, setRawValues] = useState<Record<string, string>>(() => ({
    "温度 (0-2)": String(temperature ?? ""),
    "最大标记": String(maxTokens ?? ""),
    "Top P (0-1)": String(topP ?? ""),
    "取样数量 (n)": String(sampleCount ?? ""),
    "频率惩罚 (-2~2)": String(frequencyPenalty ?? ""),
  }));
  const editingRef = useRef<Set<string>>(new Set());

  // 外部值变化（如切换模型重置参数）时同步显示；输入中不覆盖
  useEffect(() => {
    fields.forEach((f) => {
      if (!editingRef.current.has(f.label)) {
        setRawValues((s) => ({ ...s, [f.label]: String(f.value ?? "") }));
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [temperature, maxTokens, topP, sampleCount, frequencyPenalty]);

  const fields: NumberFieldConfig[] = [
    {
      label: "温度 (0-2)",
      value: temperature,
      onChange: onTemperatureChange,
      step: "0.1",
      min: "0",
      max: "2",
      parse: parseFloat,
      defaultValue: 0.7,
      icon: <Thermometer size={14} color={theme.textMuted} />,
    },
    {
      label: "最大标记",
      value: maxTokens,
      onChange: onMaxTokensChange,
      step: "1",
      min: "1",
      max: "8192",
      parse: parseInt10,
      defaultValue: 2048,
      icon: <Maximize2 size={14} color={theme.textMuted} />,
    },
    {
      label: "Top P (0-1)",
      value: topP,
      onChange: onTopPChange,
      step: "0.01",
      min: "0",
      max: "1",
      parse: parseFloat,
      defaultValue: 0.95,
      icon: <Percent size={14} color={theme.textMuted} />,
    },
    {
      label: "取样数量 (n)",
      value: sampleCount,
      onChange: onSampleCountChange,
      step: "1",
      min: "1",
      max: "5",
      parse: parseInt10,
      defaultValue: 1,
      icon: <Layers size={14} color={theme.textMuted} />,
    },
    {
      label: "频率惩罚 (-2~2)",
      value: frequencyPenalty,
      onChange: onFrequencyPenaltyChange,
      step: "0.1",
      min: "-2",
      max: "2",
      parse: parseFloat,
      defaultValue: 0,
      icon: <Activity size={14} color={theme.textMuted} />,
    },
  ];

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "10px 12px 10px 34px",
    borderRadius: `${radius.md}px`,
    border: `1px solid ${theme.border}`,
    background: theme.bgTertiary,
    color: theme.text,
    fontSize: "13px",
    outline: "none",
    boxSizing: "border-box",
    transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
  };

  const fieldWrapStyle: React.CSSProperties = {
    position: "relative",
    display: "flex",
    flexDirection: "column",
    gap: `${spacing.xs}px`,
  };

  const iconWrapStyle: React.CSSProperties = {
    position: "absolute",
    left: 10,
    top: 30,
    pointerEvents: "none",
  };

  return (
    <Box
      surface="tertiary"
      radius="lg"
      padding="lg"
      border={theme.borderLight}
      theme={theme}
      style={{
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: `${spacing.lg}px`,
        boxShadow: isLight
          ? "0 1px 3px rgba(0,0,0,0.04), inset 0 0 0 1px rgba(255,255,255,0.8)"
          : "0 1px 3px rgba(0,0,0,0.2)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: `${spacing.sm}px`,
          fontSize: "13px",
          fontWeight: 600,
          color: theme.textSecondary,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        <Brain size={16} color={theme.secondary} />
        <span>生成参数</span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: `${spacing.md}px`,
        }}
      >
        {fields.map((field) => (
          <div key={field.label} style={fieldWrapStyle}>
            <label
              style={{
                fontSize: "12px",
                fontWeight: 500,
                color: theme.textSecondary,
              }}
            >
              {field.label}
            </label>
            <span style={iconWrapStyle}>{field.icon}</span>
            <input
              type="number"
              step={field.step}
              min={field.min}
              max={field.max}
              value={rawValues[field.label] ?? String(field.value ?? "")}
              onChange={(e) => {
                const raw = e.target.value;
                editingRef.current.add(field.label);
                setRawValues((s) => ({ ...s, [field.label]: raw }));
                const parsed = field.parse(raw);
                if (!Number.isNaN(parsed)) field.onChange(parsed);
              }}
              style={inputStyle}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = theme.primary;
                e.currentTarget.style.boxShadow = `0 0 0 3px ${theme.primaryGlow}`;
              }}
              onBlur={(e) => {
                editingRef.current.delete(field.label);
                const parsed = field.parse(e.currentTarget.value);
                if (Number.isNaN(parsed)) {
                  setRawValues((s) => ({ ...s, [field.label]: String(field.value ?? "") }));
                }
                e.currentTarget.style.borderColor = theme.border;
                e.currentTarget.style.boxShadow = "none";
              }}
            />
          </div>
        ))}
        <div style={fieldWrapStyle}>
          <label
            style={{
              fontSize: "12px",
              fontWeight: 500,
              color: theme.textSecondary,
            }}
          >
            回复格式
          </label>
          <span style={iconWrapStyle}>
            <FileText size={14} color={theme.textMuted} />
          </span>
          <div style={{ position: "relative" }}>
            <Select
              value={responseFormat}
              onChange={(v) => onResponseFormatChange(v as "md" | "json")}
              options={[
                { value: "md", label: "Markdown" },
                { value: "json", label: "JSON" },
              ]}
              theme={theme}
              width="100%"
            />
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: `${spacing.md}px`,
          padding: `${spacing.md}px`,
          background: isLight ? "rgba(248,250,252,0.6)" : theme.bgTertiary,
          borderRadius: `${radius.md}px`,
          border: `1px solid ${theme.borderLight}`,
        }}
      >
        <Sparkles size={16} color={theme.accent} />
        <Toggle
          checked={enableThinking}
          onChange={onEnableThinkingChange}
          theme={theme}
          label="启用深度思考 (enable_thinking)"
        />
      </div>

      <div style={{ fontSize: "12px", color: theme.textMuted, lineHeight: 1.5 }}>
        仅对支持这些参数的模型生效（百炼、ModelScope 等）。关闭深度思考可获得更快的响应速度。
      </div>
    </Box>
  );
}
