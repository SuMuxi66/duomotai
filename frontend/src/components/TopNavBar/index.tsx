import React, { useRef, useState, useEffect, useCallback } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { CustomIcon } from "@/components/CustomIcon";
import { Logo } from "@/components/Logo";
import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/Button";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";
import type { DeviceInfo } from "@/types";
import { DeviceSelector } from "./DeviceSelector";
import { SettingsMenu } from "./SettingsMenu";
import { NavControls } from "./NavControls";

export interface TopNavBarProps {
  theme: Theme;
  themeMode: "light" | "dark";
  deviceInfo: DeviceInfo | null;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  cameraSource: string;
  glassesIp: string;
  visionEnabled: boolean;
  ttsEnabled: boolean;
  wakeWordEnabled: boolean;
  continuousMode: boolean;
  fanStatus: boolean | null;
  glassesCameraConnected: boolean;
  useGlassesAudio?: boolean;
  glassesAudioConnected?: boolean;
  onVisionEnabledChange: (enabled: boolean) => void;
  onTtsEnabledChange: (enabled: boolean) => void;
  onWakeWordEnabledChange: (enabled: boolean) => void;
  onContinuousModeChange: (mode: boolean) => void;
  onAudioDeviceChange: (deviceId: string) => void;
  onAudioOutputDeviceChange: (deviceId: string) => void;
  onVideoDeviceChange: (deviceId: string) => void;
  onCameraSourceChange: (source: string) => void;
  onGlassesIpChange: (ip: string) => void;
  onOpenModelSelector: () => void;
  onOpenPromptSettings: () => void;
  onToggleThemeMode: () => void;
  onFanToggle: () => void;
  /** 视频画面旋转角度（0/90/180/270），设置菜单"旋转画面"按钮驱动 */
  videoRotation?: number;
  onRotateVideo?: () => void;

  // 左侧聊天控制（来自原 UnifiedHeader）
  onOpenHistory?: () => void;
  onNewChat?: () => void;
  wakeWordDetected?: boolean;
  wakeWordLiveTranscript?: string;
  isAiSpeaking?: boolean;
}

