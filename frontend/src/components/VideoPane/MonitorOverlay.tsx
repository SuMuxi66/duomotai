/**
 * MonitorOverlay - 监控数据叠加层
 *
 * 包含温度/重量数据获取逻辑、烹饪倒计时列表。
 * 温度/重量轮询由本组件自行管理。
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import { Timer } from "lucide-react";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";
import type { CookingTimerInfo } from "@/components/ChatPane";

export interface TemperatureData {
  target: number;
  average: number;
  maximum: number;
  minimum: number;
  sensor: number;
  timestamp: string | null;
}

export interface WeightData {
  weight: number;
  unit: string;
  stable: boolean;
  timestamp: string | null;
}

export interface MonitorOverlayProps {
  theme: Theme;
  cookingTimers?: CookingTimerInfo[];
  onCancelCookingTimer?: () => void;
}

function formatRemaining(endTime: number): { timeStr: string; isUrgent: boolean; progress: number; total: number } {
  const remaining = Math.max(0, Math.round(endTime - Date.now() / 1000));
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  const timeStr = mins >= 60
    ? `${Math.floor(mins / 60)}:${String(mins % 60).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return { timeStr, isUrgent: remaining > 0 && remaining <= 30, progress: 0, total: remaining };
}

export const MonitorOverlay: React.FC<MonitorOverlayProps> = ({
  theme,
  cookingTimers,
  onCancelCookingTimer,
}) => {
  const [temperatureData, setTemperatureData] = useState<TemperatureData>({
    target: 0, average: 0, maximum: 0, minimum: 0, sensor: 0, timestamp: null,
  });
  const [weightData, setWeightData] = useState<WeightData>({ weight: 0, unit: "g", stable: true, timestamp: null });
  const [isWeightExpired, setIsWeightExpired] = useState(false);
  const [isTemperatureExpired, setIsTemperatureExpired] = useState(false);
  const [, setTimerTick] = useState(0);

  const lastUpdateTimeRef = useRef(Date.now());
  const lastTempKeyRef = useRef("");
  const lastWeightKeyRef = useRef("");
  const lastWeightUpdateRef = useRef(0);

  // 自动下料控制（下料机接板卡继电器 NC 触点；后端 /dispense/* 按克自动投放）
  const [dispenseG, setDispenseG] = useState("5");
  const [dispenseActive, setDispenseActive] = useState(false);
  const [dispenseRemaining, setDispenseRemaining] = useState(0);
  const [dispenseDoneText, setDispenseDoneText] = useState("");
  const dispensePollRef = useRef<number | null>(null);

  const stopDispensePoll = useCallback(() => {
    if (dispensePollRef.current !== null) {
      clearInterval(dispensePollRef.current);
      dispensePollRef.current = null;
    }
  }, []);

  const startDispensePoll = useCallback(() => {
    stopDispensePoll();
    dispensePollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch("/dispense/status");
        if (!res.ok) return;
        const s = await res.json();
        setDispenseActive(!!s.active);
        setDispenseRemaining(s.remaining || 0);
        if (!s.active && s.finished && s.reason === "completed") {
          setDispenseDoneText(`上次投放 ${s.target_g}g 完成`);
          stopDispensePoll();
        } else if (!s.active) {
          stopDispensePoll();
        }
      } catch {
        /* 下料状态轮询失败：忽略，等下一轮 */
      }
    }, 1000);
  }, [stopDispensePoll]);

  useEffect(() => stopDispensePoll, [stopDispensePoll]);

  const lastDispenseClickRef = useRef(0);

  const handleStartDispense = useCallback(async () => {
    const grams = parseFloat(dispenseG);
    if (!grams || grams <= 0) return;
    // 防连点（2026-09-15）：1.5 秒内的重复点击直接忽略。后端每次 start 都会
    // "停旧任务 + 开阀"，密集点击会把香橙派串口命令队列打爆——板卡开始不应答
    // 就触发 8~10 秒的全量重分类，表现就是"越点越卡"。
    const now = Date.now();
    if (now - lastDispenseClickRef.current < 1500) return;
    lastDispenseClickRef.current = now;
    try {
      const res = await fetch("/dispense/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_g: grams }),
      });
      if (res.ok) {
        const r = await res.json();
        if (r.success) {
          // already_running：后端对"同目标重复启动"的幂等应答，直接沿用进行中的任务
          setDispenseDoneText(r.reason === "already_running" ? r.message || "下料已在进行中" : "");
          setDispenseActive(true);
          startDispensePoll();
        } else {
          setDispenseDoneText(`下料失败：${r.error || "未知原因"}`);
        }
      }
    } catch (e) {
      console.error("[下料] 启动失败:", e);
    }
  }, [dispenseG, startDispensePoll]);

  const handleStopDispense = useCallback(async () => {
    try {
      await fetch("/dispense/stop", { method: "POST" });
    } catch (e) {
      console.error("[下料] 停止失败:", e);
    }
    setDispenseActive(false);
    stopDispensePoll();
  }, [stopDispensePoll]);

  // 倒计时 re-render tick
  useEffect(() => {
    const id = setInterval(() => setTimerTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const fetchTemperature = useCallback(async () => {
    try {
      const res = await fetch("/temperature");
      if (res.ok) {
        const data = await res.json();
        const key = JSON.stringify(data);
        if (key !== lastTempKeyRef.current) {
          lastTempKeyRef.current = key;
          setTemperatureData(data);
        }
        lastUpdateTimeRef.current = Date.now();
        setIsTemperatureExpired(false);
      }
    } catch (e) {
      console.error("[温度] 获取数据失败:", e);
    }
  }, []);

  const fetchWeight = useCallback(async () => {
    try {
      const res = await fetch("/weight");
      if (res.ok) {
        const data = await res.json();
        const key = JSON.stringify(data);
        if (key !== lastWeightKeyRef.current) {
          lastWeightKeyRef.current = key;
          setWeightData(data);
        }
        lastWeightUpdateRef.current = Date.now();
        setIsWeightExpired(false);
      }
    } catch (e) {
      console.error("[重量] 获取数据失败:", e);
    }
  }, []);

  useEffect(() => {
    fetch("/temperature/start", { method: "POST" });
    fetchTemperature();
    const tempInterval = window.setInterval(fetchTemperature, 1000);
    const expiryCheck = window.setInterval(() => {
      setIsTemperatureExpired(Date.now() - lastUpdateTimeRef.current > 10000);
    }, 10000);
    return () => {
      clearInterval(tempInterval);
      clearInterval(expiryCheck);
      fetch("/temperature/stop", { method: "POST" });
    };
  }, [fetchTemperature]);

  useEffect(() => {
    fetchWeight();
    const weightInterval = window.setInterval(fetchWeight, 1000);
    const weightExpiryCheck = window.setInterval(() => {
      setIsWeightExpired(Date.now() - lastWeightUpdateRef.current > 15000);
    }, 5000);
    return () => {
      clearInterval(weightInterval);
      clearInterval(weightExpiryCheck);
    };
  }, [fetchWeight]);

  const isDark = theme.mode === "dark";

  const weightTime = weightData.timestamp
    ? (() => { try { return new Date(weightData.timestamp || "").toLocaleTimeString(); } catch { return "--:--:--"; } })()
    : "--:--:--";

  return (
    <div style={{
      padding: `${spacing.lg}px ${spacing.xl}px`,
      borderTop: `1px solid ${theme.borderLight}`,
      background: isDark ? theme.bgSecondary : "rgba(248, 250, 252, 0.95)",
      borderBottomLeftRadius: 16,
      borderBottomRightRadius: 16,
    }}>
      {/* 主体 */}
      <div style={{ display: "flex", gap: `${spacing.md}px`, alignItems: "stretch" }}>
        {/* 左侧：倒计时列表 */}
        <div style={{ flex: 1.2, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {cookingTimers && cookingTimers.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {cookingTimers.map((timer) => {
                const { timeStr, isUrgent } = formatRemaining(timer.end_time);
                const remainingSeconds = timer.total_seconds > 0
                  ? Math.max(0, Math.round(timer.end_time - Date.now() / 1000))
                  : 0;
                const progress = timer.total_seconds > 0
                  ? Math.min(1, remainingSeconds / timer.total_seconds)
                  : 0;
                return (
                  <div key={timer.id} style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "10px 14px",
                    background: isDark ? theme.bgTertiary : "white",
                    border: `1px solid ${theme.borderLight}`,
                    borderRadius: radius.md,
                    boxShadow: isDark ? "none" : "0 1px 3px rgba(0,0,0,0.04)",
                    transition: "all 0.3s ease",
                  }}>
                    {/* 左侧时间 */}
                    <div style={{ textAlign: "center", flexShrink: 0, minWidth: 54 }}>
                      <div style={{
                        fontSize: 18,
                        fontWeight: 800,
                        color: isUrgent ? theme.error : "#fb923c",
                        fontVariantNumeric: "tabular-nums",
                        lineHeight: 1.1,
                      }}>
                        {timeStr}
                      </div>
                      <div style={{ fontSize: 10, color: theme.textMuted, marginTop: 2 }}>剩余</div>
                    </div>

                    {/* 分隔线 */}
                    <div style={{ width: 1, height: 32, background: theme.borderLight, flexShrink: 0 }} />

                    {/* 描述 + 进度 */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                        <span style={{
                          fontSize: 13,
                          color: theme.text,
                          fontWeight: 500,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}>
                          {timer.description}
                        </span>
                        {isUrgent && (
                          <span style={{
                            fontSize: 9,
                            padding: "1px 5px",
                            borderRadius: radius.sm,
                            background: "rgba(239, 68, 68, 0.12)",
                            color: theme.error,
                            flexShrink: 0,
                            fontWeight: 600,
                          }}>
                            即将到期
                          </span>
                        )}
                      </div>
                      <div style={{
                        width: "100%",
                        height: 4,
                        background: theme.borderLight,
                        borderRadius: 2,
                        overflow: "hidden",
                      }}>
                        <div style={{
                          height: "100%",
                          width: `${progress * 100}%`,
                          background: isUrgent ? theme.error : "#fb923c",
                          borderRadius: 2,
                          transition: "width 1s linear",
                        }} />
                      </div>
                    </div>
                  </div>
                );
              })}
              {onCancelCookingTimer && (
                <button
                  onClick={onCancelCookingTimer}
                  style={{
                    fontSize: 11,
                    color: theme.textMuted,
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    padding: "4px 0",
                    textAlign: "right",
                    transition: "color 0.2s ease",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = theme.error; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = theme.textMuted; }}
                >
                  取消全部倒计时
                </button>
              )}
            </div>
          ) : (
            <div style={{
              flex: 1,
              minHeight: 140,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 10,
              background: isDark ? theme.bgTertiary : "white",
              borderRadius: radius.md,
              border: `1px dashed ${theme.borderLight}`,
            }}>
              <div style={{
                width: 48,
                height: 48,
                borderRadius: "50%",
                background: isDark ? "rgba(255,255,255,0.05)" : "rgba(148, 163, 184, 0.1)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}>
                <Timer size={24} color={theme.textMuted} strokeWidth={1.5} />
              </div>
              <div style={{ fontSize: 13, color: theme.textMuted }}>暂无倒计时</div>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>语音说"定时 5 分钟"即可添加</div>
            </div>
          )}
        </div>

        {/* 右侧：环境状态 */}
        <div style={{
          flex: 1,
          minWidth: 220,
          maxWidth: 320,
          padding: spacing.md,
          background: isDark ? theme.bgTertiary : "white",
          borderRadius: radius.md,
          border: `1px solid ${theme.borderLight}`,
          boxShadow: isDark ? "none" : "0 1px 3px rgba(0,0,0,0.04)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
        }}>
          <div style={{ display: "flex", gap: spacing.md, alignItems: "stretch" }}>
            {/* 温度 */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 4 }}>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>温度</div>
              <div style={{
                fontSize: 24,
                fontWeight: 800,
                color: isTemperatureExpired ? theme.textMuted : theme.text,
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.2,
              }}>
                {isTemperatureExpired ? "--" : `${Math.round(temperatureData.average || 0)}°C`}
              </div>
              <div style={{ fontSize: 10, color: theme.textMuted, marginTop: 2 }}>
                目标 {isTemperatureExpired ? "--" : `${Math.round(temperatureData.target || 0)}°C`}
              </div>
            </div>

            {/* 分隔线 */}
            <div style={{ width: 1, background: theme.borderLight }} />

            {/* 重量 */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 4 }}>
              <div style={{ fontSize: 11, color: theme.textSecondary }}>重量</div>
              <div style={{
                fontSize: 24,
                fontWeight: 800,
                color: isWeightExpired ? theme.textMuted : theme.text,
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.2,
              }}>
                {isWeightExpired ? "--" : Math.round(weightData.weight)}
                <span style={{ fontSize: 12, fontWeight: 600, marginLeft: 2 }}>{weightData.unit || "g"}</span>
              </div>
              <div style={{ fontSize: 10, color: theme.textMuted, marginTop: 2 }}>
                {weightTime}
              </div>
              {/* 手动去皮：物品放上秤之前点一下，读数归零；解决重启后基准漂移 */}
              <button
                onClick={async () => {
                  try {
                    await fetch("/weight/tare", { method: "POST" });
                  } catch (e) {
                    console.error("[重量] 去皮失败:", e);
                  }
                }}
                style={{
                  fontSize: 10,
                  color: theme.textSecondary,
                  background: "transparent",
                  border: `1px solid ${theme.borderLight}`,
                  borderRadius: 8,
                  padding: "1px 10px",
                  marginTop: 3,
                  cursor: "pointer",
                }}
                title="秤面为空时点击，读数归零"
              >
                去皮
              </button>
              {/* 自动下料：输入克数启动下料机，按重量自动停；下料中显示剩余量 */}
              <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 3 }}>
                <input
                  value={dispenseG}
                  onChange={(e) => setDispenseG(e.target.value.replace(/[^\d.]/g, ""))}
                  disabled={dispenseActive}
                  style={{
                    width: 46,
                    fontSize: 10,
                    color: theme.text,
                    background: "transparent",
                    border: `1px solid ${theme.borderLight}`,
                    borderRadius: 8,
                    padding: "1px 6px",
                    textAlign: "center",
                  }}
                  title="本次投放克数"
                />
                <span style={{ fontSize: 10, color: theme.textSecondary }}>g</span>
                {dispenseActive ? (
                  <button
                    onClick={handleStopDispense}
                    style={{
                      fontSize: 10,
                      color: theme.error,
                      background: "transparent",
                      border: `1px solid ${theme.borderLight}`,
                      borderRadius: 8,
                      padding: "1px 10px",
                      cursor: "pointer",
                    }}
                    title="立即断电停止下料"
                  >
                    停止（剩 {Math.round(dispenseRemaining)}g）
                  </button>
                ) : (
                  <button
                    onClick={handleStartDispense}
                    style={{
                      fontSize: 10,
                      color: theme.textSecondary,
                      background: "transparent",
                      border: `1px solid ${theme.borderLight}`,
                      borderRadius: 8,
                      padding: "1px 10px",
                      cursor: "pointer",
                    }}
                    title="启动下料机投放指定克数，达到后自动停"
                  >
                    下料
                  </button>
                )}
              </div>
              {dispenseDoneText && (
                <div style={{ fontSize: 10, color: theme.textSecondary, marginTop: 2 }}>
                  {dispenseDoneText}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
