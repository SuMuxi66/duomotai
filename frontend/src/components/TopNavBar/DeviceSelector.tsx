/**
 * DeviceSelector - 设备选择器
 *
 * 麦克风/扬声器/摄像头下拉框。
 * 支持 compact 模式：在中等宽度屏幕上折叠为一个“设备”图标按钮，
 * 点击后弹出包含三个选择器的下拉面板，节省顶部导航栏横向空间。
 */
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Select } from "@/components/ui/Select";
import { Button } from "@/components/ui/Button";
import { CustomIcon } from "@/components/CustomIcon";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";
import type { DeviceInfo } from "@/types";

export interface DeviceSelectorProps {
  theme: Theme;
  deviceInfo: DeviceInfo | null;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  glassesCameraConnected: boolean;
  glassesAudioConnected: boolean;
  onAudioDeviceChange: (deviceId: string) => void;
  onAudioOutputDeviceChange: (deviceId: string) => void;
  onVideoDeviceChange: (deviceId: string) => void;
  /** 是否显示摄像头选择（分步前端 5182 仅需麦克风/扬声器时可传 false） */
  showVideo?: boolean;
  /** 紧凑模式：只显示一个设备图标按钮，点击展开面板 */
  compact?: boolean;
}

export const DeviceSelector: React.FC<DeviceSelectorProps> = ({
  theme,
  deviceInfo,
  selectedAudioDevice,
  selectedAudioOutputDevice,
  selectedVideoDevice,
  glassesCameraConnected,
  glassesAudioConnected,
  onAudioDeviceChange,
  onAudioOutputDeviceChange,
  onVideoDeviceChange,
  showVideo = true,
  compact = false,
}) => {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0, width: 0 });

  const isDark = theme.mode === "dark";
  const controlBorder = isDark ? "rgba(255, 255, 255, 0.06)" : theme.borderLight;

  const audioOptions = [
    { value: "", label: "默认" },
    ...(glassesAudioConnected ? [{ value: "__GLASSES_AUDIO__", label: "眼镜麦克风" }] : []),
    ...(deviceInfo?.audioDevices.map((d) => ({
      value: d.deviceId,
      label: d.label || `麦克风 ${d.deviceId.slice(0, 8)}`,
    })) || []),
  ];

  const audioOutputOptions = [
    { value: "", label: "默认扬声器" },
    ...(glassesAudioConnected ? [{ value: "__GLASSES_AUDIO__", label: "眼镜扬声器" }] : []),
    ...(deviceInfo?.audioOutputDevices?.map((d) => ({
      value: d.deviceId,
      label: d.label || `扬声器 ${d.deviceId.slice(0, 8)}`,
    })) || []),
  ];

  const videoOptions = [
    { value: "", label: deviceInfo?.videoDevices?.length ? "默认摄像头" : "未检测到摄像头" },
    ...(glassesCameraConnected ? [{ value: "__GLASSES_CAMERA__", label: "眼镜摄像头" }] : []),
    ...(deviceInfo?.videoDevices.map((d) => ({
      value: d.deviceId,
      label: d.label || `摄像头 ${d.deviceId.slice(0, 8)}`,
    })) || []),
  ];

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!wrapperRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  useEffect(() => {
    if (open && wrapperRef.current) {
      const updatePosition = () => {
        const rect = wrapperRef.current!.getBoundingClientRect();
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
  }, [open]);

  const renderSelectors = (width: number | string = 200) => (
    <div style={{ display: "flex", flexDirection: "column", gap: `${spacing.md}px` }}>
      <div title="选择麦克风设备">
        <Select
          value={selectedAudioDevice}
          onChange={onAudioDeviceChange}
          options={audioOptions}
          placeholder="默认"
          theme={theme}
          iconName="mic"
          width={width}
        />
      </div>
      <div title="选择扬声器设备">
        <Select
          value={selectedAudioOutputDevice}
          onChange={onAudioOutputDeviceChange}
          options={audioOutputOptions}
          placeholder="默认扬声器"
          theme={theme}
          iconName="speaker"
          width={width}
        />
      </div>
      {showVideo && (
        <div title="选择摄像头设备">
          <Select
            value={selectedVideoDevice}
            onChange={onVideoDeviceChange}
            options={videoOptions}
            placeholder={deviceInfo?.videoDevices?.length ? "选择摄像头" : "未检测到摄像头"}
            theme={theme}
            iconName="camera"
            width={width}
          />
        </div>
      )}
    </div>
  );

  if (!compact) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
        <div title="选择麦克风设备">
          <Select
            value={selectedAudioDevice}
            onChange={onAudioDeviceChange}
            options={audioOptions}
            placeholder="默认"
            theme={theme}
            iconName="mic"
            width={130}
          />
        </div>
        <div title="选择扬声器设备">
          <Select
            value={selectedAudioOutputDevice}
            onChange={onAudioOutputDeviceChange}
            options={audioOutputOptions}
            placeholder="默认扬声器"
            theme={theme}
            iconName="speaker"
            width={130}
          />
        </div>
        {showVideo && (
          <div title="选择摄像头设备">
            <Select
              value={selectedVideoDevice}
              onChange={onVideoDeviceChange}
              options={videoOptions}
              placeholder={deviceInfo?.videoDevices?.length ? "选择摄像头" : "未检测到摄像头"}
              theme={theme}
              iconName="camera"
              width={130}
            />
          </div>
        )}
      </div>
    );
  }

  return (
    <div ref={wrapperRef} style={{ position: "relative", flexShrink: 0 }}>
      <Button
        variant="secondary"
        theme={theme}
        title="音频/视频设备"
        onClick={() => setOpen((prev) => !prev)}
        icon={<CustomIcon name="mic" size={18} color={open ? theme.primary : theme.textSecondary} />}
        style={{
          height: 44,
          background: open ? (isDark ? "#000000" : "rgba(106, 159, 181, 0.12)") : undefined,
          border: open ? (isDark ? `1px solid ${theme.borderLight}` : `1px solid ${theme.primary}`) : undefined,
        }}
      />

      {open &&
        createPortal(
          <div
            ref={menuRef}
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
              padding: `${spacing.md}px`,
              zIndex: 999999,
            }}
          >
            {renderSelectors("100%")}
          </div>,
          document.body
        )}
    </div>
  );
};
