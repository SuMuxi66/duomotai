import { useEffect, useState } from "react";
import { useTheme } from "../contexts/ThemeContext";
import { DemoVoice } from "./DemoVoice";
import { DemoVision } from "./DemoVision";
import { DemoHardware } from "./DemoHardware";
import { DemoMonitor } from "./DemoMonitor";

function DemoHome() {
  const { theme } = useTheme();
  const container: React.CSSProperties = {
    minHeight: "100vh", background: theme.bg, color: theme.text, padding: "40px", fontFamily: "'Inter', sans-serif",
  };
  const card: React.CSSProperties = {
    background: theme.bgSecondary, borderRadius: "12px", padding: "24px", marginBottom: "16px",
    cursor: "pointer", border: `1px solid ${theme.border}`, transition: "0.2s",
  };
  const title: React.CSSProperties = { fontSize: "28px", fontWeight: 700, marginBottom: "8px" };
  const sub: React.CSSProperties = { fontSize: "14px", color: theme.textMuted, marginBottom: "32px" };

  return (
    <div style={container}>
      <div style={{ maxWidth: "700px", margin: "0 auto" }}>
        <div style={title}>智能烹饪系统 · 分模块验收测试</div>
        <div style={sub}>选择模块进入对应的实操测试页面</div>
        <div style={card} onClick={() => window.location.hash = "#/demo/voice"}>
          <div style={{ fontSize: "18px", fontWeight: 600 }}>模块一：语料处理与语音交互</div>
          <div style={{ fontSize: "13px", color: theme.textMuted, marginTop: "4px" }}>
            菜谱检索 · 步骤引导 · 语音唤醒/打断/流式识别
          </div>
        </div>
        <div style={card} onClick={() => window.location.hash = "#/demo/vision"}>
          <div style={{ fontSize: "18px", fontWeight: 600 }}>模块二：视觉AI与智能定时器</div>
          <div style={{ fontSize: "13px", color: theme.textMuted, marginTop: "4px" }}>
            图像采集 · VLM分析 · 风险预警 · 口语化定时
          </div>
        </div>
        <div style={card} onClick={() => window.location.hash = "#/demo/hardware"}>
          <div style={{ fontSize: "18px", fontWeight: 600 }}>模块三：硬件联动与红外测温</div>
          <div style={{ fontSize: "13px", color: theme.textMuted, marginTop: "4px" }}>
            排气扇控制 · 状态回读 · MLX90640红外测温
          </div>
        </div>
        <div style={{ textAlign: "center", marginTop: "48px" }}>
          <a href="/" style={{ color: theme.primary, fontSize: "14px" }}>← 返回主系统</a>
        </div>
      </div>
    </div>
  );
}

export function DemoRouter() {
  const [page, setPage] = useState<string>("home");

  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.replace(/^#/, "");
      if (h.startsWith("/demo/")) {
        const p = h.replace("/demo/", "").split("?")[0];
        setPage(p);
      } else {
        setPage("home");
      }
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  switch (page) {
    case "voice":    return <DemoVoice />;
    case "vision":   return <DemoVision />;
    case "hardware": return <DemoHardware />;
    case "monitor":  return <DemoMonitor />;
    default:         return <DemoHome />;
  }
}