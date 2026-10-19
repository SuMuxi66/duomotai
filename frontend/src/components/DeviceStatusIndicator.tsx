import { DeviceInfo } from "../lib/device";

interface DeviceStatusIndicatorProps {
  deviceInfo: DeviceInfo | null;
}

export function DeviceStatusIndicator({ deviceInfo }: DeviceStatusIndicatorProps) {
  if (!deviceInfo) return null;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: "16px",
      marginBottom: "8px",
      padding: "6px 12px",
      borderRadius: "6px",
      background: "#1a1a1a",
      border: "1px solid #333"
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <div style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: deviceInfo.hasWebcam ? "#4caf50" : "#f44336",
          boxShadow: deviceInfo.hasWebcam ? "0 0 4px rgba(76, 175, 80, 0.3)" : "0 0 4px rgba(244, 67, 54, 0.3)",
          transition: "all 0.3s ease"
        }} />
        <span style={{ color: "#e0e0e0", fontSize: "12px" }}>
          摄像头 {deviceInfo.hasWebcam ? `(${deviceInfo.videoDevices.length})` : "未检测到"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <div style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: deviceInfo.hasMicrophone ? "#4caf50" : "#f44336",
          boxShadow: deviceInfo.hasMicrophone ? "0 0 4px rgba(76, 175, 80, 0.3)" : "0 0 4px rgba(244, 67, 54, 0.3)",
          transition: "all 0.3s ease"
        }} />
        <span style={{ color: "#e0e0e0", fontSize: "12px" }}>
          麦克风 {deviceInfo.hasMicrophone ? `(${deviceInfo.audioDevices.length})` : "未检测到"}
        </span>
      </div>
      {(!deviceInfo.hasWebcam || !deviceInfo.hasMicrophone) && (
        <span style={{ color: "#ff9800", fontSize: "11px", marginLeft: "auto" }}>
          ⚠️ 部分设备不可用
        </span>
      )}
    </div>
  );
}
