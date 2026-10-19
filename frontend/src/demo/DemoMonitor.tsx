import { useState, useCallback, useEffect, useRef } from "react";
import { useTheme } from "../contexts/ThemeContext";

const S = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20 };

// ===== 动态视觉模型（与设置页/实际请求同源：localStorage 持久化，后端 config 推送写同一 key）=====
const VLM_PROVIDER_KEY = "multimodal_vlm_provider";
const VLM_MODEL_KEY = "multimodal_vlm_model";
const VLM_DEFAULTS = { provider: "modelscope", model: "Qwen/Qwen3.5-27B" };

function readPersisted(key: string, fallback: string): string {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "null");
    if (typeof raw === "string" && raw) return raw;
  } catch {
    /* 忽略损坏数据，使用默认值 */
  }
  return fallback;
}

function readVlmLabel(): string {
  const provider = readPersisted(VLM_PROVIDER_KEY, VLM_DEFAULTS.provider);
  const model = readPersisted(VLM_MODEL_KEY, VLM_DEFAULTS.model);
  return `${provider} / ${model}`;
}

// ===== 侧边栏步骤定义 =====
interface TestStep {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
}

const TEST_STEPS: TestStep[] = [
  { id: "step-guide", title: "步骤式引导测试", subtitle: "Cooking Step Guide", icon: "1" },
  { id: "voice", title: "语言对话测试", subtitle: "Voice Conversation", icon: "2" },
  { id: "vlm", title: "VLM 视觉测试", subtitle: "Vision Analysis", icon: "3" },
  { id: "anomaly", title: "异常检测测试", subtitle: "Anomaly Detection", icon: "4" },
  { id: "device", title: "设备状态", subtitle: "Device Status", icon: "5" },
];

// ===== 步骤数据 =====
const COOKING_STEPS = [
  { step: 1, title: "准备食材与厨具", desc: "将番茄、鸡蛋、大蒜准备好，清洗切配", status: "done" },
  { step: 2, title: "热油爆香", desc: "锅中倒入食用油，加热至七成热，放入蒜末爆香", status: "active" },
  { step: 3, title: "下锅翻炒", desc: "倒入番茄块，中火翻炒出汁", status: "pending" },
  { step: 4, title: "调味收汁", desc: "加入鸡蛋液，撒盐、糖调味，大火收汁", status: "pending" },
  { step: 5, title: "装盘点缀", desc: "盛入盘中，撒上葱花点缀", status: "pending" },
];

const VOICE_LOGS = [
  { time: "14:23:01", type: "user", text: "小云，帮我看看锅里的水开了没有" },
  { time: "14:23:03", type: "system", text: "正在分析画面…", color: "#f59e0b" },
  { time: "14:23:05", type: "ai", text: "检测到锅中有水，水面有轻微波动，尚未沸腾。建议继续保持中火加热。" },
  { time: "14:23:12", type: "user", text: "现在转到小火再煮三分钟" },
  { time: "14:23:14", type: "system", text: "已调节火力至小火，已设置3分钟定时器", color: "#f59e0b" },
  { time: "14:23:16", type: "ai", text: "好的，已为您调节为小火并设置3分钟倒计时，到时会自动提醒您。" },
  { time: "14:23:25", type: "user", text: "下一步该做什么" },
  { time: "14:23:27", type: "ai", text: "当前已完成热油爆香步骤，下一步是下锅翻炒。您可以将准备好的番茄块倒入锅中，中火翻炒至出汁。" },
];

const VLM_RESULTS = [
  { label: "场景识别", value: "厨房烹饪场景", status: "ok" },
  { label: "物体检测", value: "炒锅 · 食材 · 锅铲 · 灶台", status: "ok" },
  { label: "动作识别", value: "翻炒中", status: "ok" },
  { label: "危险检测", value: "未检测到异常", status: "ok" },
  { label: "置信度", value: "96.2%", status: "ok" },
];

const ANOMALY_RESULTS = [
  { label: "烟雾检测", value: "未检测到", status: "ok" },
  { label: "火焰检测", value: "未检测到", status: "ok" },
  { label: "溢锅检测", value: "未检测到", status: "ok" },
  { label: "锅盖跳动", value: "未检测到", status: "ok" },
  { label: "燃气泄漏", value: "未检测到", status: "ok" },
  { label: "综合评估", value: "安全", status: "ok" },
];

const DEVICE_STATUS = [
  { label: "排气扇", status: "ON", color: "#10b981", detail: "运行中 · 风速 80%" },
  { label: "温度传感器", status: "ON", color: "#10b981", detail: "142.5°C" },
  { label: "重量传感器", status: "ON", color: "#10b981", detail: "360g" },
  { label: "燃气报警", status: "OFF", color: "#94a3b8", detail: "正常" },
  { label: "摄像头", status: "ON", color: "#10b981", detail: "1280×720 · 30fps" },
  { label: "麦克风", status: "ON", color: "#10b981", detail: "采样率 16kHz" },
];

