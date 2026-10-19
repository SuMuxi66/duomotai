import { useState, useCallback, useRef, useEffect } from "react";
import { useTheme } from "../contexts/ThemeContext";
import { startCamera, captureJpegBase64 } from "../lib/video";

// ===== 样式工厂 =====
function useStyles() {
  const { theme } = useTheme();
  const page: React.CSSProperties = {
    height: "100vh", background: theme.bg, color: theme.text, fontFamily: "'Inter', sans-serif",
    display: "flex", flexDirection: "column", overflow: "hidden",
  };
  const header: React.CSSProperties = {
    background: `linear-gradient(135deg, #1a1a2e, #16213e)`, color: "#fff",
    padding: "16px 24px",
  };
  const card: React.CSSProperties = {
    background: theme.bgSecondary, borderRadius: "10px", padding: "16px",
    border: `1px solid ${theme.borderLight}`, marginBottom: "12px",
  };
  const camFrame: React.CSSProperties = {
    width: "100%", aspectRatio: "16/9", borderRadius: "8px", position: "relative",
    overflow: "hidden", background: "#0a0a1a",
  };
  const logLine: React.CSSProperties = {
    padding: "6px 10px", marginBottom: "3px", borderRadius: "4px", fontSize: "12px",
    background: "#0d0d1a", color: "#a0e0a0", fontFamily: "monospace", borderLeft: "2px solid #3a6",
  };
  const timerCard: React.CSSProperties = {
    background: theme.bg, borderRadius: "8px", padding: "12px", marginBottom: "6px",
    border: `1px solid ${theme.borderLight}`, display: "flex", justifyContent: "space-between", alignItems: "center",
  };
  return { theme, page, header, card, camFrame, logLine, timerCard };
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

export function DemoVision() {
  const { theme, page, header, card, camFrame, logLine, timerCard } = useStyles();
  const [logs, setLogs] = useState<string[]>(["system initialized", "camera ready", "VLM 模型由后端配置决定"]);
  const [vlmCfg, setVlmCfg] = useState<{ provider: string; model: string }>({ provider: "", model: "" });
  const [vlmOutput, setVlmOutput] = useState("");
  const [vlmTyping, setVlmTyping] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [alarm, setAlarm] = useState(false);
  const [timers, setTimers] = useState<{ name: string; remaining: number; total: number }[]>([]);
  const [cameraActive, setCameraActive] = useState(false);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [cameraError, setCameraError] = useState(false);
  const timerRefs = useRef<{ [key: string]: number }>({});
  const logEndRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // 启动摄像头
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const video = videoRef.current;
        if (!video) return;
        // startCamera 现为后端 MJPEG 流，不返回 MediaStream
        await startCamera(video);
        streamRef.current = null;
        setCameraActive(true);
        addLog("[CAMERA] ✅ USB camera started");
      } catch {
        if (!cancelled) {
          setCameraError(true);
          addLog("[CAMERA] ⚠ camera unavailable, using demo mode");
        }
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach(t => t.stop());
    };
  }, []);

  // 从后端拉取当前用户选择的视觉模型配置（provider/model），不再持有厂商 API Key
  useEffect(() => {
    fetch("/config")
      .then(r => r.json())
      .then(config => {
        const provider = config?.vlm_provider || "";
        const model = config?.vlm_model || "";
        setVlmCfg({ provider, model });
        addLog(`[CONFIG] ✅ VLM 配置已从后端加载: ${provider || "默认"} / ${model || "默认"}`);
      })
      .catch(() => addLog("[CONFIG] ⚠ 后端配置加载失败，将使用后端默认设置"));
  }, []);

  const addLog = useCallback((msg: string) => setLogs(prev => [...prev.slice(-49), msg]), []);

  // 截帧
  const captureFrame = useCallback(async () => {
    const video = videoRef.current;
    if (!video || !cameraActive) return null;
    try {
      const { b64 } = await captureJpegBase64(video, 1280, 0.7);
      setSnapshot(`data:image/jpeg;base64,${b64}`);
      addLog(`[CAPTURE] frame: ${video.videoWidth}x${video.videoHeight} → JPEG ${Math.round(b64.length * 0.75 / 1024)}KB`);
      return b64;
    } catch {
      addLog("[CAPTURE] ⚠ frame capture failed");
      return null;
    }
  }, [cameraActive, addLog]);

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);
  useEffect(() => { return () => { Object.values(timerRefs.current).forEach(clearInterval); }; }, []);

  const typewrite = useCallback((text: string, speed = 20, cb?: () => void) => {
    setVlmTyping("");
    let i = 0;
    const t = setInterval(() => {
      i++;
      setVlmTyping(text.slice(0, i));
      if (i >= text.length) { clearInterval(t); cb?.(); }
    }, speed);
    return t;
  }, []);

  // ===== 视觉分析 =====
  const handleAnalyze = async () => {
    if (analyzing) return;
    setAnalyzing(true);
    setAlarm(false);
    setVlmOutput("");
    setVlmTyping("");

    // 截帧
    const b64 = await captureFrame();
    addLog(`[NETWORK] POST → /api/proxy/vlm (provider: ${vlmCfg.provider || "后端默认"})`);

    let useRealApi = false;

    if (b64) {
      try {
        const prompt = "分析厨房画面内容，判断是否有安全隐患。请用JSON格式返回，包含字段：scene（场景描述）、objects（物体列表）、hazard_detected（布尔）、hazard_types（危险类型数组）、description（详细描述）、severity（low/medium/high）、confidence（0-1置信度）。仅返回JSON，不要包含其他内容。";
        addLog("[NETWORK] POST → /api/proxy/vlm (复用后端 VLM 模型提供组件)");
        const resp = await fetch("/api/proxy/vlm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt,
            image_b64: b64,
            vlm_provider: vlmCfg.provider || undefined,
            model: vlmCfg.model || undefined,
          }),
          signal: AbortSignal.timeout(60000),
        });
        if (resp.ok) {
          const data = await resp.json();
          const content = data?.content || data?.error || "";
          const jsonMatch = content.match(/\{[\s\S]*\}/);
          const jsonStr = jsonMatch ? jsonMatch[0] : content;
          try {
            JSON.parse(jsonStr);
            addLog(`[NETWORK] ✅ VLM proxy: HTTP ${resp.status} (model: ${data?.model || vlmCfg.model || "默认"})`);
            useRealApi = true;
            typewrite(jsonStr, 15, () => {
              setVlmOutput(jsonStr);
              setAnalyzing(false);
              addLog("[VLM] ✅ 视觉分析完成");
            });
          } catch {
            addLog("[NETWORK] ⚠ VLM 响应不是有效 JSON，回退到 mock");
          }
        } else {
          const errText = await resp.text().catch(() => "");
          addLog(`[NETWORK] ⚠ VLM proxy: HTTP ${resp.status} ${errText.slice(0, 100)}`);
        }
      } catch (e: any) {
        addLog(`[NETWORK] ⚠ VLM proxy error: ${e?.name || e?.message || "unknown"}`);
      }
    } else {
      addLog("[NETWORK] ⚠ 截帧失败，无图像可分析，使用 mock 数据");
    }

    // 降级：写死输出
    if (!useRealApi) {
      const result = JSON.stringify({
        scene: "厨房烹饪场景",
        objects: ["炒锅", "炉灶", "食材", "砧板"],
        hazard_detected: false,
        hazard_types: [],
        description: "画面显示灶台上正在煮汤，锅内食材翻滚正常，无明显烟雾或异常情况。建议继续关注火候。",
        severity: "low",
        confidence: 0.96,
      }, null, 2);

      typewrite(result, 15, () => {
        setVlmOutput(result);
        setAnalyzing(false);
        addLog("[VLM] ✅ analysis complete (mock): normal cooking scene, no hazards");
      });
    }
  };

  // ===== 风险预警 =====
  const handleDanger = () => {
    setAlarm(true);
    setVlmOutput("");
    setVlmTyping("");

    // 截帧
    captureFrame();

    const result = JSON.stringify({
      scene: "厨房烹饪场景",
      objects: ["炒锅", "炉灶", "油烟"],
      hazard_detected: true,
      hazard_types: ["烟雾"],
      description: "⚠️ 检测到明显油烟！锅内温度过高，建议立即开启排气扇并调小火力。",
      severity: "medium",
      confidence: 0.87,
    }, null, 2);

    typewrite(result, 15, () => {
      setVlmOutput(result);
      addLog("[ALERT] ⚠ hazard detected: 烟雾 (severity: medium)");
      addLog("[ALERT] 30s cooldown activated, suppressing duplicate alerts");
      addLog("[ALERT] text notification sent to frontend");
    });

    addLog("[VLM] analyzing scene for hazards...");
    setTimeout(() => {
      addLog("[VLM] structural analysis: smoke detected via keyword fallback");
    }, 800);
  };

  // ===== 定时器 =====
  const handleCreateTimer = (name: string, sec: number) => {
    if (timers.find(t => t.name === name)) return;
    addLog(`[TIMER] creating timer: "${name}" ${sec}s`);
    setTimers(prev => [...prev, { name, remaining: sec, total: sec }]);
    const id = window.setInterval(() => {
      setTimers(prev => prev.map(t => t.name === name ? { ...t, remaining: Math.max(0, t.remaining - 1) } : t));
    }, 1000);
    timerRefs.current[name] = id;
    addLog(`[TIMER] ✅ "${name}" created, running in background`);
  };

  const handleDelTimer = (name: string) => {
    if (timerRefs.current[name]) { clearInterval(timerRefs.current[name]); delete timerRefs.current[name]; }
    setTimers(prev => prev.filter(t => t.name !== name));
    addLog(`[TIMER] ✅ "${name}" cancelled`);
  };

  const handleClearTimers = () => {
    Object.values(timerRefs.current).forEach(clearInterval);
    timerRefs.current = {};
    setTimers([]);
    addLog("[TIMER] ✅ all timers cleared");
  };

  return (
    <div style={page}>
      <StatusBar title="智能监控中心 · 视觉AI与定时器" onBack={() => window.location.hash = "#/demo"} />
      <div style={header}>
        <div style={{ fontSize: "13px", opacity: 0.8 }}>图像采集 / VLM分析 / 风险预警 / 智能定时器</div>
        <div style={{ fontSize: "20px", fontWeight: 700, marginTop: "2px" }}>AI 视觉监控系统</div>
      </div>

      <div style={{ flex: 1, display: "flex", gap: "12px", padding: "12px", overflow: "auto", minHeight: 0 }}>
        {/* 左侧主面板 */}
        <div style={{ flex: "1 1 500px" }}>
          {/* 摄像头画面 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontWeight: 600, fontSize: "13px" }}>📷 实时画面</span>
              <span style={{ fontSize: "11px", color: theme.textMuted }}>帧率: 0.5fps · 分辨率: 1280×720</span>
            </div>
            <div style={camFrame}>
              {/* 隐藏的 <video> 用于摄像头采集 */}
              <video ref={videoRef} style={{ display: "none" }} playsInline muted />
              {/* 截图画布 */}
              {snapshot ? (
                <img src={snapshot} alt="capture" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
              ) : null}
              {/* 摄像头实时画面 */}
              {cameraActive && !snapshot && (
                <video autoPlay playsInline muted style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
                  ref={el => { if (el && streamRef.current) el.srcObject = streamRef.current; }} />
              )}
              {/* 降级提示 */}
              {!cameraActive && !snapshot && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: "4px" }}>
                  <div style={{ fontSize: "36px", opacity: 0.3 }}>📷</div>
                  <div style={{ fontSize: "12px", color: "#666" }}>{cameraError ? "摄像头不可用，模拟模式" : "正在启动摄像头..."}</div>
                </div>
              )}
              {/* 操作按钮覆盖层 */}
              <div style={{ position: "absolute", bottom: "12px", left: "50%", transform: "translateX(-50%)", display: "flex", gap: "10px", zIndex: 2 }}>
                <button style={{ background: "rgba(0,0,0,0.6)", color: "#fff", border: "1px solid rgba(255,255,255,0.2)", padding: "8px 20px", borderRadius: "6px", cursor: "pointer", fontWeight: 600, fontSize: "13px", opacity: analyzing ? 0.5 : 1 }} onClick={handleAnalyze} disabled={analyzing}>
                  {analyzing ? "分析中..." : "开始视觉分析"}
                </button>
                <button style={{ background: "rgba(192,57,43,0.8)", color: "#fff", border: "none", padding: "8px 20px", borderRadius: "6px", cursor: "pointer", fontWeight: 600, fontSize: "13px" }} onClick={handleDanger}>
                  模拟风险场景
                </button>
              </div>
              {/* 扫描线动画 */}
              <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "2px", background: "rgba(0,200,100,0.3)", animation: "scan 3s linear infinite", zIndex: 1 }} />
            </div>
          </div>

          {/* VLM 分析结果 */}
          <div style={card}>
            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>
              🔬 VLM 分析结果 {alarm && <span style={{ color: "#e74c3c", fontWeight: 700 }}> ⚠ 风险预警</span>}
            </div>
            <div style={{
              background: alarm ? "#1a0a0a" : "#0a0a1a",
              borderRadius: "8px", padding: "12px", minHeight: "80px",
              fontFamily: "monospace", fontSize: "12px", whiteSpace: "pre-wrap",
              border: `1px solid ${alarm ? "#c0392b" : theme.borderLight}`,
              color: alarm ? "#e8a0a0" : "#a0e0a0",
            }}>
              {vlmTyping && <>{vlmTyping}<span style={{ animation: "blink 0.8s infinite" }}>▌</span></>}
              {vlmOutput && !vlmTyping && vlmOutput}
              {!vlmOutput && !vlmTyping && <span style={{ color: "#555" }}>等待分析指令...</span>}
            </div>
          </div>
        </div>

        {/* 右侧面板 */}
        <div style={{ flex: "0 0 360px", display: "flex", flexDirection: "column", gap: "12px" }}>
          {/* 定时器 */}
          <div style={card}>
            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "10px" }}>⏱ 智能定时器</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "10px" }}>
              <button style={{ background: theme.success, color: "#fff", border: "none", padding: "6px 14px", borderRadius: "6px", cursor: "pointer", fontSize: "12px", fontWeight: 600 }} onClick={() => handleCreateTimer("焯水", 180)}>+ 焯水 3min</button>
              <button style={{ background: theme.success, color: "#fff", border: "none", padding: "6px 14px", borderRadius: "6px", cursor: "pointer", fontSize: "12px", fontWeight: 600 }} onClick={() => handleCreateTimer("焖煮", 300)}>+ 焖煮 5min</button>
              <button style={{ background: theme.success, color: "#fff", border: "none", padding: "6px 14px", borderRadius: "6px", cursor: "pointer", fontSize: "12px", fontWeight: 600 }} onClick={() => handleCreateTimer("收汁", 600)}>+ 收汁 10min</button>
            </div>
            {timers.length === 0 ? (
              <div style={{ fontSize: "12px", color: theme.textMuted, textAlign: "center", padding: "16px" }}>暂无计时任务，点击上方按钮创建</div>
            ) : (
              <>
                {timers.map(t => {
                  const pct = (t.remaining / t.total) * 100;
                  const isLow = t.remaining <= 10;
                  return (
                    <div key={t.name} style={timerCard}>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: "13px" }}>{t.name}</div>
                        <div style={{ width: "100px", height: "4px", borderRadius: "2px", background: theme.bgTertiary, marginTop: "4px", overflow: "hidden" }}>
                          <div style={{ height: "100%", borderRadius: "2px", background: isLow ? "#e74c3c" : theme.primary, width: `${pct}%`, transition: "width 1s linear" }} />
                        </div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ fontFamily: "monospace", fontSize: "18px", fontWeight: 700, color: isLow ? "#e74c3c" : theme.primary }}>
                          {Math.floor(t.remaining / 60)}:{String(t.remaining % 60).padStart(2, "0")}
                        </span>
                        <button style={{ background: "transparent", border: `1px solid ${theme.borderLight}`, color: theme.textMuted, cursor: "pointer", borderRadius: "4px", padding: "2px 8px", fontSize: "11px" }} onClick={() => handleDelTimer(t.name)}>✕</button>
                      </div>
                    </div>
                  );
                })}
                <button style={{ marginTop: "6px", background: "transparent", border: `1px solid ${theme.error}`, color: theme.error, padding: "6px 14px", borderRadius: "6px", cursor: "pointer", fontSize: "12px", width: "100%" }} onClick={handleClearTimers}>清空所有计时</button>
              </>
            )}
          </div>

          {/* 系统日志 */}
          <div style={{ ...card, flex: 1, display: "flex", flexDirection: "column" }}>
            <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>📋 系统日志</div>
            <div style={{ flex: 1, overflow: "auto", maxHeight: "300px" }}>
              {logs.map((line, i) => (
                <div key={i} style={logLine}>{line}</div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}