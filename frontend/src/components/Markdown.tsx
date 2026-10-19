import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import hljs from "highlight.js";
import "katex/dist/katex.min.css";

interface MarkdownProps {
  content: string;
  className?: string;
  style?: React.CSSProperties;
  isStreaming?: boolean;
}

const preprocessContent = (content: string, streaming = false): string => {
  const withThinkQuoted = content.replace(/<think\b[^>]*>([\s\S]*?)<\/think>/gi, (_, thinkContent) => {
    const safeText = String(thinkContent).trim();
    return `> [思考过程]\n>\n> ${safeText.replace(/\n/g, "\n> ")}`;
  });
  if (!streaming) return normalizeFreeformMarkdown(withThinkQuoted);
  // 流式时最后一行还没写完：它随时会长，此时套用"行中间 # 插换行""行尾补硬换行"
  // 这类依赖整行上下文的规则，会把半截内容误判成大号粗黑标题，等换行到达又变回正文，
  // 看起来就是"先全部加粗加黑、最后才正常"。所以最后一行走保守规则，
  // 换行一到它就进入已完整部分，按完整规则渲染。
  const cut = withThinkQuoted.lastIndexOf("\n");
  if (cut < 0) return normalizeFreeformMarkdown(withThinkQuoted, false);
  return (
    normalizeFreeformMarkdown(withThinkQuoted.slice(0, cut + 1), true) +
    normalizeFreeformMarkdown(withThinkQuoted.slice(cut + 1), false)
  );
};