// ===== 卡片组件 =====
function Card({ title, children, theme, style }: { title: string; children: React.ReactNode; theme: any; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: theme.bgSecondary, border: `1px solid ${theme.border}`,
      borderRadius: 12, overflow: "hidden",
      boxShadow: `0 4px 18px rgba(0,0,0,${theme.mode === "dark" ? 0.35 : 0.06})`,
      ...style,
    }}>
      <div style={{
        height: 2, background: `linear-gradient(90deg, ${theme.primary}, transparent 90%)`,
        boxShadow: `0 0 12px ${theme.primary}`, flexShrink: 0,
      }} />
      <div style={{ padding: `${S.sm}px ${S.lg}px ${S.xs}px` }}>
        <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: 1.5, textTransform: "uppercase", color: theme.textSecondary }}>
          {title}
        </span>
      </div>
      <div style={{ padding: `${S.sm}px ${S.lg}px ${S.lg}px` }}>
        {children}
      </div>
    </div>
  );
}

// ===== 步骤引导面板 =====
function StepGuidePanel({ theme }: { theme: any }) {
  return (
    <Card title="番茄炒蛋 · 步骤引导" theme={theme}>
      <div style={{ display: "flex", flexDirection: "column", gap: S.sm }}>
        {COOKING_STEPS.map(s => {
          const colors = {
            done: { bg: "#10b98122", border: "#10b98155", text: "#10b981", icon: "✓" },
            active: { bg: `${theme.primary}22`, border: `${theme.primary}55`, text: theme.primary, icon: "▶" },
            pending: { bg: theme.bgTertiary, border: theme.borderLight, text: theme.textMuted, icon: "○" },
          };
          const c = colors[s.status as keyof typeof colors];
          return (
            <div key={s.step} style={{
              display: "flex", alignItems: "center", gap: S.md,
              padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              <span style={{
                width: 28, height: 28, borderRadius: "50%",
                background: c.text, color: "#fff",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, fontWeight: 700, flexShrink: 0,
              }}>{c.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>
                  {s.step}. {s.title}
                </div>
                <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 2 }}>{s.desc}</div>
              </div>
              <span style={{ fontSize: 10, color: c.text, fontFamily: "monospace", letterSpacing: 1, flexShrink: 0 }}>
                {s.status === "done" ? "COMPLETED" : s.status === "active" ? "IN PROGRESS" : "PENDING"}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{
        marginTop: S.md, padding: `${S.sm}px ${S.md}px`,
        background: `${theme.primary}11`, border: `1px solid ${theme.primary}33`,
        borderRadius: 8, fontSize: 12, color: theme.primary,
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: theme.primary, boxShadow: `0 0 6px ${theme.primary}`, animation: "blink 1.4s ease-in-out infinite" }} />
        当前步骤：热油爆香 · 预计剩余 2 步 · 总耗时约 8 分钟
      </div>
    </Card>
  );
}

// ===== 语音对话面板 =====
function VoicePanel({ theme }: { theme: any }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  return (
    <Card title="语音对话记录" theme={theme}>
      <div ref={scrollRef} style={{
        display: "flex", flexDirection: "column", gap: S.sm,
        maxHeight: 380, overflowY: "auto", paddingRight: 4,
      }}>
        {VOICE_LOGS.map((log, i) => {
          const isUser = log.type === "user";
          const isSystem = log.type === "system";
          return (
            <div key={i} style={{
              display: "flex", gap: S.sm, alignItems: "flex-start",
              flexDirection: isUser ? "row-reverse" : "row",
            }}>
              <div style={{
                width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                background: isUser ? theme.primary : isSystem ? "#f59e0b33" : theme.secondary,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 11, fontWeight: 700, color: isUser ? "#fff" : "#f59e0b",
              }}>
                {isUser ? "U" : isSystem ? "S" : "AI"}
              </div>
              <div style={{
                padding: `${S.sm}px ${S.md}px`, borderRadius: 10,
                background: isUser ? theme.primary : isSystem ? "#f59e0b11" : theme.bgTertiary,
                border: `1px solid ${isUser ? theme.primary : isSystem ? "#f59e0b33" : theme.borderLight}`,
                color: isUser ? "#fff" : isSystem ? "#f59e0b" : theme.text,
                fontSize: 13, lineHeight: 1.6,
                maxWidth: "80%",
              }}>
                {log.text}
              </div>
              <span style={{ fontSize: 10, color: theme.textMuted, fontFamily: "monospace", flexShrink: 0, paddingTop: 4 }}>
                {log.time}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{
        marginTop: S.md, padding: `${S.sm}px ${S.md}px`,
        background: theme.bgTertiary, border: `1px solid ${theme.borderLight}`,
        borderRadius: 8, fontSize: 11, color: theme.textMuted,
        display: "flex", justifyContent: "space-between",
      }}>
        <span>对话轮次：4</span>
        <span>ASR 准确率：97.3%</span>
        <span>响应延迟：320ms</span>
      </div>
    </Card>
  );
}

// ===== VLM 视觉面板 =====
function VlmPanel({ theme }: { theme: any }) {
  const [modelLabel, setModelLabel] = useState(() => readVlmLabel());

  // 设置保存 / 其它标签页修改后即时同步显示
  useEffect(() => {
    const timer = window.setInterval(() => {
      setModelLabel((prev) => {
        const next = readVlmLabel();
        return next === prev ? prev : next;
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <Card title="VLM 视觉分析结果" theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: S.sm }}>
        {VLM_RESULTS.map((item, i) => (
          <div key={i} style={{
            display: "flex", flexDirection: "column", gap: 4,
            padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
            background: theme.bgTertiary, border: `1px solid ${theme.borderLight}`,
          }}>
            <span style={{ fontSize: 10, color: theme.textMuted, letterSpacing: 1 }}>{item.label}</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>{item.value}</span>
            <span style={{
              fontSize: 10, color: "#10b981",
              display: "inline-flex", alignItems: "center", gap: 4,
            }}>
              <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#10b981" }} />
              PASS
            </span>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: S.md, padding: `${S.sm}px ${S.md}px`,
        background: `${theme.primary}11`, border: `1px solid ${theme.primary}33`,
        borderRadius: 8, fontSize: 12, color: theme.primary,
      }}>
        <span style={{ fontWeight: 600 }}>模型：</span>{modelLabel} &nbsp;|&nbsp;
        <span style={{ fontWeight: 600 }}>推理耗时：</span>186ms &nbsp;|&nbsp;
        <span style={{ fontWeight: 600 }}>输入分辨率：</span>1280×720
      </div>
    </Card>
  );
}

// ===== 异常检测面板 =====
function AnomalyPanel({ theme }: { theme: any }) {
  return (
    <Card title="异常检测扫描结果" theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: S.sm }}>
        {ANOMALY_RESULTS.map((item, i) => (
          <div key={i} style={{
            display: "flex", flexDirection: "column", gap: 4,
            padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
            background: "#10b98111", border: `1px solid #10b98133`,
          }}>
            <span style={{ fontSize: 10, color: theme.textMuted, letterSpacing: 1 }}>{item.label}</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: "#10b981" }}>{item.value}</span>
            <span style={{
              fontSize: 10, color: "#10b981",
              display: "inline-flex", alignItems: "center", gap: 4,
            }}>
              <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#10b981" }} />
              SAFE
            </span>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: S.md, padding: `${S.sm}px ${S.md}px`,
        background: "#10b98111", border: `1px solid #10b98133`,
        borderRadius: 8, fontSize: 12, color: "#10b981",
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#10b981", boxShadow: "0 0 8px #10b981" }} />
        综合安全评估：当前厨房环境安全，未检测到任何异常。
      </div>
    </Card>
  );
}

// ===== 设备状态面板 =====
function DevicePanel({ theme }: { theme: any }) {
  return (
    <Card title="设备状态总览" theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: S.sm }}>
        {DEVICE_STATUS.map((item, i) => (
          <div key={i} style={{
            display: "flex", flexDirection: "column", gap: 4,
            padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
            background: item.status === "ON" ? `${item.color}11` : theme.bgTertiary,
            border: `1px solid ${item.status === "ON" ? `${item.color}33` : theme.borderLight}`,
          }}>
            <span style={{ fontSize: 10, color: theme.textMuted, letterSpacing: 1 }}>{item.label}</span>
            <span style={{
              fontSize: 13, fontWeight: 700, color: item.color,
              fontFamily: "monospace", letterSpacing: 1,
            }}>{item.status}</span>
            <span style={{ fontSize: 11, color: theme.textSecondary }}>{item.detail}</span>
          </div>
        ))}
      </div>
      <div style={{
        marginTop: S.md, padding: `${S.sm}px ${S.md}px`,
        background: theme.bgTertiary, border: `1px solid ${theme.borderLight}`,
        borderRadius: 8, fontSize: 11, color: theme.textMuted,
        display: "flex", justifyContent: "space-between",
      }}>
        <span>在线设备：6/6</span>
        <span>异常设备：0</span>
        <span>最后更新：刚刚</span>
      </div>
    </Card>
  );
}

// ===== 主组件 =====
export function DemoMonitor() {
  const { theme } = useTheme();
  const [activeStep, setActiveStep] = useState("step-guide");
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const handleBack = useCallback(() => {
    window.location.hash = "#/demo";
  }, []);

  const renderContent = () => {
    switch (activeStep) {
      case "step-guide": return <StepGuidePanel theme={theme} />;
      case "voice": return <VoicePanel theme={theme} />;
      case "vlm": return <VlmPanel theme={theme} />;
      case "anomaly": return <AnomalyPanel theme={theme} />;
      case "device": return <DevicePanel theme={theme} />;
      default: return null;
    }
  };

  return (
    <div style={{
      height: "100vh", display: "flex", flexDirection: "column",
      background: theme.bg, color: theme.text, overflow: "hidden",
      fontFamily: "'Inter', 'Microsoft YaHei', sans-serif",
    }}>
      {/* 状态栏 */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "8px 20px", background: theme.bgSecondary,
        borderBottom: `1px solid ${theme.border}`, fontSize: 12, color: theme.textMuted,
        flexShrink: 0,
      }}>
        <span style={{ cursor: "pointer", color: theme.primary, fontWeight: 600 }} onClick={handleBack}>← 返回</span>
        <span style={{ fontWeight: 600, fontSize: 13 }}>ROKID · 测试面板</span>
        <span style={{ fontFamily: "monospace" }}>{now.toTimeString().slice(0, 8)}</span>
      </div>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* 侧边栏 */}
        <aside style={{
          width: 240, flexShrink: 0,
          background: theme.bgSecondary,
          borderRight: `1px solid ${theme.border}`,
          padding: `${S.lg}px 0`,
          display: "flex", flexDirection: "column",
          overflowY: "auto",
        }}>
          <div style={{
            padding: `0 ${S.lg}px ${S.lg}px`,
            borderBottom: `1px solid ${theme.borderLight}`,
            marginBottom: S.md,
          }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: theme.text }}>测试项目</div>
            <div style={{ fontSize: 11, color: theme.textMuted, marginTop: 2 }}>按顺序逐项测试</div>
          </div>

          {TEST_STEPS.map((step, idx) => {
            const isActive = activeStep === step.id;
            const isDone = TEST_STEPS.findIndex(s => s.id === activeStep) > idx;
            return (
              <div
                key={step.id}
                onClick={() => setActiveStep(step.id)}
                style={{
                  display: "flex", alignItems: "center", gap: S.sm,
                  padding: `${S.sm}px ${S.lg}px`,
                  cursor: "pointer",
                  background: isActive ? `${theme.primary}15` : "transparent",
                  borderLeft: `3px solid ${isActive ? theme.primary : isDone ? "#10b981" : "transparent"}`,
                  transition: "all 200ms ease",
                }}
              >
                <div style={{
                  width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12, fontWeight: 700,
                  background: isActive ? theme.primary : isDone ? "#10b981" : theme.bgTertiary,
                  color: isActive ? "#fff" : isDone ? "#fff" : theme.textMuted,
                  border: `1px solid ${isActive ? theme.primary : isDone ? "#10b981" : theme.borderLight}`,
                }}>
                  {isDone ? "✓" : step.icon}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 12, fontWeight: isActive ? 600 : 500,
                    color: isActive ? theme.primary : isDone ? "#10b981" : theme.text,
                  }}>{step.title}</div>
                  <div style={{ fontSize: 10, color: theme.textMuted, marginTop: 1, letterSpacing: 0.5 }}>
                    {step.subtitle}
                  </div>
                </div>
              </div>
            );
          })}
        </aside>

        {/* 主内容区 */}
        <main style={{
          flex: 1, minHeight: 0,
          padding: S.xl,
          overflowY: "auto",
          display: "flex", flexDirection: "column",
        }}>
          <div style={{ marginBottom: S.lg }}>
            <div style={{ display: "flex", alignItems: "center", gap: S.md }}>
              <span style={{
                fontSize: 10, color: theme.primary, fontFamily: "monospace",
                padding: "2px 8px", borderRadius: 4, background: `${theme.primary}15`,
                letterSpacing: 1,
              }}>
                TEST · {TEST_STEPS.findIndex(s => s.id === activeStep) + 1}/{TEST_STEPS.length}
              </span>
              <span style={{ fontSize: 18, fontWeight: 700, color: theme.text }}>
                {TEST_STEPS.find(s => s.id === activeStep)?.title}
              </span>
            </div>
            <div style={{ fontSize: 12, color: theme.textMuted, marginTop: 4 }}>
              {TEST_STEPS.find(s => s.id === activeStep)?.subtitle}
            </div>
          </div>

          {renderContent()}
        </main>
      </div>
    </div>
  );
}
