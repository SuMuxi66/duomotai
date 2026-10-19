import { useState, useCallback, useRef, useEffect } from "react";
import { useTheme } from "../contexts/ThemeContext";

// ===== 写死数据 =====
const RECIPES = [
  { name: "土豆炖牛肉", category: "炖菜", match: ["土豆", "牛肉", "胡萝卜"], score: 100, time: "55分钟", difficulty: "中级", img: "🍲" },
  { name: "番茄炒鸡蛋", category: "炒菜", match: ["番茄", "鸡蛋"], score: 80, time: "10分钟", difficulty: "初级", img: "🍳" },
  { name: "酸辣土豆丝", category: "炒菜", match: ["土豆", "青椒"], score: 60, time: "15分钟", difficulty: "初级", img: "🥗" },
  { name: "土豆鸡蛋饼", category: "面食", match: ["土豆", "鸡蛋", "面粉"], score: 75, time: "20分钟", difficulty: "初级", img: "🥞" },
];

const STEPS = [
  { title: "食材准备", desc: "土豆去皮切块，牛肉切块冷水下锅焯水，撇去浮沫捞出备用", time: "5分钟" },
  { title: "爆香翻炒", desc: "热锅冷油，放入葱姜爆香，加入牛肉块翻炒至表面微焦", time: "3分钟" },
  { title: "调味炖煮", desc: "加入生抽2勺、老抽1勺、料酒1勺翻炒均匀，倒入足量开水没过牛肉", time: "2分钟" },
  { title: "小火慢炖", desc: "大火烧开后转小火，盖上锅盖炖煮40分钟至牛肉软烂", time: "40分钟" },
  { title: "加土豆炖", desc: "加入土豆块，继续炖煮15分钟至土豆能用筷子轻松穿透", time: "15分钟" },
  { title: "收汁出锅", desc: "转大火收汁，加盐调味，出锅装盘，撒上葱花即可", time: "3分钟" },
];

// ===== 语音波形动画组件 =====
function VoiceWave({ active }: { active: boolean }) {
  const bars = 5;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "3px", height: "24px" }}>
      {Array.from({ length: bars }).map((_, i) => (
        <div key={i} style={{
          width: "4px", borderRadius: "2px",
          height: active ? `${Math.random() * 16 + 8}px` : "4px",
          background: active ? "#27ae60" : "#555",
          transition: "height 0.15s ease",
          animation: active ? `wave 0.${i + 3}s ease-in-out infinite alternate` : "none",
          animationDelay: `${i * 0.1}s`,
        }} />
      ))}
    </div>
  );
}

// ===== 状态栏 =====
function StatusBar({ title, onBack }: { title: string; onBack: () => void }) {
  const { theme } = useTheme();
  const [time, setTime] = useState(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }));
  useEffect(() => { const t = setInterval(() => setTime(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })), 1000); return () => clearInterval(t); }, []);
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 20px", background: theme.bgSecondary, borderBottom: `1px solid ${theme.border}`,
      fontSize: "12px", color: theme.textMuted,
    }}>
      <span style={{ cursor: "pointer", color: theme.primary }} onClick={onBack}>← 返回</span>
      <span style={{ fontWeight: 600, fontSize: "13px" }}>{title}</span>
      <span>{time}</span>
    </div>
  );
}

// ===== 步骤进度条 =====
function StepProgress({ current, total }: { current: number; total: number }) {
  const { theme } = useTheme();
  return (
    <div style={{ display: "flex", gap: "6px", marginBottom: "12px" }}>
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} style={{
          flex: 1, height: "4px", borderRadius: "2px",
          background: i <= current ? theme.primary : theme.bgTertiary,
          transition: "background 0.3s",
        }} />
      ))}
    </div>
  );
}

