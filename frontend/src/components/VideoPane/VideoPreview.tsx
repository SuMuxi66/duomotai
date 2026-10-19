/**
 * VideoPreview - 视频预览区域
 *
 * video/img 元素 + 空状态占位符 + 信息栏。
 * 流里是原始画面（旋转是每台后端自己的偏好）：按本机 CAMERA_ROTATION 做 CSS
 * 旋转仅影响本前端显示；AI 看到的帧由后端（/usb-camera/latest 与视觉链路）
 * 按同一角度转正，两边始终一致。
 */
import React, { useEffect, useRef, useState } from "react";
import { Camera, Glasses } from "lucide-react";
import { radius } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface VideoPreviewProps {
  theme: Theme;
  useGlassesCamera: boolean;
  glassesImgRef?: React.Ref<HTMLImageElement>;
  videoRef: React.RefObject<HTMLVideoElement>;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  isCameraConnected: boolean;
  enabled: boolean;
  glassesConnected: boolean;
  fps: number;
  videoResolution: { width: number; height: number };
  onLoadedMetadata: (width: number, height: number) => void;
  /** 后端 USB 摄像头 MJPEG 流地址（为空则不显示） */
  usbSrc?: string;
  /** MJPEG 流出错（断开/无画面）回调 */
  onStreamError?: () => void;
  /** 本机画面旋转角度（0/90/180/270，权威在后端 CAMERA_ROTATION，仅 CSS 显示） */
  videoRotation?: number;
}

const keyframes = `
@keyframes preview-pulse {
  0%, 100% { transform: scale(1); opacity: 0.85; }
  50% { transform: scale(1.08); opacity: 0.45; }
}
@keyframes preview-spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
`;

function PlaceholderCard({
  theme,
  icon,
  title,
  subtitle,
}: {
  theme: Theme;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  const isDark = theme.mode === "dark";
  return (
    <div style={{
      position: "absolute",
      top: "50%",
      left: "50%",
      transform: "translate(-50%, -50%)",
      zIndex: 5,
      textAlign: "center",
      minWidth: 220,
      padding: "28px 32px",
      background: isDark ? "rgba(30, 41, 59, 0.72)" : "rgba(255, 255, 255, 0.9)",
      backdropFilter: "blur(12px)",
      WebkitBackdropFilter: "blur(12px)",
      borderRadius: radius.xl,
      border: `1px solid ${isDark ? "rgba(255,255,255,0.08)" : "rgba(148, 163, 184, 0.2)"}`,
      boxShadow: isDark
        ? "0 20px 60px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05)"
        : "0 20px 60px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.8)",
    }}>
      <div style={{
        width: 64,
        height: 64,
        margin: "0 auto 16px",
        borderRadius: "50%",
        background: isDark ? "rgba(255,255,255,0.06)" : "rgba(148, 163, 184, 0.12)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
      }}>
        <div style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          background: isDark ? "rgba(255,255,255,0.08)" : "rgba(148, 163, 184, 0.18)",
          animation: "preview-pulse 2s ease-in-out infinite",
        }} />
        <div style={{ position: "relative", zIndex: 1 }}>{icon}</div>
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: theme.text, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 12, color: theme.textMuted, lineHeight: 1.5 }}>{subtitle}</div>
    </div>
  );
}

export const VideoPreview: React.FC<VideoPreviewProps> = ({
  theme,
  useGlassesCamera,
  glassesImgRef,
  videoRef,
  canvasRef,
  isCameraConnected,
  enabled,
  glassesConnected,
  onLoadedMetadata,
  usbSrc,
  onStreamError,
  videoRotation = 0,
}) => {
  const isDark = theme.mode === "dark";

  // 容器尺寸监听：90°/270° 旋转时需要宽高互换适配
  const wrapRef = useRef<HTMLDivElement>(null);
  const [wrapSize, setWrapSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWrapSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setWrapSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const rot = ((videoRotation % 360) + 360) % 360;
  const swapped = rot === 90 || rot === 270;
  // 90°/270°：布局宽高互换后旋转，画面正好填满容器；0°/180°：常规样式加旋转
  const usbImgStyle: React.CSSProperties = swapped
    ? {
        position: "absolute",
        top: "50%",
        left: "50%",
        width: wrapSize.h || "100%",
        height: wrapSize.w || "100%",
        objectFit: "contain",
        objectPosition: "center center",
        transform: `translate(-50%, -50%) rotate(${rot}deg)`,
        zIndex: 1,
      }
    : {
        width: "100%",
        height: "100%",
        objectFit: "contain",
        objectPosition: "center center",
        transform: rot ? `rotate(${rot}deg)` : undefined,
        zIndex: 1,
        position: "relative",
      };

  return (
    <div ref={wrapRef} style={{
      flex: 1,
      position: "relative",
      background: isDark
        ? "linear-gradient(180deg, #1e293b 0%, #0f172a 100%)"
        : "linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 100%)",
      overflow: "hidden",
    }}>
      <style>{keyframes}</style>

      {useGlassesCamera ? (
        <img
          ref={glassesImgRef}
          src={undefined}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            objectPosition: "center center",
            transform: "rotate(-90deg)",
            zIndex: 1,
            position: "relative",
          }}
          alt="眼镜摄像头画面"
        />
      ) : (
        <>
          {/* 后端 USB 摄像头 MJPEG 流：<img> 对 multipart/x-mixed-replace 支持可靠 */}
          {usbSrc && (
            <img
              src={usbSrc}
              style={usbImgStyle}
              alt="USB摄像头画面"
              onLoad={(e) => {
                const el = e.currentTarget;
                if (el.naturalWidth && el.naturalHeight) {
                  onLoadedMetadata(el.naturalWidth, el.naturalHeight);
                }
              }}
              onError={() => onStreamError?.()}
            />
          )}
          {/* 隐藏截帧源，保持 videoRef/canvas 兼容现有 captureNow 链路 */}
          <video
            ref={videoRef}
            style={{ display: "none" }}
            playsInline
          />
        </>
      )}
      <canvas ref={canvasRef} style={{ display: "none" }} />

      {/* 本地摄像头占位符 */}
      {!isCameraConnected && enabled && !useGlassesCamera && (
        <PlaceholderCard
          theme={theme}
          icon={<Camera size={28} color={theme.textSecondary} strokeWidth={1.5} />}
          title="正在连接后端摄像头"
          subtitle="等待 USB 摄像头画面"
        />
      )}

      {/* 眼镜摄像头占位符 */}
      {useGlassesCamera && !glassesConnected && (
        <PlaceholderCard
          theme={theme}
          icon={<Glasses size={28} color={theme.textSecondary} strokeWidth={1.5} />}
          title="等待眼镜连接"
          subtitle="请确认眼镜已连接并处于在线状态"
        />
      )}
    </div>
  );
};
