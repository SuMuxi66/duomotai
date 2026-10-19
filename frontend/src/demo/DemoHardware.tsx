import { useState, useCallback, useRef, useEffect } from "react";
import { useTheme } from "../contexts/ThemeContext";

// ===== 样式工厂 =====
function useStyles() {
  const { theme } = useTheme();
  const page: React.CSSProperties = {
    height: "100vh", background: theme.bg, color: theme.text, fontFamily: "'Inter', sans-serif",
    display: "flex", flexDirection: "column", overflow: "hidden",
  };
  const header: React.CSSProperties = {
    background: `linear-gradient(135deg, #1a3a2a, #0d2818)`, color: "#fff",
    padding: "16px 24px",
  };
  const card: React.CSSProperties = {
    background: theme.bgSecondary, borderRadius: "10px", padding: "16px",
    border: `1px solid ${theme.borderLight}`, marginBottom: "12px",
  };
  const fanPanel: React.CSSProperties = {
    background: theme.bg, borderRadius: "12px", padding: "20px",
    border: `1px solid ${theme.borderLight}`, display: "flex", alignItems: "center", gap: "20px",
  };
  const gauge: React.CSSProperties = {
    width: "100%", height: "120px", borderRadius: "8px", position: "relative",
    overflow: "hidden", background: "#0a0a1a",
  };
  const logLine: React.CSSProperties = {
    padding: "6px 10px", marginBottom: "3px", borderRadius: "4px", fontSize: "12px",
    background: "#0d0d1a", color: "#a0e0a0", fontFamily: "monospace", borderLeft: "2px solid #3a6",
  };
  return { theme, page, header, card, fanPanel, gauge, logLine };
}

function StatusBar({ title, onBack }: { title: string; onBack: () => void }) {
  const { theme } = useTheme();
  const statusBar: React.CSSProperties = {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "8px 20px", background: theme.bgSecondary, borderBottom: `1px solid ${theme.border}`,
    fontSize: "12px", color: theme.textMuted,
  };
  const [time, setTime] = useState(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  useEffect(() => { const t = setInterval(() => setTime(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })), 1000); return () => clearInterval(t); }, []);
  return (
    <div style={statusBar}>
      <span style={{ cursor: "pointer", color: theme.primary }} onClick={onBack}>← 返回</span>
      <span style={{ fontWeight: 600, fontSize: "13px" }}>{title}</span>
      <span>{time}</span>
    </div>
  );
}

