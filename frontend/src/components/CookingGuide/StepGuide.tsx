import { StepGuideProps } from "./types";
import { CheckCircle, SkipForward, Lightbulb, ChevronRight } from "lucide-react";

/**
 * 步骤引导组件
 * 显示当前步骤的详细信息，包括步骤内容、提示和操作按钮
 */
export function StepGuide({ step, totalSteps, onConfirm, onSkip, theme }: StepGuideProps) {
  const displayOrder = Math.min(Math.max(step.order || 1, 1), totalSteps);
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      height: "100%",
      padding: "20px",
    }}>
      {/* 步骤进度指示器 */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        marginBottom: "20px"
      }}>
        {Array.from({ length: totalSteps }, (_, i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: "4px",
              borderRadius: "2px",
              background: i < displayOrder
                ? theme.success
                : i === displayOrder - 1
                  ? theme.primary
                  : theme.borderLight,
              transition: "background 0.3s ease"
            }}
          />
        ))}
      </div>

      {/* 步骤标题 */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        marginBottom: "16px"
      }}>
        <div style={{
          width: "36px",
          height: "36px",
          borderRadius: "50%",
          background: theme.primaryLight,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: "16px",
          fontWeight: 600,
          color: theme.primary,
        }}>
          {displayOrder}
        </div>
        <div>
          <span style={{
            fontSize: "12px",
            color: theme.textMuted,
          }}>
            步骤 {displayOrder} / {totalSteps}
          </span>
          {step.title && (
            <div style={{
              fontSize: "16px",
              fontWeight: 600,
              color: theme.text,
            }}>
              {step.title}
            </div>
          )}
        </div>
      </div>

      {/* 步骤内容 */}
      <div style={{
        flex: 1,
        background: theme.bgSecondary,
        borderRadius: "16px",
        border: `1px solid ${theme.borderLight}`,
        padding: "20px",
        marginBottom: "16px",
        overflow: "auto",
      }}>
        <div style={{
          fontSize: "15px",
          lineHeight: 1.7,
          color: theme.text,
          whiteSpace: "pre-wrap",
        }}>
          {step.content}
        </div>
      </div>

      {/* 提示信息 */}
      {step.tip && (
        <div style={{
          background: theme.accentLight,
          borderRadius: "12px",
          padding: "14px 16px",
          marginBottom: "16px",
          display: "flex",
          gap: "12px",
          alignItems: "flex-start",
        }}>
          <Lightbulb
            size={18}
            color={theme.accent}
            strokeWidth={1.5}
            style={{ flexShrink: 0, marginTop: "2px" }}
          />
          <div>
            <div style={{
              fontSize: "12px",
              fontWeight: 600,
              color: theme.accent,
              marginBottom: "4px",
            }}>
              小贴士
            </div>
            <div style={{
              fontSize: "13px",
              color: theme.textSecondary,
              lineHeight: 1.5,
            }}>
              {step.tip}
            </div>
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div style={{
        display: "flex",
        gap: "12px",
      }}>
        <button
          onClick={onSkip}
          style={{
            flex: "0 0 auto",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            padding: "12px 18px",
            fontSize: "13px",
            fontWeight: 500,
            color: theme.textMuted,
            background: theme.bgTertiary,
            border: `1px solid ${theme.borderLight}`,
            borderRadius: "12px",
            cursor: "pointer",
            transition: "all 0.2s ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = theme.borderLight;
            e.currentTarget.style.color = theme.text;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = theme.bgTertiary;
            e.currentTarget.style.color = theme.textMuted;
          }}
        >
          <SkipForward size={16} strokeWidth={1.5} />
          跳过
        </button>

        <button
          onClick={onConfirm}
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "8px",
            padding: "14px 20px",
            fontSize: "14px",
            fontWeight: 600,
            color: theme.buttonPrimaryText,
            background: theme.buttonPrimary,
            border: "none",
            borderRadius: "12px",
            cursor: "pointer",
            transition: "all 0.2s ease",
            boxShadow: `0 4px 12px ${theme.primaryGlow}`,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = "translateY(-1px)";
            e.currentTarget.style.boxShadow = `0 6px 16px ${theme.primaryGlow}`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "translateY(0)";
            e.currentTarget.style.boxShadow = `0 4px 12px ${theme.primaryGlow}`;
          }}
        >
          <CheckCircle size={18} strokeWidth={1.5} />
          完成此步骤
          <ChevronRight size={18} strokeWidth={1.5} />
        </button>
      </div>
    </div>
  );
}