export const TopNavBar: React.FC<TopNavBarProps> = ({
  theme,
  themeMode,
  deviceInfo,
  selectedAudioDevice,
  selectedAudioOutputDevice,
  selectedVideoDevice,
  visionEnabled,
  ttsEnabled,
  wakeWordEnabled,
  continuousMode,
  fanStatus,
  glassesCameraConnected,
  glassesAudioConnected,
  onVisionEnabledChange,
  onTtsEnabledChange,
  onWakeWordEnabledChange,
  onContinuousModeChange,
  onAudioDeviceChange,
  onAudioOutputDeviceChange,
  onVideoDeviceChange,
  onOpenModelSelector,
  onOpenPromptSettings,
  onToggleThemeMode,
  onFanToggle,
  videoRotation,
  onRotateVideo,
  onOpenHistory,
  onNewChat,
  wakeWordDetected,
  wakeWordLiveTranscript,
  isAiSpeaking,
}) => {
  const navRef = useRef<HTMLElement>(null);
  const spotlightRef = useRef<HTMLDivElement>(null);
  const spotlightVisibleRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const [showLeftButton, setShowLeftButton] = useState(false);
  const [showRightButton, setShowRightButton] = useState(false);
  const [windowWidth, setWindowWidth] = useState(() => (typeof window !== "undefined" ? window.innerWidth : 1200));

  const compact = windowWidth < 1280;
  const veryCompact = windowWidth < 820;

  const isDark = theme.mode === "dark";
  const controlBorder = isDark ? "rgba(255, 255, 255, 0.06)" : theme.borderLight;

  const hasWakeWordLiveTranscript = !!wakeWordLiveTranscript?.trim();
  const statusColor = isAiSpeaking
    ? "#f59e0b"
    : wakeWordDetected || hasWakeWordLiveTranscript
      ? "#22c55e"
      : wakeWordEnabled
        ? "#3b82f6"
        : "#ef4444";
  const statusAnimation =
    hasWakeWordLiveTranscript || wakeWordDetected || isAiSpeaking
      ? "blink 1.5s ease-in-out infinite"
      : "none";
  const statusTooltip = isAiSpeaking
    ? "AI 回复中"
    : wakeWordDetected
      ? "已检测到提示词"
      : hasWakeWordLiveTranscript
        ? "识别中"
        : wakeWordEnabled
          ? "麦克风监听中"
          : "唤醒词未开启";

  // 黑色（白色主题）/ 白色（深色主题）聚光灯颜色
  const spotlightColor = isDark
    ? "rgba(255, 255, 255, 0.20)"
    : "rgba(0, 0, 0, 0.35)";
  const spotlightCore = isDark
    ? "rgba(255, 255, 255, 0.42)"
    : "rgba(0, 0, 0, 0.68)";

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLElement>) => {
    const nav = navRef.current;
    const spot = spotlightRef.current;
    if (!nav || !spot) return;
    const rect = nav.getBoundingClientRect();
    // 对于可横向滚动的 nav，需要加上 scrollLeft/scrollTop，使聚光灯坐标与内容坐标对齐
    const x = e.clientX - rect.left + nav.scrollLeft;
    const y = e.clientY - rect.top + nav.scrollTop;

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      spot.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`;
      if (!spotlightVisibleRef.current) {
        spotlightVisibleRef.current = true;
        spot.style.opacity = "1";
      }
    });
  }, []);

  const handleMouseLeave = useCallback(() => {
    const spot = spotlightRef.current;
    if (!spot) return;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    spotlightVisibleRef.current = false;
    spot.style.opacity = "0";
  }, []);

  const checkScroll = () => {
    if (navRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = navRef.current;
      setShowLeftButton(scrollLeft > 10);
      setShowRightButton(scrollLeft < scrollWidth - clientWidth - 10);
    }
  };

  useEffect(() => {
    const handleResize = () => setWindowWidth(window.innerWidth);
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    checkScroll();
    const nav = navRef.current;
    if (nav) {
      nav.addEventListener("scroll", checkScroll);
      window.addEventListener("resize", checkScroll);
      const observer = new MutationObserver(checkScroll);
      observer.observe(nav, { childList: true, subtree: true });
      const timer = setTimeout(checkScroll, 300);
      return () => {
        nav.removeEventListener("scroll", checkScroll);
        window.removeEventListener("resize", checkScroll);
        observer.disconnect();
        clearTimeout(timer);
      };
    }
  }, [deviceInfo]);

  const scrollBy = (direction: "left" | "right") => {
    if (navRef.current) {
      const scrollAmount = navRef.current.clientWidth * 0.7;
      navRef.current.scrollBy({
        left: direction === "left" ? -scrollAmount : scrollAmount,
        behavior: "smooth",
      });
    }
  };

  const scrollBtnStyle: React.CSSProperties = {
    background: theme.bgSecondary,
    color: theme.primary,
    pointerEvents: "auto",
  };

  return (
    <div className="top-nav-bar-container" style={{ position: "relative", width: "100%", padding: "8px 12px" }}>
      {showLeftButton && (
        <div
          style={{
            position: "absolute", left: 12, top: 8, bottom: 8, width: "80px",
            background: `linear-gradient(90deg, ${isDark ? theme.bgSecondary : "white"} 0%, transparent 100%)`,
            zIndex: 10, pointerEvents: "none", display: "flex", alignItems: "center",
            borderRadius: "24px 0 0 24px",
          }}
        >
          <IconButton
            icon={<ChevronLeft size={18} />}
            onClick={() => scrollBy("left")}
            theme={theme}
            size={32}
            style={{ ...scrollBtnStyle, marginLeft: 8 }}
          />
        </div>
      )}

      {showRightButton && (
        <div
          style={{
            position: "absolute", right: 12, top: 8, bottom: 8, width: "80px",
            background: `linear-gradient(-90deg, ${isDark ? theme.bgSecondary : "white"} 0%, transparent 100%)`,
            zIndex: 10, pointerEvents: "none", display: "flex", alignItems: "center", justifyContent: "flex-end",
            borderRadius: "0 24px 24px 0",
          }}
        >
          <IconButton
            icon={<ChevronRight size={18} />}
            onClick={() => scrollBy("right")}
            theme={theme}
            size={32}
            style={{ ...scrollBtnStyle, marginRight: 8 }}
          />
        </div>
      )}

      <nav
        ref={navRef as any}
        className="top-nav-bar"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-start",
          gap: compact ? "8px" : "12px",
          width: "100%",
          boxSizing: "border-box",
          padding: compact ? "10px 14px" : "12px 24px",
          background: isDark ? theme.bgSecondary : "rgba(255, 255, 255, 0.95)",
          borderRadius: radius.xl,
          border: `1px solid ${controlBorder}`,
          backdropFilter: "blur(20px)",
          boxShadow: isDark ? "0 10px 28px rgba(0, 0, 0, 0.3)" : "0 4px 24px rgba(15, 23, 42, 0.08)",
          overflowX: "auto",
          overflowY: "hidden",
          flexWrap: veryCompact ? "wrap" : "nowrap",
          scrollbarWidth: "none",
          msOverflowStyle: "none",
          WebkitOverflowScrolling: "touch",
        }}
      >
        {/* 聚光灯背景 */}
        <div
          ref={spotlightRef}
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            width: 200,
            height: 200,
            borderRadius: "50%",
            background: `radial-gradient(circle, ${spotlightCore} 0%, ${spotlightColor} 45%, transparent 72%)`,
            filter: "blur(18px)",
            pointerEvents: "none",
            transform: "translate3d(0, 0, 0) translate(-50%, -50%)",
            willChange: "transform",
            zIndex: 0,
            opacity: 0,
            transition: "opacity 200ms ease",
          }}
        />

        {/* 左侧区域：标题 + 聊天控制 */}
        <div style={{ display: "flex", alignItems: "center", gap: compact ? "8px" : "12px", flexShrink: 0 }}>
          {/* 标题区域 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: compact ? "8px" : "12px",
              paddingRight: compact ? "12px" : "20px",
              borderRight: `1px solid ${controlBorder}`,
              flexShrink: 0,
            }}
          >
            <Logo theme={theme} size={veryCompact ? 32 : 40} />
            <div style={{ display: "flex", flexDirection: "column" }}>
              <div
                style={{
                  fontSize: veryCompact ? "15px" : compact ? "16px" : "18px",
                  fontWeight: 700,
                  letterSpacing: "-0.5px",
                  whiteSpace: "nowrap",
                  color: isDark ? theme.text : "transparent",
                  background: isDark ? "none" : `linear-gradient(135deg, ${theme.text}, ${theme.textSecondary})`,
                  WebkitBackgroundClip: isDark ? "unset" : "text",
                  WebkitTextFillColor: isDark ? "unset" : "transparent",
                  backgroundClip: isDark ? "unset" : "text",
                }}
              >
                多模态分析系统
              </div>
              {!compact && (
                <div
                  style={{
                    fontSize: "11px",
                    color: theme.textMuted,
                    letterSpacing: "1px",
                    textTransform: "uppercase",
                    whiteSpace: "nowrap",
                  }}
                >
                  MULTIMODAL ANALYSIS
                </div>
              )}
            </div>
          </div>

          {/* 左侧聊天控制 */}
          <div style={{ display: "flex", alignItems: "center", gap: `${spacing.md}px`, flexShrink: 0 }}>
            <Button
              variant="secondary"
              size="sm"
              theme={theme}
              icon={<CustomIcon name="history" size={16} color={onOpenHistory ? theme.primary : theme.textMuted} />}
              onClick={onOpenHistory}
              disabled={!onOpenHistory}
              aria-label="历史对话"
              title="历史对话"
            />
            <Button
              variant="secondary"
              size="sm"
              theme={theme}
              icon={<CustomIcon name="plus" size={16} color={onNewChat ? theme.primary : theme.textMuted} />}
              onClick={onNewChat}
              disabled={!onNewChat}
              aria-label="新建对话"
              title="新建对话"
            />
            <span title={statusTooltip} style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: statusColor,
                  boxShadow: `0 0 6px ${statusColor}80`,
                  animation: statusAnimation,
                  transition: "all 0.3s ease",
                }}
              />
            </span>
          </div>
        </div>

        {/* 右侧区域：设备 + 设置 + 导航控制，marginLeft auto 推到最右 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: compact ? "8px" : "12px",
            marginLeft: "auto",
            flexShrink: 0,
            flexWrap: veryCompact ? "wrap" : "nowrap",
            justifyContent: "flex-end",
          }}
        >
          <DeviceSelector
            theme={theme}
            deviceInfo={deviceInfo}
            selectedAudioDevice={selectedAudioDevice}
            selectedAudioOutputDevice={selectedAudioOutputDevice}
            selectedVideoDevice={selectedVideoDevice}
            glassesCameraConnected={glassesCameraConnected}
            glassesAudioConnected={glassesAudioConnected ?? false}
            onAudioDeviceChange={onAudioDeviceChange}
            onAudioOutputDeviceChange={onAudioOutputDeviceChange}
            onVideoDeviceChange={onVideoDeviceChange}
            compact={compact}
          />

          <SettingsMenu
            theme={theme}
            visionEnabled={visionEnabled}
            ttsEnabled={ttsEnabled}
            wakeWordEnabled={wakeWordEnabled}
            continuousMode={continuousMode}
            onVisionEnabledChange={onVisionEnabledChange}
            onTtsEnabledChange={onTtsEnabledChange}
            onWakeWordEnabledChange={onWakeWordEnabledChange}
            onContinuousModeChange={onContinuousModeChange}
            compact={compact}
          />

          <NavControls
            theme={theme}
            themeMode={themeMode}
            fanStatus={fanStatus}
            onOpenModelSelector={onOpenModelSelector}
            onOpenPromptSettings={onOpenPromptSettings}
            onToggleThemeMode={onToggleThemeMode}
            onFanToggle={onFanToggle}
            videoRotation={videoRotation}
            onRotateVideo={onRotateVideo}
            compact={compact}
          />
        </div>
      </nav>
    </div>
  );
};
