/**
 * VideoControls - 视频控制栏
 *
 * 标题、运行状态徽章、来源切换、音频切换、分辨率显示。
 */
import React from "react";
import { Badge } from "@/components/ui/Badge";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface VideoControlsProps {
  theme: Theme;
  useGlassesCamera: boolean;
  useGlassesAudio: boolean;
  glassesAudioConnected: boolean;
  glassesConnected: boolean;
  /** 是否有画面（未传时按无画面显示） */
  streamActive?: boolean;
  videoResolution: { width: number; height: number };
  onSourceChange?: () => void;
  onAudioSourceChange?: () => void;
}

const toggleBoxStyle = (theme: Theme): React.CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: `${spacing.sm}px`,
  padding: `${spacing.sm}px ${spacing.md}px`,
  background: theme.mode === "dark" ? theme.buttonSecondary : "rgba(248, 250, 252, 0.95)",
  border: `1px solid ${theme.borderLight}`,
  borderRadius: radius.lg,
  fontSize: 12,
  cursor: "pointer",
});

export const VideoControls: React.FC<VideoControlsProps> = ({
  theme,
  useGlassesCamera,
  useGlassesAudio,
  glassesAudioConnected,
  glassesConnected,
  streamActive = false,
  videoResolution,
}) => {
  return (
    <div
      style={{
        padding: "18px 24px",
        borderBottom: `1px solid ${theme.borderLight}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: theme.bgSecondary,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: `${spacing.md}px` }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: theme.text, letterSpacing: "-0.3px" }}>
          实时视频流
        </span>
        {streamActive ? (
          <Badge variant="info" dot theme={theme}>
            运行中
          </Badge>
        ) : (
          <Badge variant="default" dot theme={theme}>
            已关闭
          </Badge>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: `${spacing.md}px` }}>
        <div
          style={{
            ...toggleBoxStyle(theme),
            cursor: "default",
          }}
        >
          <span style={{ color: theme.textMuted }}>来源</span>
          <span style={{ color: theme.text, fontWeight: 600 }}>
            {useGlassesCamera ? "眼镜摄像头" : "USB摄像头"}
          </span>
          {glassesConnected && (
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", marginLeft: 4 }} />
          )}
        </div>

        <div
          style={{
            ...toggleBoxStyle(theme),
            cursor: "default",
          }}
        >
          <span style={{ color: theme.textMuted }}>音频</span>
          <span style={{ color: theme.text, fontWeight: 600 }}>
            {useGlassesAudio ? "眼镜麦克风" : "电脑麦克风"}
          </span>
          {useGlassesAudio && glassesAudioConnected && (
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", marginLeft: 4 }} />
          )}
          {useGlassesAudio && !glassesAudioConnected && (
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#ef4444", marginLeft: 4 }} title="眼镜未连接" />
          )}
        </div>

        <div
          style={{
            ...toggleBoxStyle(theme),
            cursor: "default",
          }}
        >
          <span style={{ color: theme.textMuted }}>分辨率</span>
          <span style={{ color: theme.text, fontWeight: 600 }}>
            {videoResolution.width}×{videoResolution.height}
          </span>
        </div>
      </div>
    </div>
  );
};
