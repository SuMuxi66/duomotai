import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { List, useDynamicRowHeight, type ListImperativeAPI, type RowComponentProps } from "react-window";
import { Theme } from "../lib/theme";
import { Markdown } from "./Markdown";
import { Msg } from "../types";

interface VirtualMessageListProps {
  messages: Msg[];
  theme: Theme;
}

interface MessageItemProps {
  message: Msg;
  index: number;
  theme: Theme;
  isHovered: boolean;
  onHover: (index: number | null) => void;
}

/**
 * 将模型思考内容格式化为纯文本。
 *
 * 部分模型的 thinking/reasoning 输出会包裹在 <think>...</think> 等 XML 标签中，
 * 或者带有 Markdown 标记、多余空行。此函数仅保留可读文字，避免前端出现
 * "JSON 串"/代码块/标签残留等杂乱显示。
 */
function formatReasoningText(raw: string): string {
  if (!raw) return "";

  // 1. 提取 <think>、<thinking>、<reasoning> 等标签内部文字
  let text = raw.replace(/<(think|thinking|reasoning)[^>]*>([\s\S]*?)<\/(think|thinking|reasoning)>/gi, "$2");

  // 2. 完全去除 <tool>、<image> 等工具标签及其内部内容（通常是 JSON）
  text = text.replace(/<(tool|image|audio|video)[^>]*>[\s\S]*?<\/(tool|image|audio|video)>/gi, "");

  // 3. 去除其余 XML/HTML 标签
  text = text.replace(/<[^>]+>/g, "");

  // 4. 去除 Markdown 加粗/斜体标记，只保留文字
  text = text.replace(/(\*\*|__)(.*?)\1/g, "$2");
  text = text.replace(/(\*|_)(.*?)\1/g, "$2");

  // 5. 去除整行 Markdown 标题
  text = text.replace(/^[\s]*#{1,6}[\s]+.*$/gm, "");

  // 6. 去除行首列表符号（*Input: -> Input:）
  text = text.replace(/^[\s]*[-*+][\s]*/gm, "");

  // 7. 去除独立的章节标题行（如 "ThinkingProcess:", "Analyze the Request:" 等）
  text = text.replace(/^[\s]*[A-Za-z\u4e00-\u9fa5][A-Za-z0-9\s\u4e00-\u9fa5\-]*:[\s]*$/gim, "");

  // 8. 去除独立的 JSON / 代码串行
  text = text.replace(/^[\s]*[\{\[][^\n]*[\}\]][\s]*$/gm, "");

  // 9. 去除模型自我纠错的 "Wait," 元话语句
  text = text.replace(/^[\s]*Wait,.*$/gim, "");

  // 10. 合并连续空行，去除行首尾空白
  text = text.replace(/\n[\s]*\n[\s]*\n/g, "\n\n").trim();

  return text;
}

// 记忆化的消息气泡组件（极简现代风格）
const MessageItem = React.memo<MessageItemProps>(({
  message,
  index,
  theme,
  isHovered,
  onHover
}) => {
  const isUser = message.role === "user";
  const isVision = message.role === "vision";
  const isDark = theme.mode === 'dark';
  const hasReasoning = (message.role === "assistant" || message.role === "assistant_stream") && !!message.reasoning?.trim();
  const [reasoningOpen, setReasoningOpen] = useState(Boolean(message.reasoningExpanded));
  const isStreaming = message.role === "assistant_stream";

  // 响应式字号
  const f = {
    bubble: 'clamp(13px, 1.4vw + 0.3rem, 16px)',
    button: 'clamp(11px, 1.1vw + 0.2rem, 13px)',
    reasoning: 'clamp(12px, 1.2vw + 0.2rem, 14px)',
    timestamp: 'clamp(10px, 0.8vw + 0.2rem, 12px)',
    emoji: 'clamp(9px, 0.7vw + 0.1rem, 11px)',
  };
  const bubblePad = 'clamp(8px, 0.8vw + 0.3rem, 12px) clamp(12px, 1.4vw + 0.3rem, 20px)';

  // 实时计时：流式推理中用 setInterval 递增秒数，完成后用 reasoningDurationMs
  // 使用 ref 存储 interval ID，确保组件卸载或状态变化时能立即清理
  const [liveSeconds, setLiveSeconds] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(message.timestamp ?? Date.now());

  // 当消息从 streaming 变为 completed 时，更新 startTime 为最终值
  useEffect(() => {
    if (isStreaming) {
      // 流式开始时重置起点
      startTimeRef.current = message.timestamp ?? Date.now();
      setLiveSeconds(0);
    }
    // 非 streaming 时不重置，保留之前的值
  }, [isStreaming, message.timestamp]);

  useEffect(() => {
    // 清理旧 interval
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (!hasReasoning || !isStreaming) return;

    // 启动 interval，每秒更新一次
    intervalRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);
      setLiveSeconds(Math.max(0, elapsed));
    }, 1000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [hasReasoning, isStreaming]);

  // 组件卸载时清理 interval
  useEffect(() => {
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, []);

  const reasoningSeconds = hasReasoning
    ? (isStreaming
        ? liveSeconds
        : Math.round((message.reasoningDurationMs ?? 0) / 1000))
    : 0;

  useEffect(() => {
    setReasoningOpen(Boolean(message.reasoningExpanded));
  }, [message.reasoningExpanded, message.reasoning]);

  const timestamp = message.timestamp
    ? new Date(message.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

  // 21st 风格气泡配色：柔和渐变 + 细腻阴影
  let bubbleBg: string;
  let bubbleText: string;
  let bubbleRadius: string;
  let bubbleShadow: string;
  let bubbleBorder: string;

  if (isUser) {
    bubbleBg = isDark
      ? 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)'
      : 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)';
    bubbleText = '#ffffff';
    bubbleRadius = '20px 20px 6px 20px';
    bubbleShadow = isDark
      ? '0 4px 14px rgba(37, 99, 235, 0.35)'
      : '0 4px 14px rgba(37, 99, 235, 0.25)';
    bubbleBorder = 'none';
  } else if (isVision) {
    bubbleBg = isDark ? '#1e3a5f' : '#e0f2fe';
    bubbleText = isDark ? '#e0f2fe' : '#0369a1';
    bubbleRadius = '20px 20px 20px 6px';
    bubbleShadow = isDark
      ? '0 2px 12px rgba(2, 132, 199, 0.25)'
      : '0 2px 12px rgba(2, 132, 199, 0.12)';
    bubbleBorder = isDark ? '1px solid rgba(56, 189, 248, 0.15)' : '1px solid rgba(14, 165, 233, 0.12)';
  } else {
    bubbleBg = isDark ? '#262626' : '#ffffff';
    bubbleText = isDark ? '#e5e5e5' : '#1a1a1a';
    bubbleRadius = '20px 20px 20px 6px';
    bubbleShadow = isDark
      ? '0 2px 12px rgba(0, 0, 0, 0.35)'
      : '0 2px 12px rgba(15, 23, 42, 0.06)';
    bubbleBorder = isDark ? '1px solid rgba(255, 255, 255, 0.08)' : '1px solid rgba(148, 163, 184, 0.12)';
  }

  return (
    <div
      style={{
        display: 'flex',
        gap: '10px',
        flexDirection: isUser ? 'row-reverse' : 'row',
        animation: 'message-in 0.35s ease-out',
        padding: '6px 0',
        alignItems: 'flex-end',
        position: 'relative',
      }}
      onMouseEnter={() => onHover(index)}
      onMouseLeave={() => onHover(null)}
    >
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: isUser ? 'flex-end' : 'flex-start',
        maxWidth: 'min(85%, 42rem)',
      }}>
        {/* 21st 风格气泡 */}
        <div style={{
          padding: bubblePad,
          background: bubbleBg,
          color: bubbleText,
          borderRadius: bubbleRadius,
          border: bubbleBorder,
          fontSize: f.bubble,
          lineHeight: '1.65',
          whiteSpace: 'pre-line',
          wordBreak: 'break-word',
          overflowWrap: 'anywhere',
          boxShadow: isHovered
            ? `0 8px 24px ${isDark ? 'rgba(0,0,0,0.45)' : 'rgba(15, 23, 42, 0.12)'}`
            : bubbleShadow,
          transition: 'box-shadow 0.25s ease',
        }}>
          {/* 思考过程折叠 */}
          {hasReasoning && (
            <div style={{ marginBottom: '8px' }}>
              <button
                type="button"
                onClick={() => setReasoningOpen((v) => !v)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 10px',
                  borderRadius: '8px',
                  border: `1px solid ${isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)'}`,
                  background: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)',
                  color: isUser ? 'rgba(255,255,255,0.85)' : (isDark ? '#bbb' : '#666'),
                  cursor: 'pointer',
                  fontSize: f.button,
                  width: '100%',
                  transition: 'background 0.15s',
                }}
                aria-expanded={reasoningOpen}
              >
                <span style={{ opacity: 0.6, fontSize: f.emoji }}>💭</span>
                <span style={{ fontWeight: 500, flex: 1, textAlign: 'left' }}>
                  已思考 {reasoningSeconds} 秒
                </span>
                <span style={{
                  transform: reasoningOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s',
                  opacity: 0.6,
                }}>
                  ▾
                </span>
              </button>
              {reasoningOpen && (
                <div style={{
                  marginTop: '6px',
                  padding: '8px 10px',
                  borderRadius: '8px',
                  border: `1px solid ${isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)'}`,
                  background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)',
                  fontSize: f.reasoning,
                  lineHeight: '1.6',
                  color: isUser ? 'rgba(255,255,255,0.8)' : (isDark ? '#aaa' : '#666') ,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  <div style={{ borderLeft: `2px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)'}`, paddingLeft: '10px' }}>
                    {formatReasoningText(message.reasoning || "")}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 消息内容 */}
          <div style={{ fontSize: f.bubble, lineHeight: '1.65' }}>
            <Markdown
              content={message.text}
              isStreaming={message.role === "assistant_stream"}
              style={{ color: 'inherit' }}
            />
          </div>
        </div>

        {/* 时间戳（hover 时显示） */}
        <div style={{
          fontSize: f.timestamp,
          color: theme.textMuted,
          opacity: isHovered ? 0.8 : 0,
          transition: 'opacity 0.2s',
          marginTop: '4px',
          padding: '0 4px',
        }}>
          {timestamp}
        </div>
      </div>
    </div>
  );
});

