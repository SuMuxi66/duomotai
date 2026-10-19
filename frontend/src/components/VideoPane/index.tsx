import {
  useEffect,
  useRef,
  useCallback,
  useState,
  useImperativeHandle,
  forwardRef,
} from "react";
import type { Theme } from "@/lib/theme";
import type { CookingTimerInfo } from "@/components/ChatPane";
import { VideoPreview } from "./VideoPreview";
import { MonitorOverlay } from "./MonitorOverlay";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CustomIcon } from "@/components/CustomIcon";

export interface VideoPaneRef {
  captureNow: () => Promise<{ b64: string; width: number; height: number } | null>;
}

interface VideoPaneProps {
  onFrame?: (jpeg_b64: string, width: number, height: number) => void;
  enabled: boolean;
  fps: number;
  maxWidth: number;
  jpegQuality: number;
  videoDeviceId?: string;
  currentProvider?: string;
  currentModel?: string;
  theme: Theme;
  isRecording?: boolean;
  latestObservation?: string;
  isCookingScene?: boolean;
  cookingSuggestion?: string;
  cookingTimers?: CookingTimerInfo[];
  onCancelCookingTimer?: () => void;
  onRunningChange?: (running: boolean) => void;
  onLatencyChange?: (latencyMs: number) => void;
  useGlassesCamera?: boolean;
  glassesImgRef?: React.Ref<HTMLImageElement>;
  onSourceChange?: (source: "local" | "glasses") => void;
  useGlassesAudio?: boolean;
  glassesAudioConnected?: boolean;
  onAudioSourceChange?: (source: "local" | "glasses") => void;
  onResolutionChange?: (resolution: { width: number; height: number }) => void;
  onGlassesConnectedChange?: (connected: boolean) => void;
  /** 视觉分析开关回调（按钮位于视频流头部、分辨率右侧） */
  onVisionToggle?: () => void;
  /** 本机画面旋转角度（权威在后端 CAMERA_ROTATION，仅 CSS 显示） */
  videoRotation?: number;
}

