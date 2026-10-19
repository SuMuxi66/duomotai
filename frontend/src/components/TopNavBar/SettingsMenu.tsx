/**
 * SettingsMenu - 操作开关组
 *
 * 视觉/TTS/唤醒词/连续模式开关。
 */
import React from "react";
import { CustomIcon } from "@/components/CustomIcon";
import type { CustomIconName } from "@/components/CustomIcon";
import { Button } from "@/components/ui/Button";
import type { Theme } from "@/lib/theme";

export interface SettingsMenuProps {
  theme: Theme;
  visionEnabled: boolean;
  ttsEnabled: boolean;
  wakeWordEnabled: boolean;
  continuousMode: boolean;
  onVisionEnabledChange: (enabled: boolean) => void;
  onTtsEnabledChange: (enabled: boolean) => void;
  onWakeWordEnabledChange: (enabled: boolean) => void;
  onContinuousModeChange: (mode: boolean) => void;
  /** 紧凑模式：仅显示图标，节省宽度 */
  compact?: boolean;
}

interface ToggleBtn {
  label: string;
  active: boolean;
  icon: CustomIconName;
  onClick: () => void;
}

export const SettingsMenu: React.FC<SettingsMenuProps> = ({
  theme,
  visionEnabled,
  ttsEnabled,
  wakeWordEnabled,
  continuousMode,
  onVisionEnabledChange,
  onTtsEnabledChange,
  onWakeWordEnabledChange,
  onContinuousModeChange,
  compact = false,
}) => {
  const isDark = theme.mode === "dark";
  const activeBg = isDark ? "#000000" : "rgba(106, 159, 181, 0.12)";
  const activeBorder = isDark ? `1px solid ${theme.borderLight}` : `1px solid ${theme.primary}`;
  const activeColor = isDark ? theme.text : theme.primary;
  const inactiveColor = isDark ? theme.textMuted : theme.textSecondary;

  const buttons: ToggleBtn[] = [
    { label: "视觉分析", active: visionEnabled, icon: "eye", onClick: () => onVisionEnabledChange(!visionEnabled) },
    { label: "语音播报", active: ttsEnabled, icon: "speaker", onClick: () => onTtsEnabledChange(!ttsEnabled) },
    { label: "持续识别", active: continuousMode, icon: "zap", onClick: () => onContinuousModeChange(!continuousMode) },
    { label: "唤醒词", active: wakeWordEnabled, icon: "mic", onClick: () => onWakeWordEnabledChange(!wakeWordEnabled) },
  ];

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
      {buttons.map((btn, idx) => (
        <Button
          key={idx}
          variant="secondary"
          theme={theme}
          onClick={btn.onClick}
          title={btn.label}
          icon={<CustomIcon name={btn.icon} size={18} color={btn.active ? activeColor : inactiveColor} />}
          style={{
            height: 44,
            background: btn.active ? activeBg : undefined,
            border: btn.active ? activeBorder : undefined,
            color: btn.active ? activeColor : inactiveColor,
          }}
        >
          {!compact && btn.label}
        </Button>
      ))}
    </div>
  );
};