MessageItem.displayName = 'MessageItem';

// 行渲染组件 - react-window v2 的 rowComponent
interface RowProps {
  messages: Msg[];
  theme: Theme;
  hoveredMessage: number | null;
  onHover: (index: number | null) => void;
}

const Row = ({
  index,
  style,
  messages,
  theme,
  hoveredMessage,
  onHover,
}: RowComponentProps<RowProps>) => {
  const message = messages[index];
  if (!message) return null;

  return (
    <div style={style}>
      <MessageItem
        message={message}
        index={index}
        theme={theme}
        isHovered={hoveredMessage === index}
        onHover={onHover}
      />
    </div>
  );
};

export function VirtualMessageList({
  messages,
  theme
}: VirtualMessageListProps) {
  const [hoveredMessage, setHoveredMessage] = useState<number | null>(null);
  const [containerHeight, setContainerHeight] = useState(600);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<ListImperativeAPI>(null);
  const autoScrollRef = useRef(true);
  const prevMessageCountRef = useRef(messages.length);

  // 使用 useCallback 包裹 onHover 避免 memo 失效
  const onHover = useCallback((index: number | null) => {
    setHoveredMessage(index);
  }, []);

  // 动态行高 hook，默认预估高度 100px
  const dynamicRowHeight = useDynamicRowHeight({
    defaultRowHeight: 100,
  });

  // 监听容器高度变化
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const height = entry.contentBoxSize[0]?.blockSize ?? entry.contentRect.height;
        if (height > 0) {
          setContainerHeight(height);
        }
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // 检测用户是否手动向上滚动
  useEffect(() => {
    const el = containerRef.current?.querySelector('.msg-list') as HTMLDivElement | null;
    if (!el) return;

    const handleScroll = () => {
      // 距离底部 80px 以内视为"在底部"
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      autoScrollRef.current = atBottom;
    };

    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // 新消息/会话历史加载时自动滚动到底部
  useEffect(() => {
    const grew = messages.length > prevMessageCountRef.current;
    prevMessageCountRef.current = messages.length;
    if (!grew || !autoScrollRef.current) return;

    // scrollToRow 按估算行高计算目标位置，动态行高测量后会漂移且不修正；
    // 直接把滚动容器顶到真实底部，配合重试覆盖测量收敛过程
    const scrollToEnd = () => {
      const el = containerRef.current?.querySelector('.msg-list') as HTMLDivElement | null;
      if (el) el.scrollTop = el.scrollHeight;
    };
    // 动态行高下，react-window 对未渲染行用估算高度：跳到底部后新行测量又会改变总高度。
    // 加载后 4 秒内每 200ms 强制吸底一次，让估算逐步收敛到真实底部，之后交还控制权
    scrollToEnd();
    const raf = requestAnimationFrame(scrollToEnd);
    const timers: number[] = [];
    for (let t = 200; t <= 4000; t += 200) {
      timers.push(window.setTimeout(scrollToEnd, t));
    }
    return () => {
      cancelAnimationFrame(raf);
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, [messages.length, listRef]);

  // 刷新/挂载（含本地缓存直载）时定位到最底部：动态行高估算需持续收敛
  useEffect(() => {
    if (messages.length === 0) return;
    const scrollToEnd = () => {
      const el = containerRef.current?.querySelector('.msg-list') as HTMLDivElement | null;
      if (el) el.scrollTop = el.scrollHeight;
    };
    scrollToEnd();
    const raf = requestAnimationFrame(scrollToEnd);
    const timers: number[] = [];
    for (let t = 200; t <= 4000; t += 200) {
      timers.push(window.setTimeout(scrollToEnd, t));
    }
    return () => {
      cancelAnimationFrame(raf);
      timers.forEach((t) => window.clearTimeout(t));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 流式输出时持续滚动到底部
  useEffect(() => {
    if (!autoScrollRef.current) return;
    const lastMsg = messages[messages.length - 1];
    if (lastMsg?.role === 'assistant_stream') {
      listRef.current?.scrollToRow({
        index: messages.length - 1,
        align: 'end',
        behavior: 'instant',
      });
    }
  }, [messages, listRef]);

  // rowProps - 传给 Row 组件的额外属性
  const rowProps = useMemo<RowProps>(() => ({
    messages,
    theme,
    hoveredMessage,
    onHover,
  }), [messages, theme, hoveredMessage, onHover]);

  return (
    <div
      ref={containerRef}
      style={{
        height: "100%",
        overflow: 'hidden',
        minHeight: 0
      }}
    >
      <style>{`
        .msg-list {
          scrollbar-width: thin;
          scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
        }
        .msg-list::-webkit-scrollbar {
          width: 6px;
        }
        .msg-list::-webkit-scrollbar-track {
          background: transparent;
          margin: 4px 0;
        }
        .msg-list::-webkit-scrollbar-thumb {
          background: rgba(148, 163, 184, 0.35);
          border-radius: 10px;
          border: 1px solid rgba(255, 255, 255, 0.3);
        }
        .msg-list::-webkit-scrollbar-thumb:hover {
          background: rgba(148, 163, 184, 0.55);
        }
      `}</style>
      <List
        listRef={listRef}
        rowComponent={Row}
        rowCount={messages.length}
        rowHeight={dynamicRowHeight}
        rowProps={rowProps}
        className="msg-list"
        style={{ height: containerHeight, overflow: 'auto', scrollbarWidth: 'thin', scrollbarColor: 'rgba(148, 163, 184, 0.35) transparent' }}
        overscanCount={5}
      />
    </div>
  );
}