export function DemoHardware() {
  const { theme, page, header, card, fanPanel, gauge, logLine } = useStyles();
  const [logs, setLogs] = useState<string[]>([
    "[HARDWARE] initializing hardware interfaces...",
    "[HARDWARE] miIO device: connected (192.168.1.100)",
    "[HARDWARE] MLX90640: serial port opened (COM3, 115200 baud)",
  ]);
  const [fanOn, setFanOn] = useState(false);
  const [fanAnimating, setFanAnimating] = useState(false);
  const [tempRunning, setTempRunning] = useState(false);
  const [tempHistory, setTempHistory] = useState<number[]>([125, 128, 132, 135, 138, 142, 140, 136, 133, 130]);
  const [currentTemp, setCurrentTemp] = useState(0);
  const logEndRef = useRef<HTMLDivElement>(null);
  const tempIntervalRef = useRef<number | null>(null);

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);
  useEffect(() => { return () => { if (tempIntervalRef.current) clearInterval(tempIntervalRef.current); }; }, []);

  const addLog = useCallback((msg: string) => setLogs(prev => [...prev.slice(-49), msg]), []);

  // ===== 排气扇 =====
  const handleFanToggle = () => {
    if (fanAnimating) return;
    setFanAnimating(true);

    if (!fanOn) {
      addLog("[FAN] voice command: 打开排气扇");
      addLog("[FAN] local regex matched: FAN_OPEN_PATTERN (0.3ms)");
      setTimeout(() => {
        addLog("[FAN] miIO → send: set_power=on (192.168.1.100)");
        setTimeout(() => {
          addLog("[FAN] ✅ status readback: power=on → command verified");
          addLog("[FAN] ventilation started, 1200m³/h");
          setFanOn(true);
          setFanAnimating(false);
        }, 600);
      }, 400);
    } else {
      addLog("[FAN] voice command: 关闭排气扇");
      addLog("[FAN] local regex matched: FAN_CLOSE_PATTERN (0.3ms)");
      setTimeout(() => {
        addLog("[FAN] miIO → send: set_power=off (192.168.1.100)");
        setTimeout(() => {
          addLog("[FAN] ✅ status readback: power=off → command verified");
          setFanOn(false);
          setFanAnimating(false);
        }, 600);
      }, 400);
    }
  };

  // ===== 红外测温 =====
  const handleTempToggle = () => {
    if (tempRunning) {
      if (tempIntervalRef.current) { clearInterval(tempIntervalRef.current); tempIntervalRef.current = null; }
      setTempRunning(false);
      addLog("[TEMP] daemon thread stopped, serial port closed");
      return;
    }

    setTempRunning(true);
    addLog("[TEMP] MLX90640: handshake OK, device identified");
    addLog("[TEMP] daemon thread started: 768px/frame, 10fps");
    addLog("[TEMP] sliding window frame sync: buffer aligned");

    tempIntervalRef.current = window.setInterval(() => {
      const base = currentTemp > 0 ? currentTemp : 120 + Math.random() * 30;
      const delta = (Math.random() - 0.5) * 6;
      const newTemp = Math.round((base + delta) * 10) / 10;
      setCurrentTemp(newTemp);
      setTempHistory(prev => [...prev.slice(-29), newTemp]);
    }, 800);
  };

  // 温度颜色
  const tempColor = currentTemp > 160 ? "#e74c3c" : currentTemp > 130 ? "#f39c12" : "#27ae60";

  return (
    <div style={page}>
      <StatusBar title="设备控制中心 · 硬件联动" onBack={() => window.location.hash = "#/demo"} />
      <div style={header}>
        <div style={{ fontSize: "13px", opacity: 0.8 }}>智能排气扇 / MLX90640 红外测温 / 软硬件联动</div>
        <div style={{ fontSize: "20px", fontWeight: 700, marginTop: "2px" }}>硬件设备控制台</div>
      </div>

      <div style={{ flex: 1, display: "flex", gap: "12px", padding: "12px", overflow: "auto", minHeight: 0 }}>
        {/* 左侧主面板 */}
        <div style={{ flex: "1 1 500px" }}>
          {/* 排气扇控制 */}
          <div style={card}>
            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "12px" }}>💨 智能排气扇</div>
            <div style={fanPanel}>
              <div style={{
                width: "80px", height: "80px", borderRadius: "50%", position: "relative",
                background: "#0a0a1a", border: `2px solid ${fanOn ? "#27ae60" : "#333"}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                transition: "all 0.3s",
              }}>
                <div style={{ fontSize: "32px", animation: fanOn ? "spin 1s linear infinite" : "none" }}>🌀</div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: "18px", fontWeight: 700, color: fanOn ? "#27ae60" : theme.textMuted }}>
                  {fanOn ? "● 运行中" : "○ 已关闭"}
                </div>
                <div style={{ fontSize: "12px", color: theme.textMuted, marginTop: "4px" }}>
                  状态: {fanOn ? "排气扇已开启 · 1200m³/h" : "待机中"}
                </div>
                <div style={{ fontSize: "11px", color: theme.textMuted }}>
                  控制协议: miIO · 状态回读: {fanOn ? "已确认" : "未确认"}
                </div>
              </div>
              <button
                onClick={handleFanToggle}
                disabled={fanAnimating}
                style={{
                  width: "56px", height: "56px", borderRadius: "50%", border: "none", cursor: fanAnimating ? "not-allowed" : "pointer",
                  background: fanOn ? "#c0392b" : "#27ae60", color: "#fff",
                  fontSize: "13px", fontWeight: 700, transition: "0.3s", opacity: fanAnimating ? 0.5 : 1,
                  boxShadow: fanOn ? "none" : "0 0 20px rgba(39,174,96,0.3)",
                }}
              >
                {fanAnimating ? "..." : fanOn ? "关" : "开"}
              </button>
            </div>
          </div>

          {/* 红外测温 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <span style={{ fontWeight: 600, fontSize: "13px" }}>🌡 MLX90640 红外测温</span>
              <button
                onClick={handleTempToggle}
                style={{
                  background: tempRunning ? "#c0392b" : "#27ae60", color: "#fff",
                  border: "none", padding: "6px 16px", borderRadius: "6px", cursor: "pointer",
                  fontWeight: 600, fontSize: "12px",
                }}
              >
                {tempRunning ? "停止采集" : "开始采集"}
              </button>
            </div>

            {/* 实时温度仪表 */}
            <div style={gauge}>
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
                {tempRunning ? (
                  <>
                    <div style={{ fontSize: "48px", fontWeight: 700, fontFamily: "monospace", color: tempColor, transition: "color 0.3s" }}>
                      {currentTemp.toFixed(1)}
                    </div>
                    <div style={{ fontSize: "14px", color: tempColor, marginTop: "-4px" }}>℃</div>
                    <div style={{ fontSize: "11px", color: theme.textMuted, marginTop: "4px" }}>
                      768 像素点 · 帧同步解析
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: "14px", color: "#555" }}>点击"开始采集"启动测温</div>
                )}
              </div>
              {/* 扫描线 */}
              {tempRunning && <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "2px", background: "rgba(39,174,96,0.3)", animation: "scan 2s linear infinite" }} />}
            </div>

            {/* 温度曲线 */}
            {tempRunning && tempHistory.length > 1 && (
              <div style={{ marginTop: "12px" }}>
                <div style={{ fontSize: "11px", color: theme.textMuted, marginBottom: "4px" }}>实时温度趋势（最近 30 帧）</div>
                <div style={{ display: "flex", alignItems: "flex-end", height: "50px", gap: "2px" }}>
                  {tempHistory.map((t, i) => {
                    const h = Math.max(4, ((t - 80) / 120) * 46);
                    return (
                      <div key={i} style={{
                        flex: 1, height: `${h}px`, borderRadius: "2px 2px 0 0",
                        background: t > 150 ? "#e74c3c" : t > 120 ? "#f39c12" : "#27ae60",
                        opacity: 0.7 + (i === tempHistory.length - 1 ? 0.3 : 0),
                        transition: "height 0.3s",
                      }} />
                    );
                  })}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: theme.textMuted, marginTop: "2px" }}>
                  <span>80℃</span>
                  <span>200℃</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 右侧：系统日志 */}
        <div style={{ ...card, flex: "0 0 360px", display: "flex", flexDirection: "column" }}>
          <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>📋 系统日志</div>
          <div style={{ flex: 1, overflow: "auto", maxHeight: "500px" }}>
            {logs.map((line, i) => (
              <div key={i} style={logLine}>{line}</div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      </div>
    </div>
  );
}