export const VideoPane = forwardRef<VideoPaneRef, VideoPaneProps>((props, ref) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const onFrameRef = useRef(props.onFrame);
  const onRunningChangeRef = useRef(props.onRunningChange);
  const onLatencyChangeRef = useRef(props.onLatencyChange);
  const rafIdRef = useRef<number>(0);
  const lastFrameTimeRef = useRef<number>(0);
  const [videoResolution, setVideoResolution] = useState<{ width: number; height: number }>({ width: 2560, height: 1440 });
  const [isCameraConnected, setIsCameraConnected] = useState(false);
  const [glassesConnected, setGlassesConnected] = useState(false);
  const [usbSrc, setUsbSrc] = useState<string>("");

  useEffect(() => {
    props.onResolutionChange?.(videoResolution);
  }, [videoResolution]);

  useEffect(() => {
    props.onGlassesConnectedChange?.(glassesConnected);
  }, [glassesConnected]);

  // 眼镜摄像头连接状态轮询
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch("/glasses-frame/latest");
        const data = await res.json();
        setGlassesConnected(data.connected);
      } catch {}
    }, 3000);
    fetch("/glasses-frame/latest").then((r) => r.json()).then((d) => setGlassesConnected(d.connected)).catch(() => {});
    return () => clearInterval(interval);
  }, []);

  onFrameRef.current = props.onFrame;
  onRunningChangeRef.current = props.onRunningChange;
  onLatencyChangeRef.current = props.onLatencyChange;

  const measureLatency = useCallback(() => {
    const video = videoRef.current;
    if (!video || !video.srcObject) {
      rafIdRef.current = requestAnimationFrame(measureLatency);
      return;
    }
    const now = performance.now();
    const latency = now - lastFrameTimeRef.current;
    if (lastFrameTimeRef.current > 0 && latency > 0 && latency < 1000) {
      onLatencyChangeRef.current?.(Math.round(latency));
    }
    rafIdRef.current = requestAnimationFrame(measureLatency);
  }, []);

  const onVideoFrame = useCallback((now: number) => {
    lastFrameTimeRef.current = now;
  }, []);

  useEffect(() => {
    rafIdRef.current = requestAnimationFrame(function measureLoop(now) {
      onVideoFrame(now);
      rafIdRef.current = requestAnimationFrame(measureLoop);
    });
    return () => {
      if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
    };
  }, [onVideoFrame]);

  // 暴露给父组件的手动采集方法
  useImperativeHandle(ref, () => ({
    captureNow: async () => {
      console.log("[VideoPane] captureNow 被调用, useGlassesCamera:", props.useGlassesCamera);
      if (props.useGlassesCamera) {
        try {
          const res = await fetch("/glasses-frame/latest");
          const data = await res.json();
          if (data && data.frame) {
            return { b64: data.frame, width: 640, height: 480 };
          }
          console.log("[VideoPane] 眼镜摄像头无帧数据");
          return null;
        } catch (e) {
          console.error("[VideoPane] 采集眼镜帧失败:", e);
          return null;
        }
      }
      console.log("[VideoPane] captureNow usb 分支，从后端拉取最新帧");
      try {
        // max_w=1280：让后端把 2K 原图缩到 1280×720 再出（base64 体积 450KB → ~110KB）。
        // 原图直传会在发消息瞬间挤占主线程与 WS 带宽，连 MJPEG 流一起卡（2026-09-16 实测）。
        const res = await fetch("/usb-camera/latest?max_w=1280&quality=80");
        const data = await res.json();
        if (data && data.frame) {
          return { b64: data.frame, width: 1280, height: 720 };
        }
        console.log("[VideoPane] 后端 USB 摄像头无帧数据");
        return null;
      } catch (e) {
        console.error("[VideoPane] 采集后端帧失败:", e);
        return null;
      }
    },
  }));

  // 摄像头初始化与预览：后端 USB 摄像头代理（MJPEG 流，多前端共享）
  useEffect(() => {
    if (props.useGlassesCamera) {
      setUsbSrc("");
      setIsCameraConnected(false);
      onRunningChangeRef.current?.(false);
      return;
    }
    if (!props.enabled) {
      setUsbSrc("");
      setIsCameraConnected(false);
      onRunningChangeRef.current?.(false);
      return;
    }
    setUsbSrc("/usb-camera/stream");
    onRunningChangeRef.current?.(true);
    return () => {
      setUsbSrc("");
      setIsCameraConnected(false);
      onRunningChangeRef.current?.(false);
    };
  }, [props.enabled, props.useGlassesCamera]);

  // USB 摄像头流健康检查：长时间运行后后端捕获线程可能卡死（cap.read 阻塞），
  // MJPEG 心跳继续推旧帧导致前端显示"运行中"但画面冻结。
  // 定时检查后端最新帧时间戳，过期则强制刷新 <img> src 触发重连。
  useEffect(() => {
    if (props.useGlassesCamera || !props.enabled) return;

    const check = setInterval(async () => {
      try {
        const res = await fetch("/usb-camera/status");
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        // data.timestamp 是后端最新帧的毫秒级时间戳；超过 15s 无新帧视为冻结
        if (data.timestamp && Date.now() - data.timestamp > 15000) {
          console.warn(`[VideoPane] 流冻结检测：帧已过期 ${Math.round((Date.now() - data.timestamp) / 1000)}s，强制重连`);
          setIsCameraConnected(false);
          setUsbSrc(`/usb-camera/stream?t=${Date.now()}`);
        }
      } catch {
        // 连不上后端（可能重启中）也触发重连
        console.warn("[VideoPane] 摄像头状态检查失败，触发重连");
        setIsCameraConnected(false);
        setUsbSrc(`/usb-camera/stream?t=${Date.now()}`);
      }
    }, 5000);

    return () => clearInterval(check);
  }, [props.enabled, props.useGlassesCamera]);

  const { theme } = props;

  // 状态徽标：眼镜模式看连接轮询，USB 模式看首帧是否加载
  const streamActive = props.useGlassesCamera ? glassesConnected : isCameraConnected;

  const isDark = theme.mode === "dark";
  const visionActive = props.enabled;
  const visionBtnBg = isDark ? "#000000" : "rgba(106, 159, 181, 0.12)";
  const visionBtnBorder = isDark ? `1px solid ${theme.borderLight}` : `1px solid ${theme.primary}`;
  const visionBtnColor = isDark ? theme.text : theme.primary;
  const visionBtnInactiveColor = isDark ? theme.textMuted : theme.textSecondary;

  const infoTag = (
    label: string,
    value: string,
    dotColor?: string | null,
  ) => (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 12px",
        background: theme.mode === "dark" ? theme.buttonSecondary : "rgba(248, 250, 252, 0.95)",
        border: `1px solid ${theme.borderLight}`,
        borderRadius: 16,
        fontSize: 12,
        flexShrink: 0,
      }}
    >
      <span style={{ color: theme.textMuted }}>{label}</span>
      <span style={{ color: theme.text, fontWeight: 600, whiteSpace: "nowrap" }}>{value}</span>
      {dotColor && (
        <div
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: dotColor,
            marginLeft: 4,
          }}
        />
      )}
    </div>
  );

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* 视频状态标题栏 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 24px",
          borderBottom: `1px solid ${theme.borderLight}`,
          background: theme.bgSecondary,
          flexShrink: 0,
          borderTopLeftRadius: 16,
          borderTopRightRadius: 16,
          borderBottomLeftRadius: 0,
          borderBottomRightRadius: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: theme.text, letterSpacing: "-0.3px" }}>
            实时视频流
          </span>
          {streamActive ? (
            <Badge variant="info" dot theme={theme}>运行中</Badge>
          ) : (
            <Badge variant="default" dot theme={theme}>已关闭</Badge>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {infoTag(
            "来源",
            props.useGlassesCamera ? "眼镜摄像头" : "USB摄像头",
            props.useGlassesCamera ? (glassesConnected ? "#22c55e" : "#ef4444") : null,
          )}
          {infoTag(
            "音频",
            props.useGlassesAudio ? "眼镜麦克风" : "电脑麦克风",
            props.useGlassesAudio ? (props.glassesAudioConnected ? "#22c55e" : "#ef4444") : null,
          )}
          {infoTag("分辨率", `${videoResolution.width}×${videoResolution.height}`)}
          {props.onVisionToggle && (
            <Button
              variant="secondary"
              theme={theme}
              onClick={props.onVisionToggle}
              title="视觉分析"
              icon={<CustomIcon name="eye" size={18} color={visionActive ? visionBtnColor : visionBtnInactiveColor} />}
              style={{
                height: 44,
                background: visionActive ? visionBtnBg : undefined,
                border: visionActive ? visionBtnBorder : undefined,
                color: visionActive ? visionBtnColor : visionBtnInactiveColor,
              }}
            >
              视觉分析
            </Button>
          )}
        </div>
      </div>

      <VideoPreview
        theme={theme}
        useGlassesCamera={props.useGlassesCamera ?? false}
        glassesImgRef={props.glassesImgRef}
        videoRef={videoRef}
        canvasRef={canvasRef}
        isCameraConnected={isCameraConnected}
        enabled={props.enabled}
        glassesConnected={glassesConnected}
        fps={props.fps}
        videoResolution={videoResolution}
        usbSrc={usbSrc}
        videoRotation={props.videoRotation}
        onLoadedMetadata={(width, height) => {
          setVideoResolution({ width, height });
          setIsCameraConnected(true);
        }}
        onStreamError={() => setIsCameraConnected(false)}
      />

      <MonitorOverlay
        theme={theme}
        cookingTimers={props.cookingTimers}
        onCancelCookingTimer={props.onCancelCookingTimer}
      />
    </div>
  );
});
