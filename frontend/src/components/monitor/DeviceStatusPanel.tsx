/**
 * DeviceStatusPanel — exhaust fan + sensor switches + live readings.
 */
import React from "react";
import { Fan, Thermometer, Scale, AlertOctagon } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { MonitorCard } from "./MonitorCard";
import type { DeviceStatus } from "./types";

export interface DeviceStatusPanelProps {
  data: DeviceStatus | null;
}

interface Row {
  key: string;
  label: string;
  icon: React.ReactNode;
  on: boolean;
  danger?: boolean;
}

export const DeviceStatusPanel: React.FC<DeviceStatusPanelProps> = ({ data }) => {
  const { theme } = useTheme();

  const rows: Row[] = [
    {
      key: "fan",
      label: "排气扇",
      icon: <Fan size={14} />,
      on: !!data?.fan_on,
    },
    {
      key: "temp",
      label: "温度传感器",
      icon: <Thermometer size={14} />,
      on: !!data?.temp_sensor_active,
    },
    {
      key: "weight",
      label: "重量传感器",
      icon: <Scale size={14} />,
      on: !!data?.weight_sensor_active,
    },
    {
      key: "gas",
      label: "燃气报警",
      icon: <AlertOctagon size={14} />,
      on: !!data?.gas_alarm,
      danger: true,
    },
  ];

  return (
    <MonitorCard
      title="设备状态"
      icon={<Fan size={14} />}
      right={
        <span
          style={{
            fontSize: 11,
            color: data?.gas_alarm ? theme.error : theme.textMuted,
          }}
        >
          {data?.gas_alarm ? "ALARM" : "NOMINAL"}
        </span>
      }
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: spacing.sm,
        }}
      >
        {rows.map(r => {
          const activeColor = r.danger ? theme.error : theme.success;
          const color = r.on ? activeColor : theme.textMuted;
          return (
            <div
              key={r.key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: `${spacing.sm}px ${spacing.md}px`,
                borderRadius: 8,
                background: r.on ? color + "18" : theme.bgTertiary,
                border: `1px solid ${r.on ? color + "55" : theme.borderLight}`,
                transition: "all 200ms ease",
              }}
            >
              <span style={{ color, display: "inline-flex" }}>{r.icon}</span>
              <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
                <span style={{ fontSize: 12, color: theme.text, fontWeight: 500 }}>
                  {r.label}
                </span>
                <span style={{ fontSize: 10, color, letterSpacing: 0.5 }}>
                  {r.on ? (r.danger ? "触发" : "ON") : "OFF"}
                </span>
              </div>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: color,
                  boxShadow: r.on ? `0 0 8px ${color}` : "none",
                  flexShrink: 0,
                }}
              />
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: spacing.md,
          paddingTop: spacing.md,
          borderTop: `1px solid ${theme.borderLight}`,
          display: "flex",
          gap: spacing.lg,
        }}
      >
        <Reading
          label="温度"
          value={data?.temperature_c != null ? `${data.temperature_c.toFixed(1)}°C` : "—"}
          color={theme.error}
        />
        <Reading
          label="重量"
          value={data?.weight_g != null ? `${data.weight_g} g` : "—"}
          color={theme.secondary}
        />
      </div>
    </MonitorCard>
  );
};

const Reading: React.FC<{ label: string; value: string; color: string }> = ({
  label,
  value,
  color,
}) => {
  const { theme } = useTheme();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 10, color: theme.textMuted, letterSpacing: 1 }}>{label}</span>
      <span
        style={{
          fontSize: 18,
          fontWeight: 600,
          color,
          fontFamily: "'SF Mono', monospace",
        }}
      >
        {value}
      </span>
    </div>
  );
};