export function DemoVoice() {
  const { theme } = useTheme();
  const [phase, setPhase] = useState(0); // 0=idle,1=wakeup,2=search,3=recipe,4=guide,5=done
  const [guideStep, setGuideStep] = useState(0);
  const [selectedRecipe, setSelectedRecipe] = useState<typeof RECIPES[0] | null>(null);
  const [transcript, setTranscript] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const autoTimer = useRef<number | null>(null);

  const chatLog = useRef<{ role: "user" | "sys" | "result"; text: string }[]>([
    { role: "sys", text: "系统已就绪，菜谱库已加载 86 条，等待语音指令..." },
  ]);
  const [chat, setChat] = useState(chatLog.current);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chat]);

  const addMsg = useCallback((role: "user" | "sys" | "result", text: string) => {
    chatLog.current = [...chatLog.current, { role, text }];
    setChat(chatLog.current);
  }, []);

  // 清空重置
  const reset = useCallback(() => {
    if (autoTimer.current) clearTimeout(autoTimer.current);
    chatLog.current = [{ role: "sys", text: "系统已就绪，菜谱库已加载 86 条，等待语音指令..." }];
    setChat(chatLog.current);
    setPhase(0);
    setGuideStep(0);
    setSelectedRecipe(null);
    setTranscript("");
  }, []);

  // 启动语音测试
  const startVoiceTest = useCallback(() => {
    reset();
    setPhase(1);
    setTranscript("正在聆听...");

    autoTimer.current = window.setTimeout(() => {
      setTranscript("小芸小芸");
      addMsg("sys", "🎤 端点检测中...");
      autoTimer.current = window.setTimeout(() => {
        addMsg("result", "✅ KWS 命中：「小芸小芸」");
        addMsg("sys", "🔊 唤醒反馈：播放「你好我在！」");
        autoTimer.current = window.setTimeout(() => {
          setTranscript("我用土豆和鸡蛋能做啥？");
          addMsg("user", "我用土豆和鸡蛋能做啥？");
          addMsg("sys", "🔍 正在检索菜谱库，双向匹配食材...");
          autoTimer.current = window.setTimeout(() => {
            setTranscript("");
            addMsg("result", `📋 共找到 ${RECIPES.length} 条相关菜谱（按匹配度排序）`);
            setPhase(2);
          }, 1500);
        }, 1500);
      }, 1200);
    }, 800);
  }, [reset, addMsg]);

  // 选择菜谱
  const selectRecipe = useCallback((r: typeof RECIPES[0]) => {
    if (autoTimer.current) clearTimeout(autoTimer.current);
    setSelectedRecipe(r);
    setGuideStep(0);
    setPhase(3);
    addMsg("sys", `✅ 已选择「${r.name}」，正在加载 ${STEPS.length} 步烹饪步骤...`);
    autoTimer.current = window.setTimeout(() => {
      setPhase(4);
      addMsg("result", `🍳 开始烹饪：${r.name}`);
      startGuide();
    }, 1000);
  }, [addMsg]);

  // 步骤引导
  const startGuide = useCallback(() => {
    let i = 0;
    const step = () => {
      if (i >= STEPS.length) {
        addMsg("result", "✅ 所有烹饪步骤已完成！请享用 🎉");
        setPhase(5);
        return;
      }
      setGuideStep(i);
      addMsg("result", `📝 步骤 ${i + 1}/${STEPS.length}：${STEPS[i].title}`);
      addMsg("sys", `${STEPS[i].desc}`);
      i++;
      autoTimer.current = window.setTimeout(step, 3500);
    };
    step();
  }, [addMsg]);

  // 语音状态指示
  const voiceStatus = phase === 1 ? "聆听中" : phase === 0 ? "待机" : "已完成";
  const voiceColor = phase === 1 ? "#27ae60" : phase === 0 ? theme.textMuted : theme.primary;

  return (
    <div style={{ height: "100vh", background: theme.bg, color: theme.text, fontFamily: "'Inter', sans-serif", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <StatusBar title="智能语音助手 · 语音交互模块" onBack={() => window.location.hash = "#/demo"} />
      <div style={{ background: `linear-gradient(135deg, ${theme.primary}, ${theme.secondary})`, color: "#fff", padding: "16px 24px" }}>
        <div style={{ fontSize: "13px", opacity: 0.8 }}>语音唤醒 / 菜谱检索 / 步骤引导</div>
        <div style={{ fontSize: "20px", fontWeight: 700, marginTop: "2px" }}>智能烹饪助手</div>
      </div>

      {/* 语音状态指示条 */}
      <div style={{
        display: "flex", alignItems: "center", gap: "12px",
        padding: "10px 24px", background: theme.bgSecondary,
        borderBottom: `1px solid ${theme.border}`,
      }}>
        <div style={{
          width: "10px", height: "10px", borderRadius: "50%",
          background: voiceColor, boxShadow: phase === 1 ? `0 0 8px ${voiceColor}` : "none",
          transition: "all 0.3s",
        }} />
        <span style={{ fontSize: "13px", fontWeight: 600, color: voiceColor }}>
          {voiceStatus}
        </span>
        <VoiceWave active={phase === 1} />
        {transcript && (
          <span style={{ fontSize: "13px", color: theme.textMuted, fontStyle: "italic" }}>
            "{transcript}"
          </span>
        )}
      </div>

      <div style={{ flex: 1, display: "flex", overflow: "hidden", minHeight: 0 }}>
        {/* 左侧：聊天面板 */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div style={{ flex: 1, overflow: "auto", padding: "16px" }}>
            {chat.map((m, i) => (
              <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", marginBottom: "8px" }}>
                {m.role === "user" ? (
                  <div style={{
                    padding: "10px 16px", borderRadius: "16px 16px 4px 16px", fontSize: "14px", lineHeight: 1.6,
                    maxWidth: "75%", background: theme.primary, color: "#fff",
                  }}>
                    <span style={{ fontSize: "12px", opacity: 0.6, marginRight: "6px" }}>🎤</span>{m.text}
                  </div>
                ) : m.role === "result" ? (
                  <div style={{
                    padding: "10px 16px", borderRadius: "16px", fontSize: "14px", lineHeight: 1.6,
                    maxWidth: "85%", background: theme.bgTertiary, color: theme.primary,
                    border: `1px solid ${theme.primary}33`, fontWeight: 500,
                  }}>
                    {m.text}
                  </div>
                ) : (
                  <div style={{
                    padding: "10px 16px", borderRadius: "16px 16px 16px 4px", fontSize: "14px", lineHeight: 1.6,
                    maxWidth: "75%", background: theme.bgTertiary, color: theme.text,
                  }}>
                    {m.text}
                  </div>
                )}
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          {/* 底部操作区 */}
          <div style={{ padding: "16px", borderTop: `1px solid ${theme.border}`, textAlign: "center" }}>
            {phase === 0 || phase === 5 ? (
              <button
                onClick={startVoiceTest}
                style={{
                  display: "inline-flex", alignItems: "center", gap: "10px",
                  background: `linear-gradient(135deg, ${theme.primary}, ${theme.secondary})`,
                  color: "#fff", border: "none", padding: "14px 32px",
                  borderRadius: "50px", cursor: "pointer", fontWeight: 600, fontSize: "15px",
                  boxShadow: `0 4px 20px ${theme.primaryGlow}`,
                  transition: "0.3s",
                }}
              >
                <span style={{ fontSize: "20px" }}>🎤</span>
                {phase === 0 ? "开始语音交互演示" : "重新测试"}
              </button>
            ) : phase === 2 ? (
              <div style={{ fontSize: "14px", color: theme.textMuted }}>
                请从右侧菜谱列表中选择一道菜开始烹饪
              </div>
            ) : (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px" }}>
                <VoiceWave active={true} />
                <span style={{ fontSize: "13px", color: theme.textMuted }}>
                  {phase === 1 ? "语音交互进行中..." : "烹饪模式运行中..."}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* 右侧：内容面板 */}
        <div style={{
          width: "380px", borderLeft: `1px solid ${theme.border}`,
          display: "flex", flexDirection: "column", background: theme.bgSecondary,
        }}>
          <div style={{ padding: "14px 16px", borderBottom: `1px solid ${theme.border}`, fontWeight: 600, fontSize: "14px" }}>
            {phase === 0 ? "📋 菜谱推荐" : phase === 2 ? "📋 检索结果" : phase >= 3 ? "🍳 烹饪引导" : "📋 菜谱推荐"}
          </div>

          <div style={{ flex: 1, overflow: "auto", padding: "12px" }}>
            {/* 空闲状态 */}
            {phase === 0 && (
              <div style={{ textAlign: "center", padding: "40px 0", color: theme.textMuted, fontSize: "13px" }}>
                <div style={{ fontSize: "48px", marginBottom: "12px", opacity: 0.3 }}>🎤</div>
                <div>点击下方按钮开始语音交互演示</div>
                <div style={{ marginTop: "8px", fontSize: "12px", color: theme.textMuted }}>
                  将模拟：唤醒 → 语音识别 → 菜谱检索 → 步骤引导
                </div>
              </div>
            )}

            {/* 检索结果 / 菜谱列表 */}
            {(phase === 2 || phase === 1) && (
              <div>
                {RECIPES.map((r, i) => (
                  <div
                    key={i}
                    onClick={() => phase === 2 ? selectRecipe(r) : undefined}
                    style={{
                      padding: "14px", borderRadius: "10px", marginBottom: "8px",
                      background: theme.bg, border: `1px solid ${theme.borderLight}`,
                      cursor: phase === 2 ? "pointer" : "default",
                      transition: "0.2s", position: "relative", overflow: "hidden",
                    }}
                    onMouseEnter={e => { if (phase === 2) { (e.currentTarget as HTMLElement).style.borderColor = theme.primary; (e.currentTarget as HTMLElement).style.transform = "translateY(-1px)"; } }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = theme.borderLight; (e.currentTarget as HTMLElement).style.transform = "none"; }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                      <span style={{ fontSize: "28px" }}>{r.img}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <span style={{ fontWeight: 600, fontSize: "15px" }}>{r.name}</span>
                          <span style={{ fontSize: "12px", fontWeight: 700, color: theme.primary }}>{r.score}分</span>
                        </div>
                        <div style={{ fontSize: "12px", color: theme.textMuted, marginTop: "2px" }}>
                          {r.category} · {r.time} · {r.difficulty}
                        </div>
                        <div style={{ marginTop: "6px", display: "flex", gap: "4px", flexWrap: "wrap" }}>
                          {r.match.map((t, j) => (
                            <span key={j} style={{
                              padding: "2px 8px", borderRadius: "4px", fontSize: "11px",
                              background: theme.primary + "22", color: theme.primary, fontWeight: 500,
                            }}>{t}</span>
                          ))}
                        </div>
                      </div>
                    </div>
                    {phase === 2 && (
                      <div style={{ position: "absolute", bottom: "8px", right: "12px", fontSize: "11px", color: theme.primary, opacity: 0.6 }}>
                        点击选择 →
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 步骤引导面板 */}
            {phase >= 3 && selectedRecipe && (
              <div>
                {/* 菜谱概览 */}
                <div style={{
                  padding: "16px", borderRadius: "10px", marginBottom: "12px",
                  background: `linear-gradient(135deg, ${theme.primary}22, ${theme.secondary}22)`,
                  border: `1px solid ${theme.primary}33`,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span style={{ fontSize: "32px" }}>{selectedRecipe.img}</span>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: "16px" }}>{selectedRecipe.name}</div>
                      <div style={{ fontSize: "12px", color: theme.textMuted }}>{selectedRecipe.category} · 共 {STEPS.length} 步</div>
                    </div>
                  </div>
                </div>

                {/* 进度条 */}
                <StepProgress current={phase === 3 ? -1 : guideStep} total={STEPS.length} />

                {/* 当前步骤 */}
                {phase === 4 && (
                  <div style={{
                    padding: "16px", borderRadius: "10px", marginBottom: "12px",
                    background: theme.bg, border: `2px solid ${theme.primary}`,
                    position: "relative",
                  }}>
                    <div style={{ position: "absolute", top: "-10px", left: "12px", padding: "0 6px", background: theme.bg, fontSize: "11px", color: theme.primary, fontWeight: 600 }}>
                      当前步骤 · {guideStep + 1}/{STEPS.length}
                    </div>
                    <div style={{ marginTop: "4px" }}>
                      <div style={{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>
                        {STEPS[guideStep].title}
                      </div>
                      <div style={{ fontSize: "14px", color: theme.text, lineHeight: 1.6, opacity: 0.9 }}>
                        {STEPS[guideStep].desc}
                      </div>
                      <div style={{ marginTop: "8px", display: "flex", gap: "8px" }}>
                        <span style={{ fontSize: "12px", color: theme.textMuted }}>⏱ {STEPS[guideStep].time}</span>
                      </div>
                    </div>
                  </div>
                )}

                {/* 步骤列表 */}
                <div style={{ fontSize: "12px", color: theme.textMuted, marginBottom: "8px", fontWeight: 600 }}>
                  全部步骤概览
                </div>
                {STEPS.map((s, i) => (
                  <div key={i} style={{
                    padding: "10px 12px", marginBottom: "4px", borderRadius: "8px",
                    background: i === guideStep && phase === 4 ? theme.bg : "transparent",
                    border: `1px solid ${i === guideStep && phase === 4 ? theme.primary + "44" : "transparent"}`,
                    display: "flex", gap: "10px", alignItems: "flex-start",
                    opacity: i < guideStep ? 0.5 : 1,
                  }}>
                    <div style={{
                      width: "24px", height: "24px", borderRadius: "50%", display: "flex",
                      alignItems: "center", justifyContent: "center", flexShrink: 0,
                      background: i < guideStep ? theme.success : i === guideStep && phase === 4 ? theme.primary : theme.bgTertiary,
                      color: i < guideStep || i === guideStep ? "#fff" : theme.textMuted,
                      fontSize: "12px", fontWeight: 700,
                    }}>
                      {i < guideStep ? "✓" : i + 1}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: "13px" }}>{s.title}</div>
                      <div style={{ fontSize: "12px", color: theme.textMuted, marginTop: "2px" }}>⏱ {s.time}</div>
                    </div>
                  </div>
                ))}

                {/* 完成状态 */}
                {phase === 5 && (
                  <div style={{ textAlign: "center", padding: "20px", marginTop: "12px" }}>
                    <div style={{ fontSize: "48px", marginBottom: "8px" }}>🎉</div>
                    <div style={{ fontWeight: 700, fontSize: "16px", color: theme.success }}>烹饪完成！</div>
                    <div style={{ fontSize: "13px", color: theme.textMuted, marginTop: "4px" }}>
                      所有步骤已执行完毕，请享用
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}