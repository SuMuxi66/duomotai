/**
 * ChatPaneInput - 输入区域（PromptInput 风格）
 *
 * 按照 doc/输入框组件.md 的 PromptInput 设计：
 * - 统一圆角容器（rounded-3xl）包裹 textarea + actions
 * - textarea 无边框（容器提供边框）
 * - actions 行：左侧 Paperclip + TTS，右侧 Send/Stop
 * - 文件预览标签
 * - Tooltip 悬浮提示
 */
import { useEffect, useState, useRef, useCallback } from "react";
import { Paperclip, X, Square, ArrowUp, FileText, ImageIcon } from "lucide-react";
import { CookingTimerBadge } from "./CookingTimerBadge";
import type { CookingTimerInfo } from "./CookingTimerBadge";
import type { Theme } from "@/lib/theme";
import { spacing, transition } from "@/styles";

export interface FileData {
  name: string;
  b64: string;
  type: string;
}

export interface ChatPaneInputProps {
  theme: Theme;
  isDark: boolean;
  input: string;
  setInput: (s: string) => void;
  onSendText: (files?: FileData[]) => void;
  onStop?: () => void;
  ttsEnabled: boolean;
  partialStt: string;
  isAiSpeaking?: boolean;
  aiResponseState?: 'idle' | 'thinking' | 'responding';
  isListening?: boolean;
  speechRecognitionState?: 'idle' | 'waiting' | 'listening' | 'analyzing';
  sttPipeline?: 'none' | 'push_to_talk';
  cookingTimers?: CookingTimerInfo[];
  onCancelCookingTimer?: () => void;
}

const INPUT_HISTORY_KEY = "multimodal_input_history";
const MAX_HISTORY = 50;

// 文件上传限制
const MAX_FILES = 5;
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_TOTAL_SIZE = 30 * 1024 * 1024; // 30MB
const ACCEPTED_FILE_TYPES = [
  // 图片
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
  "image/bmp",
  "image/svg+xml",
  // 文档
  "application/pdf",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/json",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/msword",
  "application/vnd.ms-excel",
  "application/vnd.ms-powerpoint",
];

const ACCEPT_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "pdf", "txt", "md", "markdown", "csv", "json", "docx", "xlsx", "pptx", "doc", "xls", "ppt"];
const ACCEPT_EXTENSIONS_STRING = ACCEPT_EXTENSIONS.map((e) => `.${e}`).join(",");

function isAcceptedFile(file: File): boolean {
  if (ACCEPTED_FILE_TYPES.includes(file.type)) return true;
  const ext = file.name.split(".").pop()?.toLowerCase();
  return !!ext && ACCEPT_EXTENSIONS.includes(ext);
}

