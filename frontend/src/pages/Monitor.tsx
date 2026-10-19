/**
 * Monitor — observability dashboard for projection / demo.
 *
 * Renders five live tiles (VLM / ASR / step progress / device status /
 * end-to-end latency) driven by `/ws/telemetry`. If the socket is
 * unreachable the panel auto-falls back to the built-in mock generator
 * so it is always live during a demo.
 *
 * This page is self-contained and can be previewed standalone — see
 * the delivery notes for how to swap it into `main.tsx` temporarily.
 */
import { useEffect, useState } from "react";
import { Activity, Radio, Wifi, WifiOff } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { spacing } from "@/styles";
import { useTelemetryStream, type TelemetrySource } from "@/components/monitor/useTelemetryStream";
import { VlmPanel } from "@/components/monitor/VlmPanel";
import { AsrPanel } from "@/components/monitor/AsrPanel";
import { StepProgressPanel } from "@/components/monitor/StepProgressPanel";
import { DeviceStatusPanel } from "@/components/monitor/DeviceStatusPanel";
import { LatencyPanel } from "@/components/monitor/LatencyPanel";

const SOURCE_META: Record<TelemetrySource, { label: string; color: string; icon: typeof Wifi }> = {
  connecting: { label: "连接中", color: "#8f8f8c", icon: Radio },
  ws: { label: "LIVE", color: "#10b981", icon: Wifi },
  mock: { label: "MOCK", color: "#f59e0b", icon: WifiOff },
};

export default function Monitor() {
  const { theme, themeMode, toggleThemeMode } = useTheme();
  const stream = useTelemetryStream();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const src = SOURCE_META[stream.source];
  const SrcIcon = src.icon;

  return (
    <div
      style={{
        position: "relative",
        zIndex: 1,
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        padding: spacing.lg,
        gap: spacing.lg,
        background: theme.bg,
        overflow: "hidden",
      }}
    >
      {/* ambient glow */}
      <div
        aria-hidden
        style={{
          position: "fixed",
          top: "-15%",
          left: "-5%",
          width: 520,
          height: 520,
          background: `radial-gradient(circle, ${theme.primaryLight} 0%, transparent 70%)`,
          animation: "float 20s ease-in-out infinite",
          pointerEvents: "none",
          zIndex: 0,
        }}
      />
      <div
        aria-hidden
        style={{
          position: "fixed",
          bottom: "-15%",
          right: "-5%",
          width: 460,
          height: 460,
          background: `radial-gradient(circle, ${theme.secondaryLight} 0%, transparent 70%)`,
          animation: "float 25s ease-in-out infinite reverse",
          pointerEvents: "none",
          zIndex: 0,
        }}
      />

      {/* header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: spacing.lg,
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: `linear-gradient(135deg, ${theme.primary}, ${theme.secondary})`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: `0 0 18px ${theme.primaryGlow}`,
              flexShrink: 0,
            }}
          >
            <Activity size={18} color="#fff" />
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span
              style={{
                fontSize: 16,
                fontWeight: 700,
                color: theme.text,
                letterSpacing: 0.5,
              }}
            >
              ROKID · 可观测监控面板
            </span>
            <span style={{ fontSize: 11, color: theme.textMuted, letterSpacing: 1 }}>
              VLM · ASR · STEP · DEVICE · LATENCY
            </span>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              fontFamily: "'SF Mono', monospace",
              fontSize: 13,
              color: theme.textSecondary,
              letterSpacing: 1,
            }}
          >
            {formatClock(now)}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 999,
              background: src.color + "22",
              color: src.color,
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 1,
            }}
          >
            <SrcIcon size={12} />
            {src.label}
          </span>
          <button
            onClick={toggleThemeMode}
            style={{
              padding: "6px 12px",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
              background: theme.bgSecondary,
              color: theme.textSecondary,
              fontSize: 11,
              cursor: "pointer",
              letterSpacing: 0.5,
            }}
          >
            {themeMode === "dark" ? "DARK" : "LIGHT"}
          </button>
        </div>
      </header>

      {/* grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(12, 1fr)",
          gridAutoRows: "minmax(0, 1fr)",
          gap: spacing.lg,
          flex: 1,
          minHeight: 0,
        }}
      >
        <div style={{ gridColumn: "span 4", minWidth: 0 }}>
          <VlmPanel data={stream.vlm} />
        </div>
        <div style={{ gridColumn: "span 4", minWidth: 0 }}>
          <AsrPanel finalLines={stream.asrFinal} partial={stream.asrPartial} />
        </div>
        <div style={{ gridColumn: "span 4", minWidth: 0 }}>
          <LatencyPanel history={stream.latency} />
        </div>
        <div style={{ gridColumn: "span 6", minWidth: 0 }}>
          <StepProgressPanel data={stream.step} />
        </div>
        <div style={{ gridColumn: "span 6", minWidth: 0 }}>
          <DeviceStatusPanel data={stream.device} />
        </div>
      </div>

      {/* footer ticker */}
      <footer
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          color: theme.textMuted,
          letterSpacing: 1,
          flexShrink: 0,
          textTransform: "uppercase",
        }}
      >
        <span>telemetry stream · {stream.source}</span>
        <span>
          vlm {stream.vlm ? "✓" : "·"} asr {stream.asrFinal.length}·
          {stream.asrPartial.length} step {stream.step ? `${stream.step.current_step}/${stream.step.total_steps}` : "·"}{" "}
          lat {stream.latency.length}
        </span>
      </footer>
    </div>
  );
}

function formatClock(d: Date): string {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
