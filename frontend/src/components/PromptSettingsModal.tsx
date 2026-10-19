import type { Theme } from "../lib/theme";

export interface PromptSettingsModalProps {
  theme: Theme;
  customPrompt: string;
  setCustomPrompt: (value: string) => void;
  onClose: () => void;
}

/**
 * 个性化提示词设置弹窗
 * 在每次发送聊天消息时自动附加到用户问题前面
 */
export function PromptSettingsModal({
  theme,
  customPrompt,
  setCustomPrompt,
  onClose,
}: PromptSettingsModalProps) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 2000,
        background: "rgba(15, 23, 42, 0.35)",
        backdropFilter: "blur(8px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "min(720px, 100%)",
          borderRadius: "24px",
          background: theme.bgSecondary,
          border: "1px solid " + theme.borderLight,
          boxShadow: "0 24px 80px rgba(15, 23, 42, 0.24)",
          padding: "24px",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "16px",
          }}
        >
          <div>
            <div style={{ fontSize: "20px", fontWeight: 700, color: theme.text }}>
              个性化提示词
            </div>
            <div style={{ fontSize: "13px", color: theme.textMuted, marginTop: "4px" }}>
              会在每次发送聊天消息时自动附加到用户问题前面
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              width: "36px",
              height: "36px",
              borderRadius: "12px",
              border: "1px solid " + theme.borderLight,
              background: theme.bg,
              color: theme.textSecondary,
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>
        <textarea
          value={customPrompt}
          onChange={(e) => setCustomPrompt(e.target.value)}
          placeholder="例如：你是一个更简洁的中文助手，回答要先给结论，再给步骤。"
          style={{
            width: "100%",
            minHeight: "240px",
            resize: "vertical",
            borderRadius: "18px",
            border: "1px solid " + theme.borderLight,
            background: theme.bg,
            color: theme.text,
            padding: "16px",
            fontSize: "14px",
            lineHeight: 1.7,
            outline: "none",
          }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "12px",
            marginTop: "16px",
          }}
        >
          <button
            onClick={() => setCustomPrompt("")}
            style={{
              padding: "10px 16px",
              borderRadius: "14px",
              border: "1px solid " + theme.borderLight,
              background: theme.bg,
              color: theme.textSecondary,
              cursor: "pointer",
            }}
          >
            清空
          </button>
          <button
            onClick={onClose}
            style={{
              padding: "10px 16px",
              borderRadius: "14px",
              border: "1px solid " + theme.primary,
              background: theme.primary,
              color: "white",
              cursor: "pointer",
            }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