function loadInputHistory(): string[] {
  try {
    const raw = localStorage.getItem(INPUT_HISTORY_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch { /* ignore */ }
  return [];
}

function saveInputHistory(history: string[]) {
  try {
    localStorage.setItem(INPUT_HISTORY_KEY, JSON.stringify(history.slice(-MAX_HISTORY)));
  } catch { /* ignore */ }
}

export function ChatPaneInput(props: ChatPaneInputProps) {
  const { theme, isDark } = props;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [filePreviews, setFilePreviews] = useState<FileData[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);

  // --- 文件错误提示：3 秒后自动消失 ---
  useEffect(() => {
    if (!fileError) return;
    const timer = setTimeout(() => setFileError(null), 3000);
    return () => clearTimeout(timer);
  }, [fileError]);

  // --- 输入历史：上下方向键切换 ---
  const historyRef = useRef<string[]>(loadInputHistory());
  const historyIndexRef = useRef<number | null>(null);
  const tempInputRef = useRef<string>("");

  // --- 自动伸缩高度 ---
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [props.input]);

  const sttPipelineLabel = props.sttPipeline === 'push_to_talk' ? '单次语音输入STT' : '未启用';
  const sttStateLabel = props.speechRecognitionState === 'waiting'
    ? '等待说话'
    : props.speechRecognitionState === 'listening'
      ? '正在听'
      : props.speechRecognitionState === 'analyzing'
        ? '识别中'
        : props.isListening
          ? '唤醒词模式'
          : '';

  const showSttStatus = !!(props.partialStt || (props.speechRecognitionState && props.speechRecognitionState !== 'idle') || props.isListening);
  const showAiStatus = !!(props.aiResponseState && props.aiResponseState !== 'idle');
  const isResponding = props.aiResponseState !== 'idle' || !!props.isAiSpeaking;

  // --- 将 File 读取为 base64 ---
  const readFileAsBase64 = useCallback((file: File): Promise<FileData> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = (reader.result as string).split(",")[1]; // strip data:...;base64, prefix
        resolve({ name: file.name, b64, type: file.type });
      };
      reader.onerror = () => reject(new Error(`读取文件失败: ${file.name}`));
      reader.readAsDataURL(file);
    });
  }, []);

  // --- 文件上传处理 ---
  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    setFileError(null);
    if (!event.target.files || event.target.files.length === 0) return;

    const selected = Array.from(event.target.files);
    const currentTotalSize = files.reduce((sum, f) => sum + f.size, 0);

    // 数量限制
    if (files.length + selected.length > MAX_FILES) {
      setFileError(`最多同时上传 ${MAX_FILES} 个文件`);
      event.target.value = "";
      return;
    }

    // 类型与大小校验（MIME 类型 + 扩展名双兜底）
    const validFiles: File[] = [];
    for (const file of selected) {
      if (!isAcceptedFile(file)) {
        setFileError(`不支持的文件类型：${file.name}`);
        event.target.value = "";
        return;
      }
      if (file.size > MAX_FILE_SIZE) {
        setFileError(`单个文件不能超过 10MB：${file.name}`);
        event.target.value = "";
        return;
      }
      validFiles.push(file);
    }

    const newTotalSize = currentTotalSize + validFiles.reduce((sum, f) => sum + f.size, 0);
    if (newTotalSize > MAX_TOTAL_SIZE) {
      setFileError(`上传文件总大小不能超过 30MB`);
      event.target.value = "";
      return;
    }

    setFiles((prev) => [...prev, ...validFiles]);
    const newPreviews = await Promise.all(validFiles.map(readFileAsBase64));
    setFilePreviews((prev) => [...prev, ...newPreviews]);
    event.target.value = "";
  };

  const handleRemoveFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
    setFilePreviews((prev) => prev.filter((_, i) => i !== index));
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  // --- 获取当前文件数据用于发送 ---
  const getFilesForSend = useCallback((): FileData[] => {
    return filePreviews;
  }, [filePreviews]);

  // --- 发送后清空文件 ---
  const clearFiles = useCallback(() => {
    setFiles([]);
    setFilePreviews([]);
    setFileError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, []);

  // --- 保存当前输入到历史 ---
  const commitInputToHistory = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const history = historyRef.current;
    // 如果和最新一条相同则不重复添加
    if (history.length > 0 && history[history.length - 1] === trimmed) {
      // 把这条移到最新（刷新时间顺序）
      historyRef.current = [...history.slice(0, -1), trimmed];
    } else {
      historyRef.current = [...history, trimmed];
    }
    saveInputHistory(historyRef.current);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (props.input.trim() || filePreviews.length > 0) {
        commitInputToHistory(props.input);
        historyIndexRef.current = null;
        tempInputRef.current = "";
        const filesToSend = getFilesForSend();
        props.onSendText(filesToSend.length > 0 ? filesToSend : undefined);
        clearFiles();
      }
      return;
    }

    // 上下方向键切换输入历史（命令行风格）
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const history = historyRef.current;
      if (history.length === 0) return;

      if (e.key === "ArrowUp") {
        // 如果还没有进入历史浏览模式：只在输入为空或当前内容已在历史中时进入
        if (historyIndexRef.current === null) {
          const trimmedInput = props.input.trim();
          const inHistoryIndex = history.lastIndexOf(trimmedInput);
          // 输入为空 或 输入正好匹配历史中的某一条，才允许向上浏览
          if (trimmedInput === "" || inHistoryIndex !== -1) {
            tempInputRef.current = props.input;
            // 从匹配位置继续向上，否则从最后一条开始
            const startIndex = inHistoryIndex !== -1
              ? Math.max(0, inHistoryIndex - 1)
              : history.length - 1;
            historyIndexRef.current = startIndex;
            props.setInput(history[startIndex]);
            e.preventDefault();
          }
          return;
        }
        // 已经在历史模式中，继续向上
        const nextIndex = Math.max(0, historyIndexRef.current - 1);
        if (nextIndex !== historyIndexRef.current) {
          historyIndexRef.current = nextIndex;
          props.setInput(history[nextIndex]);
        }
        e.preventDefault();
        return;
      }

      // ArrowDown
      if (historyIndexRef.current !== null) {
        const nextIndex = historyIndexRef.current + 1;
        if (nextIndex >= history.length) {
          // 已经到最新历史，再按 ↓ 回到临时输入（没有则清空）
          historyIndexRef.current = null;
          props.setInput(tempInputRef.current);
        } else {
          historyIndexRef.current = nextIndex;
          props.setInput(history[nextIndex]);
        }
        e.preventDefault();
        return;
      }
    }

    // 用户开始输入新内容（非上下键）时，如果当前在历史模式中且输入被改动，
    // 则退出历史模式并把当前输入作为临时输入保留
    if (historyIndexRef.current !== null) {
      // 这里不立即 reset，让输入继续；当后续按上时再根据当前 input 判断是否进入
      // 但为了用户体验，如果按下普通字符键，应该退出历史模式并保留当前编辑
      // 该逻辑在 onChange 中处理
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    props.setInput(newValue);

    // 如果当前在历史模式中，且新值不等于当前历史项，则退出历史模式
    if (historyIndexRef.current !== null) {
      const currentHistoryItem = historyRef.current[historyIndexRef.current];
      if (newValue !== currentHistoryItem) {
        historyIndexRef.current = null;
        tempInputRef.current = newValue;
      }
    }
  };

  // --- Tooltip 组件（纯 inline） ---
  const TooltipWrap = ({
    tip,
    children,
  }: {
    tip: string;
    children: React.ReactNode;
  }) => {
    const [visible, setVisible] = useState(false);
    return (
      <span
        style={{ position: "relative", display: "inline-flex" }}
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
      >
        {children}
        {visible && (
          <span
            style={{
              position: "absolute",
              bottom: "calc(100% + 6px)",
              left: "50%",
              transform: "translateX(-50%)",
              background: isDark ? "#333" : "#1f2937",
              color: "#fff",
              fontSize: 12,
              padding: "4px 8px",
              borderRadius: 6,
              whiteSpace: "nowrap",
              zIndex: 100,
              pointerEvents: "none",
            }}
          >
            {tip}
          </span>
        )}
      </span>
    );
  };

  return (
    <>
      {/* 倒计时显示 */}
      {props.cookingTimers && props.cookingTimers.length > 0 && (
        <CookingTimerBadge
          timers={props.cookingTimers}
          onCancel={props.onCancelCookingTimer}
          theme={theme}
          ttsEnabled={props.ttsEnabled}
        />
      )}

      {/* STT / AI 状态指示器 */}
      {(showSttStatus || showAiStatus) && (
        <div
          style={{
            padding: "8px 14px",
            marginBottom: "10px",
            background:
              "linear-gradient(135deg, rgba(106, 159, 181, 0.08), rgba(196, 164, 132, 0.05))",
            border: `1px solid ${theme.borderLight}`,
            borderRadius: 16,
            display: "flex",
            flexDirection: "column",
            gap: 6,
            animation: "fade-in 0.3s ease-out",
          }}
        >
          {showSttStatus && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background:
                    props.speechRecognitionState === "analyzing"
                      ? theme.secondary
                      : theme.primary,
                  animation: "blink 1.5s ease-in-out infinite",
                }}
              />
              <span
                style={{
                  fontSize: 13, color: theme.textSecondary,
                  fontStyle: "italic", flex: 1,
                }}
              >
                {`STT状态（${sttPipelineLabel}）：${sttStateLabel}`}
              </span>
            </div>
          )}
          {showAiStatus && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: theme.secondary,
                  animation: "blink 1.5s ease-in-out infinite",
                }}
              />
              <span
                style={{
                  fontSize: 13, color: theme.textSecondary,
                  fontStyle: "italic", flex: 1,
                }}
              >
                {`AI状态：${props.aiResponseState === "thinking" ? "分析输入中" : "回复生成中"}`}
              </span>
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════ */}
      {/* PromptInput 风格容器                    */}
      {/* border + rounded-3xl(24px) + p-2(8px)  */}
      {/* ════════════════════════════════════════ */}
      <div
        style={{
          margin: "0 12px 12px",
          border: `1px solid ${theme.inputBorder}`,
          borderRadius: 24,
          padding: 8,
          background: theme.inputBg,
          boxShadow: isDark ? "0 4px 14px rgba(0,0,0,0.28)" : "0 2px 8px rgba(0,0,0,0.06)",
          transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
        }}
        onFocusCapture={() => {
          // 容器整体高亮
        }}
      >
        {/* 文件预览标签 */}
        {files.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              paddingBottom: 8,
            }}
          >
            {files.map((file, index) => {
              const isImage = file.type.startsWith("image/");
              return (
                <div
                  key={index}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    background: isDark
                      ? "rgba(255,255,255,0.08)"
                      : "rgba(0,0,0,0.05)",
                    borderRadius: 8,
                    padding: "4px 8px",
                    fontSize: 12,
                    color: theme.textSecondary,
                  }}
                >
                  {isImage && filePreviews[index] ? (
                    <img
                      src={`data:${file.type};base64,${filePreviews[index].b64}`}
                      alt={file.name}
                      style={{
                        width: 20,
                        height: 20,
                        borderRadius: 4,
                        objectFit: "cover",
                      }}
                    />
                  ) : file.type === "application/pdf" ? (
                    <FileText size={14} />
                  ) : (
                    <ImageIcon size={14} />
                  )}
                  <span
                    style={{
                      maxWidth: 120,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {file.name}
                  </span>
                  <button
                    onClick={() => handleRemoveFile(index)}
                    style={{
                      width: 18,
                      height: 18,
                      borderRadius: "50%",
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: theme.textMuted,
                      padding: 0,
                    }}
                  >
                    <X size={12} />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* 文件错误提示 */}
        {fileError && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 10px",
              marginBottom: 8,
              borderRadius: 8,
              background: isDark ? "rgba(239, 68, 68, 0.15)" : "rgba(239, 68, 68, 0.08)",
              color: theme.error,
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <span>⚠️</span>
            <span style={{ flex: 1 }}>{fileError}</span>
            <button
              type="button"
              onClick={() => setFileError(null)}
              aria-label="关闭提示"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 18,
                height: 18,
                borderRadius: "50%",
                border: "none",
                background: "transparent",
                color: "inherit",
                cursor: "pointer",
                padding: 0,
                marginLeft: 2,
              }}
            >
              <X size={12} />
            </button>
          </div>
        )}

        {/* Textarea — 无边框，透明背景 */}
        <textarea
          ref={textareaRef}
          value={props.input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={
            props.partialStt ? "识别中，请继续说话..." : "输入消息，Enter 发送..."
          }
          rows={1}
          aria-label="输入消息"
          style={{
            width: "100%",
            minHeight: 44,
            maxHeight: 120,
            padding: `${spacing.sm}px ${spacing.sm}px`,
            background: "transparent",
            color: theme.inputText,
            border: "none",
            borderRadius: 0,
            fontSize: 14,
            lineHeight: 1.5,
            resize: "none",
            outline: "none",
            fontFamily: "inherit",
            boxSizing: "border-box",
            overflowY: "auto",
            transition: `height ${transition.fast}`,
          }}
        />

        {/* Actions 行 — justify-between */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
            paddingTop: 8,
          }}
        >
          {/* 左侧 actions */}
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {/* Paperclip 文件上传 */}
            <TooltipWrap tip="附加文件">
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 32,
                  height: 32,
                  borderRadius: "50%",
                  cursor: "pointer",
                  color: theme.textMuted,
                  transition: `background ${transition.fast}, color ${transition.fast}`,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = theme.borderLight;
                  e.currentTarget.style.color = theme.textSecondary;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.color = theme.textMuted;
                }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept={ACCEPT_EXTENSIONS_STRING}
                  onChange={handleFileChange}
                  style={{ display: "none" }}
                  aria-label="附加文件"
                />
                <Paperclip size={18} />
              </label>
            </TooltipWrap>
          </div>

          {/* 右侧 actions */}
          {isResponding ? (
            <TooltipWrap tip={props.aiResponseState === 'idle' ? "停止语音" : "停止生成"}>
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
                  boxShadow: "0 4px 12px rgba(239,68,68,0.3)",
                  animation: "pulse-stop 2s ease-in-out infinite",
                  transition: `transform ${transition.fast}`,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = "scale(1.1)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "scale(1)";
                }}
              >
                <Square size={14} fill="currentColor" />
              </button>
            </TooltipWrap>
          ) : (
            <TooltipWrap tip="发送消息">
              <button
                type="button"
                onClick={() => {
                  const filesToSend = getFilesForSend();
                  props.onSendText(filesToSend.length > 0 ? filesToSend : undefined);
                  clearFiles();
                }}
                disabled={!props.input.trim() && filePreviews.length === 0}
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
                  cursor: props.input.trim() || filePreviews.length > 0 ? "pointer" : "not-allowed",
                  opacity: props.input.trim() || filePreviews.length > 0 ? 1 : 0.5,
                  transition: `transform ${transition.fast}, opacity ${transition.fast}`,
                }}
                onMouseEnter={(e) => {
                  if (props.input.trim() || filePreviews.length > 0) {
                    e.currentTarget.style.transform = "scale(1.1)";
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "scale(1)";
                }}
              >
                <ArrowUp size={18} />
              </button>
            </TooltipWrap>
          )}
        </div>
      </div>
    </>
  );
}