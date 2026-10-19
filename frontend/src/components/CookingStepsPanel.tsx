/**
 * CookingStepsPanel - 做饭步骤任务清单卡片
 *
 * 与 ServingsCalcCard 同款的聊天区内联卡片样式（输入框上方弹出）：
 * 已完成打勾划线、当前步骤高亮并展开详情。
 * 全部完成时：短暂展示「全部完成」徽章后自动淡出消失（带动画）。
 */
import React, { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { radius } from "@/styles";
import type { Theme } from "@/lib/theme";
import type { Recipe } from "@/hooks/useCookingMode";

export interface CookingStepsPanelProps {
  theme: Theme;
  recipe: Recipe;
  /** 当前进行中的步骤（1 基，与后端 completed_steps 同一套序号） */
  currentStep: number;
  completedSteps: number[];
  state: string;
}

/** 完成徽章停留时长（ms）——给用户看清「全部完成」后再消失 */
const DONE_HOLD_MS = 1200;
/** 消失动画时长（ms） */
const LEAVE_ANIM_MS = 320;

export const CookingStepsPanel: React.FC<CookingStepsPanelProps> = ({
  theme,
  recipe,
  currentStep,
  completedSteps,
  state,
}) => {
  // 全部完成 → 先停留再自动消失：leaving 控制消失动画，hidden 控制彻底卸载
  const [leaving, setLeaving] = useState(false);
  const [hidden, setHidden] = useState(false);

  const isDark = theme.mode === "dark";
  const steps = recipe.steps || [];
  const total = steps.length;
  const doneCount = steps.reduce(
    (acc, _s, i) => (completedSteps.includes(i + 1) ? acc + 1 : acc),
    0,
  );
  const finished = state === "FINISHED" || (total > 0 && doneCount === total);

  // 换菜谱时复位消失动画状态（避免上次完成把新面板藏掉）
  useEffect(() => {
    setLeaving(false);
    setHidden(false);
  }, [recipe.name]);

  // 全部完成 → 展示 1.2s 后启动消失动画，动画结束彻底卸载
  useEffect(() => {
    if (!finished) return;
    const t1 = setTimeout(() => setLeaving(true), DONE_HOLD_MS);
    const t2 = setTimeout(() => setHidden(true), DONE_HOLD_MS + LEAVE_ANIM_MS);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [finished]);

  if (hidden) {
    return null;
  }

  // 与 ServingsCalcCard 一致的卡片外观 + 消失动画（淡出 + 缩小上移）
  const wrapperStyle: React.CSSProperties = {
    padding: "0 12px 10px",
    opacity: leaving ? 0 : 1,
    transform: leaving ? "scale(0.86) translateY(-8px)" : "none",
    transition: `opacity ${LEAVE_ANIM_MS}ms ease, transform ${LEAVE_ANIM_MS}ms ease`,
    pointerEvents: leaving ? "none" : "auto",
  };

  return (
    <div style={wrapperStyle}>
      <div
        style={{
          padding: "12px 14px",
          borderRadius: 14,
          background: isDark ? "rgba(23, 32, 51, 0.72)" : "rgba(248, 250, 252, 0.95)",
          border: `1px solid ${theme.borderLight}`,
          boxShadow: isDark ? "none" : "0 4px 16px rgba(15, 23, 42, 0.06)",
        }}
      >
        {/* 头部：菜名 + 完成进度（与智能计算卡片同款字级） */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            fontWeight: 700,
            color: theme.text,
            marginBottom: 10,
          }}
        >
          <span
            style={{
              flex: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            🍳 {recipe.name} · 步骤清单：
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: finished ? theme.success : theme.primary,
              background: finished ? theme.success + "20" : theme.primaryLight,
              padding: "2px 8px",
              borderRadius: 999,
              flexShrink: 0,
            }}
          >
            {finished ? "全部完成" : `${doneCount}/${total}`}
          </span>
        </div>

        {/* 步骤清单 */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            maxHeight: 240,
            overflowY: "auto",
          }}
        >
          {total === 0 && (
            <div style={{ fontSize: 12.5, color: theme.textMuted, textAlign: "center" }}>
              该菜谱暂无步骤清单
            </div>
          )}
          {steps.map((step, i) => {
            const num = i + 1;
            const done = completedSteps.includes(num);
            const current = !finished && num === currentStep;
            const title = (step.title || "").trim() || step.content || `第 ${num} 步`;

            return (
              <div
                key={num}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  fontSize: 12.5,
                  lineHeight: 1.5,
                }}
              >
                {/* 序号 / 对勾 */}
                <div
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    flexShrink: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10.5,
                    fontWeight: 700,
                    marginTop: 1,
                    ...(done
                      ? { background: theme.success, color: "#fff" }
                      : current
                        ? { background: theme.primary, color: "#fff" }
                        : { border: `1.5px solid ${theme.borderLight}`, color: theme.textMuted }),
                  }}
                >
                  {done ? <Check size={11} strokeWidth={3} /> : num}
                </div>

                {/* 步骤文本 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                    <span
                      style={{
                        fontWeight: current ? 700 : 500,
                        color: done ? theme.textMuted : current ? theme.primary : theme.textSecondary,
                        textDecoration: done ? "line-through" : "none",
                        overflow: current ? "visible" : "hidden",
                        textOverflow: current ? "clip" : "ellipsis",
                        whiteSpace: current ? "normal" : "nowrap",
                      }}
                    >
                      {title}
                    </span>
                    {current && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          color: theme.primary,
                          flexShrink: 0,
                        }}
                      >
                        进行中
                      </span>
                    )}
                  </div>

                  {/* 当前步骤展开详情 */}
                  {current && (
                    <div style={{ marginTop: 4 }}>
                      {step.title && step.title.trim() !== step.content && (
                        <div
                          style={{
                            fontSize: 12,
                            color: theme.textSecondary,
                            whiteSpace: "pre-wrap",
                            lineHeight: 1.6,
                          }}
                        >
                          {step.content}
                        </div>
                      )}
                      {step.tip && (
                        <div
                          style={{
                            marginTop: 6,
                            fontSize: 11,
                            color: theme.textMuted,
                            background: isDark ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.7)",
                            border: `1px solid ${theme.borderLight}`,
                            borderRadius: radius.md,
                            padding: "6px 8px",
                            lineHeight: 1.5,
                          }}
                        >
                          💡 {step.tip}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
