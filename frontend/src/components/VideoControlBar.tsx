interface VideoControlBarProps {
  running: boolean;
  enabled: boolean;
  fps: number;
  isRecording: boolean;
  currentProvider?: string;
  currentModel?: string;
  toggleRecording: () => void;
  onModelSettingsClick?: () => void;
}

export function VideoControlBar({
  running,
  enabled,
  fps,
  isRecording,
  currentProvider,
  currentModel,
  toggleRecording,
  onModelSettingsClick,
}: VideoControlBarProps) {
  const pulseAnimationStyle = `
    @keyframes pulse {
      0%, 100% {
        opacity: 1;
        transform: scale(1);
      }
      50% {
        opacity: 0.6;
        transform: scale(1.1);
      }
    }
  `;

  return (
    <div style={{ 
      display: "flex", 
      justifyContent: "space-between", 
      alignItems: "center", 
      marginBottom: "12px", 
      padding: "8px 12px", 
      borderRadius: "8px", 
      background: "#1a1a1a", 
      border: "1px solid #333" 
    }}>
      <style>{pulseAnimationStyle}</style>
      
      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{ 
            width: "8px", 
            height: "8px", 
            borderRadius: "50%", 
            background: running ? "#4caf50" : "#ff9800", 
            boxShadow: running ? "0 0 4px rgba(76, 175, 80, 0.3)" : "none",
            transition: "all 0.3s ease"
          }} />
          <span style={{ color: "#e0e0e0", fontSize: "14px", fontWeight: "500" }}>
            {running ? "●" : "○"}
          </span>
          <span style={{ color: "#e0e0e0", fontSize: "14px" }}>
            {running ? "运行中" : "启动中"}
          </span>
        </div>
        
        {currentProvider && currentModel && (
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "4px 12px",
            borderRadius: "6px",
            background: "rgba(76, 175, 80, 0.1)",
            border: "1px solid rgba(76, 175, 80, 0.2)"
          }}>
            <span style={{ color: "#888", fontSize: "12px", fontWeight: "500" }}>当前模型：</span>
            <span style={{ color: "#4caf50", fontSize: "12px", fontWeight: "500" }}>
              {currentProvider === "openroute" ? "OpenRouter" : 
               currentProvider === "aliyun" ? "阿里云" : 
               currentProvider === "modelscope" ? "ModelScope" : 
               currentProvider}
            </span>
            <span style={{ color: "#aaa", fontSize: "11px" }}>
              {currentModel.split('/').pop() || currentModel}
            </span>
          </div>
        )}
      </div>
      
      <div style={{ display: "flex", alignItems: "center", gap: "16px", fontSize: "13px", color: "#aaa" }}>
        <span>采帧：</span>
        <span style={{ 
          color: enabled ? "#4caf50" : "#ff9800", 
          fontWeight: "500",
          padding: "2px 8px",
          borderRadius: "4px",
          background: enabled ? "rgba(76, 175, 80, 0.1)" : "rgba(255, 152, 0, 0.1)"
        }}>
          {enabled ? "开启" : "关闭"}
        </span>
        <span>| fps={fps}</span>
      </div>

      <button
        onClick={toggleRecording}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          padding: "6px 14px",
          borderRadius: "6px",
          border: isRecording ? "1px solid #f44336" : "1px solid #333",
          background: isRecording ? "rgba(244, 67, 54, 0.15)" : "#252525",
          color: isRecording ? "#f44336" : "#e0e0e0",
          fontSize: "12px",
          fontWeight: "500",
          cursor: "pointer",
          transition: "all 0.2s ease"
        }}
        onMouseEnter={(e) => {
          if (!isRecording) {
            e.currentTarget.style.borderColor = "#4a4a4a";
            e.currentTarget.style.background = "#2a2a2a";
          }
        }}
        onMouseLeave={(e) => {
          if (!isRecording) {
            e.currentTarget.style.borderColor = "#333";
            e.currentTarget.style.background = "#252525";
          }
        }}
      >
        <div style={{
          width: "10px",
          height: "10px",
          borderRadius: "50%",
          background: isRecording ? "#f44336" : "#e0e0e0",
          boxShadow: isRecording ? "0 0 6px rgba(244, 67, 54, 0.6)" : "none",
          animation: isRecording ? "pulse 1.5s infinite" : "none"
        }} />
        <span>{isRecording ? "停止录制" : "开始录制"}</span>
      </button>

      {onModelSettingsClick && (
        <button
          onClick={onModelSettingsClick}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            border: "1px solid #333",
            background: "#252525",
            color: "#e0e0e0",
            fontSize: "12px",
            fontWeight: "500",
            cursor: "pointer",
            transition: "all 0.2s ease"
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = "#4a4a4a";
            e.currentTarget.style.background = "#2a2a2a";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = "#333";
            e.currentTarget.style.background = "#252525";
          }}
        >
          模型设置
        </button>
      )}
    </div>
  );
}