// LLM 自由格式输出常见"伪 markdown"：行首"-内容"缺空格成不了列表、
// 行间无空行导致整段被 markdown 规范折叠成一段、### 标题粘在行中间或后缺空格、
// 食材项挤在一行。这里做确定性规范化（代码围栏内原样保留），规则已通过幂等性自测
// aggressive=false 用于流式中的"最后一行（还没写完）"：跳过依赖整行上下文的规则
// （行中间 # 前插换行、行尾补硬换行空格），只保留行首标题补空格、列表与序号修复。
const normalizeFreeformMarkdown = (text: string, aggressive = true): string => {
  const parts = text.split(/(```[\s\S]*?(?:```|$))/g);
  return parts
    .map((part, index) => {
      if (index % 2 === 1) return part;
      const base = part
        .replace(/([）)])[ \t]*-[ \t]*(?=\S)/g, "$1\n- ")
        .replace(/(?<=[克毫升个根片])-[ \t]+(?=\S)/g, "\n- ")
        .replace(/(^|\n)(#{1,6})(?=[^\s#])/g, "$1$2 ")
        .replace(/((?:^|\n)(?:#{1,6}\s+)?)(\d+)[.、](?=[^\d\s])/g, "$1$2. ")
        .replace(/(^|\n)-(?!\s|-)/g, "$1- ");
      if (!aggressive) return base;
      return base
        .replace(/([^\n#])(#{1,6})[ \t]+/g, "$1\n$2 ")
        .replace(/([^\n#])(#{1,6})(?=[^\s#\n])/g, "$1\n$2 ")
        .replace(/(?<!  )(?<!\n)\n(?!\n)/g, "  \n");
    })
    .join("");
};

const CodeBlock: React.FC<{
  language?: string;
  children: string;
}> = ({ language, children }) => {
  const [copied, setCopied] = React.useState(false);
  const codeRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (codeRef.current && !language) {
      hljs.highlightElement(codeRef.current);
    }
  }, [children, language]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const highlightedCode = useMemo(() => {
    if (language && hljs.getLanguage(language)) {
      try {
        return hljs.highlight(children, { language }).value;
      } catch {
        return children;
      }
    }
    return null;
  }, [language, children]);

  return (
    <div
      className="code-block-wrapper"
      style={{
        position: "relative",
        margin: "8px 0",
        borderRadius: "8px",
        overflow: "hidden",
        background: "#1e1e1e",
      }}
    >
      <div
        className="code-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "6px 12px",
          background: "#2d2d2d",
          borderBottom: "1px solid #3d3d3d",
          fontSize: "12px",
          color: "#888",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: language ? "#4caf50" : "#ff9800",
            }}
          />
          {language || "auto"}
        </span>
        <button
          onClick={handleCopy}
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            fontSize: "12px",
            color: "#888",
            padding: "2px 8px",
            borderRadius: "4px",
          }}
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: "12px",
          overflow: "auto",
          fontSize: "13px",
          lineHeight: "1.5",
          fontFamily: 'Consolas, Monaco, "Courier New", monospace',
          background: "#1e1e1e",
          color: "#d4d4d4",
        }}
      >
        {highlightedCode ? (
          <code dangerouslySetInnerHTML={{ __html: highlightedCode }} style={{ background: "transparent" }} />
        ) : (
          <code ref={codeRef} className="hljs" style={{ background: "transparent" }}>
            {children}
          </code>
        )}
      </pre>
    </div>
  );
};

/**
 * 流式期间把渲染频率压到 ~16fps。
 *
 * 实测后端一秒能推 80+ 个 assistant_delta（465 字 / 330 帧），每个 delta 都会
 * 触发一次"全量 remark 解析 + React 协调 + 虚拟列表重测量"，文字就是这么抽搐的。
 * 节流期间仍保证最后一次 delta 一定会被渲染（trailing），流式结束切到
 * isStreaming=false 后直接同步用最终文本，结果与不节流完全一致。
 */
const STREAM_RENDER_INTERVAL_MS = 60;

function useThrottledValue(value: string, ms: number, enabled: boolean): string {
  const [throttled, setThrottled] = useState(value);
  const lastAtRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const apply = () => {
      lastAtRef.current = Date.now();
      setThrottled(value);
    };
    const wait = ms - (Date.now() - lastAtRef.current);
    if (wait <= 0) {
      apply();
      return;
    }
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(apply, wait);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [value, ms, enabled]);

  return enabled ? throttled : value;
}

/**
 * 流式中间态的 markdown 是"半截"的，直接丢给 remark 会出现整段错乱：
 * - 未闭合的 ``` 会把后面所有内容吞成黑底代码块（看起来就是"整片加黑"）；
 * - 孤立的一个 ** 要么原样显示成星号，要么和下一个 ** 之间整段被判成加粗
 *   （看起来就是"全部先加粗"），等闭合的那个到达才恢复正常。
 * 这里只做最小修补，等真正的闭合标记到达后自然会渲染成最终样式。
 */
const stabilizeStreamingMarkdown = (text: string): string => {
  let out = text;

  const fenceCount = (out.match(/^```/gm) || []).length;
  if (fenceCount % 2 === 1) out += "\n```";

  const boldMarks = [...out.matchAll(/\*\*/g)];
  if (boldMarks.length % 2 === 1) {
    const at = boldMarks[boldMarks.length - 1].index ?? 0;
    out = out.slice(0, at) + out.slice(at + 2);
  }

  return out;
};

export const Markdown: React.FC<MarkdownProps> = ({ content, className, style, isStreaming = false }) => {
  const renderContent = useThrottledValue(content, STREAM_RENDER_INTERVAL_MS, isStreaming);
  const processedContent = useMemo(
    () => preprocessContent(isStreaming ? stabilizeStreamingMarkdown(renderContent) : renderContent, isStreaming),
    [renderContent, isStreaming]
  );

  const components = useMemo(
    () => ({
      code: ({ inline, className: codeClassName, children, ...props }: any) => {
        const match = /language-(\w+)/.exec(codeClassName || "");
        const language = match ? match[1] : undefined;

        if (!inline && (language || String(children).includes("\n"))) {
          return <CodeBlock language={language}>{String(children).replace(/\n$/, "")}</CodeBlock>;
        }

        return (
          <code
            style={{
              background: "rgba(0, 0, 0, 0.08)",
              padding: "2px 6px",
              borderRadius: "4px",
              fontSize: "0.9em",
              fontFamily: 'Consolas, Monaco, "Courier New", monospace',
            }}
            {...props}
          >
            {children}
          </code>
        );
      },
      pre: ({ children }: any) => <>{children}</>,
      p: ({ children }: any) => <p style={{ margin: "0 0 8px 0", lineHeight: "1.6" }}>{children}</p>,
      ul: ({ children }: any) => <ul style={{ margin: "8px 0", paddingLeft: "20px" }}>{children}</ul>,
      ol: ({ children }: any) => <ol style={{ margin: "8px 0", paddingLeft: "20px" }}>{children}</ol>,
      li: ({ children }: any) => <li style={{ margin: "4px 0", lineHeight: "1.6" }}>{children}</li>,
      blockquote: ({ children }: any) => (
        <blockquote
          style={{
            borderLeft: "3px solid rgba(0, 0, 0, 0.2)",
            margin: "8px 0",
            paddingLeft: "12px",
            color: "rgba(0, 0, 0, 0.7)",
          }}
        >
          {children}
        </blockquote>
      ),
      a: ({ href, children }: any) => (
        <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: "#2196f3", textDecoration: "none" }}>
          {children}
        </a>
      ),
      table: ({ children }: any) => (
        <div style={{ overflowX: 'auto', margin: '8px 0' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '13px',
            lineHeight: '1.5',
          }}>
            {children}
          </table>
        </div>
      ),
      thead: ({ children }: any) => (
        <thead style={{ borderBottom: '2px solid rgba(0,0,0,0.15)' }}>
          {children}
        </thead>
      ),
      tbody: ({ children }: any) => <tbody>{children}</tbody>,
      tr: ({ children }: any) => (
        <tr style={{ borderBottom: '1px solid rgba(0,0,0,0.08)' }}>
          {children}
        </tr>
      ),
      th: ({ children }: any) => (
        <th style={{
          padding: '8px 12px',
          textAlign: 'left',
          fontWeight: 600,
          whiteSpace: 'nowrap',
        }}>
          {children}
        </th>
      ),
      td: ({ children }: any) => (
        <td style={{
          padding: '8px 12px',
          verticalAlign: 'top',
        }}>
          {children}
        </td>
      ),
    }),
    []
  );

  return (
    <div
      className={className}
      style={{
        fontSize: "14px",
        lineHeight: "1.6",
        wordBreak: "break-all",
        maxWidth: "100%",
        ...style,
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={components}>
        {processedContent}
      </ReactMarkdown>
      {isStreaming && (
        <span
          className="streaming-cursor"
          style={{
            display: "inline-block",
            width: "2px",
            height: "1em",
            background: "currentColor",
            animation: "blink 1s infinite",
            marginLeft: "2px",
            verticalAlign: "text-bottom",
          }}
        />
      )}
    </div>
  );
};

export default Markdown;
