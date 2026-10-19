import { ShieldAlert, Flame, AlertTriangle } from "lucide-react";
import { Box, Toggle } from "@/components/ui";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";

interface AlertSettingsProps {
  dangerAlert: boolean;
  oilSmokeAlert: boolean;
  onDangerAlertChange: (v: boolean) => void;
  onOilSmokeAlertChange: (v: boolean) => void;
  theme: Theme;
}

export function AlertSettings({
  dangerAlert,
  oilSmokeAlert,
  onDangerAlertChange,
  onOilSmokeAlertChange,
  theme,
}: AlertSettingsProps) {
  const isLight = theme.mode === "light";

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
          color: theme.error,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        <ShieldAlert size={16} color={theme.error} />
        <span>智能检测开关</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: `${spacing.md}px` }}>
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
          <AlertTriangle size={18} color={theme.error} />
          <Toggle
            checked={dangerAlert}
            onChange={onDangerAlertChange}
            theme={theme}
            label="启用危险报警（烟雾、锅盖跳动、泡沫溢出）"
          />
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
          <Flame size={18} color={theme.warning} />
          <Toggle
            checked={oilSmokeAlert}
            onChange={onOilSmokeAlertChange}
            theme={theme}
            label="启用油烟检测"
          />
        </div>
      </div>

      <div style={{ fontSize: "12px", color: theme.textMuted, lineHeight: 1.5 }}>
        开关即时生效（无需点确认）：关闭某项检测后，后端立即停止该项检测，不再为它调用视觉模型；
        两项全部关闭时前端停止采集画面、后端零视觉调用。
      </div>
    </Box>
  );
}
