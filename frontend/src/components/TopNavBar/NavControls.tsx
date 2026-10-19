/**
 * NavControls - 导航控制
 *
 * 设置按钮 + 下拉菜单（模型选择/提示词/主题切换/风扇开关）。
 * 保留 createPortal 下拉菜单逻辑。
 */
import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { CustomIcon } from "@/components/CustomIcon";
import type { CustomIconName } from "@/components/CustomIcon";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/Toggle";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface NavControlsProps {
  theme: Theme;
  themeMode: "light" | "dark";
  fanStatus: boolean | null;
  onOpenModelSelector: () => void;
  onOpenPromptSettings: () => void;
  onToggleThemeMode: () => void;
  onFanToggle: () => void;
  /** 视频画面旋转角度（0/90/180/270），设置菜单"旋转画面"按钮显示 */
  videoRotation?: number;
  /** 点一次顺时针旋转 90° */
  onRotateVideo?: () => void;
  /** 紧凑模式：设置按钮仅显示图标 */
  compact?: boolean;
}

interface MenuItem {
  label: string;
  icon: CustomIconName;
  color: string;
  onClick: () => void;
  suffix?: React.ReactNode;
}

export const NavControls: React.FC<NavControlsProps> = ({
  theme,
  themeMode,
  fanStatus,
  onOpenModelSelector,
  onOpenPromptSettings,
  onToggleThemeMode,
  onFanToggle,
  videoRotation,
  onRotateVideo,
  compact = false,
}) => {
  const settingsWrapperRef = useRef<HTMLDivElement>(null);
  const settingsMenuRef = useRef<HTMLDivElement>(null);
  const [showSettingsMenu, setShowSettingsMenu] = useState(false);
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0, width: 0 });

  const isDark = theme.mode === "dark";
  const controlBorder = isDark ? "rgba(255, 255, 255, 0.06)" : theme.borderLight;

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!showSettingsMenu) return;
      const target = event.target as Node;
      if (!settingsWrapperRef.current?.contains(target) && !settingsMenuRef.current?.contains(target)) {
        setShowSettingsMenu(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [showSettingsMenu]);

  useEffect(() => {
    if (showSettingsMenu && settingsWrapperRef.current) {
      const updatePosition = () => {
        const rect = settingsWrapperRef.current!.getBoundingClientRect();
        const pad = 12;
        const minW = 220;
        const availW = Math.max(window.innerWidth - pad * 2, minW);
        const menuW = Math.min(Math.max(rect.width, minW), availW);
        const maxLeft = window.innerWidth - menuW - pad;
        const left = Math.min(Math.max(rect.left, pad), Math.max(pad, maxLeft));
        setMenuPos({ top: rect.bottom + 8, left, width: menuW });
      };
      updatePosition();
      window.addEventListener("scroll", updatePosition);
      window.addEventListener("resize", updatePosition);
      return () => {
        window.removeEventListener("scroll", updatePosition);
        window.removeEventListener("resize", updatePosition);
      };
    }
  }, [showSettingsMenu]);

  const menuItems: MenuItem[] = [
    { label: "打开模型选择设置", icon: "brain", color: theme.primary, onClick: onOpenModelSelector },
    { label: "个性化提示词", icon: "pencil", color: theme.secondary, onClick: onOpenPromptSettings },
    {
      label: "旋转画面",
      icon: "refresh-cw",
      color: theme.primary,
      onClick: onRotateVideo ?? (() => {}),
      suffix: `${videoRotation ?? 0}°`,
    },
    { label: "个性化界面切换", icon: "sparkles", color: theme.accent, onClick: onToggleThemeMode, suffix: themeMode === "light" ? "深色" : "浅色" },
    {
      label: "风扇",
      icon: "wind",
      color: fanStatus ? theme.success ?? theme.primary : theme.textMuted,
      onClick: onFanToggle,
      suffix: (
        <div onClick={(e) => e.stopPropagation()}>
          <Toggle
            checked={!!fanStatus}
            onChange={onFanToggle}
            theme={theme}
            size={40}
          />
        </div>
      ),
    },
  ];

  return (
    <div ref={settingsWrapperRef} style={{ position: "relative", flexShrink: 0 }}>
      <Button
        variant="secondary"
        theme={theme}
        onClick={() => setShowSettingsMenu((prev) => !prev)}
        title="设置"
        icon={<CustomIcon name="gear" size={18} color={showSettingsMenu ? "white" : theme.textSecondary} />}
        style={{
          height: 44,
          background: showSettingsMenu ? theme.primary : undefined,
          border: showSettingsMenu ? `1px solid ${theme.primary}` : undefined,
          color: showSettingsMenu ? "white" : theme.textSecondary,
        }}
      >
        {!compact && <span style={{ fontSize: 13, fontWeight: 600 }}>设置</span>}
      </Button>

      {showSettingsMenu &&
        createPortal(
          <div
            ref={settingsMenuRef}
            style={{
              position: "fixed",
              top: menuPos.top,
              left: menuPos.left,
              width: menuPos.width,
              minWidth: "220px",
              maxWidth: "calc(100vw - 24px)",
              background: theme.bgSecondary,
              border: `1px solid ${controlBorder}`,
              borderRadius: radius.xl,
              boxShadow: isDark ? "0 20px 44px rgba(0, 0, 0, 0.42)" : "0 18px 40px rgba(15, 23, 42, 0.18)",
              padding: `${spacing.xs}px`,
              zIndex: 999999,
            }}
          >
            {menuItems.map((item, idx) => (
              <Button
                key={idx}
                variant="ghost"
                fullWidth
                theme={theme}
                onClick={() => {
                  setShowSettingsMenu(false);
                  item.onClick();
                }}
                icon={<CustomIcon name={item.icon} size={16} color={item.color} />}
                iconRight={
                  item.suffix ? (
                    <span style={{ fontSize: 12, color: theme.textMuted }}>{item.suffix}</span>
                  ) : undefined
                }
                style={{
                  justifyContent: "space-between",
                  padding: `${spacing.md}px ${spacing.lg}px`,
                  borderRadius: radius.lg,
                  color: theme.text,
                }}
              >
                <span>{item.label}</span>
              </Button>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
};
