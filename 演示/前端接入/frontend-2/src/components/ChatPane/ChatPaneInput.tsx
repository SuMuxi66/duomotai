// @ts-nocheck
/**
 * ★ 演示版 —— ChatPaneInput（小任务2：文字交互）
 *
 * 演示"发送文字"的完整过程：
 *   输入框收集文字 → ★演示②创建发送字段(messagePayload) → onSendText 发出。
 * 文件上传、输入历史、Tooltip 等全功能已移除。
 * 完整实现见 frontend-2/src/components/ChatPane/ChatPaneInput.tsx
 * 字段构造参考 frontend-2/src/hooks/useChatEngine.ts 的 messagePayload
 */
// =========================================================
// ★ 演示要点（小任务2：文字交互）
//   ① 回车即发 —— 见下方 ★演示①
//   ② 创建发送的文字字段 —— 见下方 ★演示②
// =========================================================










import { useEffect, useRef, type KeyboardEvent } from "react";
import { Square, ArrowUp } from "lucide-react";
import type { Theme } from "@/lib/theme";

// 发送消息的字段结构（要发什么，逐项列出）
export interface ChatMessagePayload {
  request_id: string;      // 请求编号
  client_sent_at: number;  // 发送时间戳
  text: string;            // ★ 用户输入的文字内容
  // 按需追加：图片帧 jpeg_b64、文件 file_b64、温度/重量等
}

export interface ChatPaneInputProps {
  theme: Theme;
  isDark: boolean;
  input: string;
  setInput: (s: string) => void;
  /** ★ 发送入口：携带构造好的字段发出 */
  onSendText: (payload: ChatMessagePayload) => void;
  /** AI 回复中显示停止按钮 */
  isResponding?: boolean;
  onStop?: () => void;
}
















export function ChatPaneInput(props: ChatPaneInputProps) {
  const { theme } = props;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 自动伸缩高度
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [props.input]);

  // ★演示②: 创建发送的文字字段（要发什么，逐项列出）
  const handleSend = () => {
    const text = props.input.trim();
    if (!text) return;
    const messagePayload: ChatMessagePayload = {
      request_id: "req-" + Date.now(),   // 请求编号
      client_sent_at: Date.now(),        // 发送时间戳
      text,                              // ★ 用户输入的文字内容
    };
    props.onSendText(messagePayload);    // 交给上层经 WebSocket 发出
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // ★演示①: 回车即发送（Shift+Enter 换行）
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      style={{
        margin: "0 12px 12px",
        border: `1px solid ${theme.inputBorder}`,
        borderRadius: 24,
        padding: 8,
        background: theme.inputBg,
      }}
    >
      {/* 输入框 —— 无边框，透明背景 */}
      <textarea
        ref={textareaRef}
        value={props.input}
        onChange={(e) => props.setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入消息，Enter 发送..."
        rows={1}
        aria-label="输入消息"
        style={{
          width: "100%",
          minHeight: 44,
          maxHeight: 120,
          padding: "8px 8px",
          background: "transparent",
          color: theme.inputText,
          border: "none",
          borderRadius: 0,
          fontSize: 14,
          lineHeight: 1.5,
          resize: "none",
          outline: "none",
          boxSizing: "border-box",
        }}
      />

      {/* 右侧 actions：发送 / 停止 */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 8,
          paddingTop: 8,
        }}
      >
        {props.isResponding ? (
          <button
            type="button"
            onClick={() => props.onStop?.()}
            aria-label="停止响应"
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "#ef4444",
              border: "none",
              color: "#fff",
              cursor: "pointer",
            }}
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            type="button"
            // ★演示②: 发送按钮 → 构造字段并发出
            onClick={handleSend}
            disabled={!props.input.trim()}
            aria-label="发送消息"
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: theme.buttonPrimary,
              border: "none",
              color: theme.buttonPrimaryText,
              cursor: props.input.trim() ? "pointer" : "not-allowed",
              opacity: props.input.trim() ? 1 : 0.5,
            }}
          >
            <ArrowUp size={18} />
          </button>
        )}
      </div>
    </div>
  );
}