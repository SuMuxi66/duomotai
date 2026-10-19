import { useState, useCallback, useEffect } from "react";
import { usePersistedState } from "./usePersistedState";
import { STORAGE_KEYS } from "../lib/storage";
import { API_BASE } from "../lib/constants";
import type { Msg, ChatSessionSummary, AIResponseState } from "../types";

const DEFAULT_SESSION_ID = "default";

export interface UseSessionManagerOptions {
  /** 打断当前 AI 响应（用于切换会话时清理状态） */
  sendInterrupt: () => void;
  /** 设置消息列表 */
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>;
  /** 设置输入框文本 */
  setInput: React.Dispatch<React.SetStateAction<string>>;
  /** 设置部分 STT 文本 */
  setPartialStt: React.Dispatch<React.SetStateAction<string>>;
  /** 设置 AI 响应状态 */
  setAiResponseState: React.Dispatch<React.SetStateAction<AIResponseState>>;
}

export interface UseSessionManagerReturn {
  activeSessionId: string;
  setActiveSessionId: React.Dispatch<React.SetStateAction<string>>;
  historySessions: ChatSessionSummary[];
  isHistoryDialogOpen: boolean;
  isHistoryLoading: boolean;
  historySearchQuery: string;
  setHistorySearchQuery: React.Dispatch<React.SetStateAction<string>>;
  createNewSession: () => Promise<void>;
  fetchHistorySessions: (query?: string) => Promise<ChatSessionSummary[]>;
  openHistoryDialog: () => Promise<void>;
  closeHistoryDialog: () => void;
  selectHistorySession: (sessionId: string) => void;
  renameHistorySession: (sessionId: string) => Promise<void>;
  deleteHistorySession: (sessionId: string) => Promise<void>;
}

/**
 * 会话管理逻辑
 * - 管理当前活跃会话 ID
 * - 管理历史会话列表、搜索、对话框状态
 * - 提供新建/重命名/删除/切换会话等函数
 */
export function useSessionManager(options: UseSessionManagerOptions): UseSessionManagerReturn {
  const { sendInterrupt, setMessages, setInput, setPartialStt, setAiResponseState } = options;

  const [activeSessionId, setActiveSessionId] = usePersistedState<string>(
    STORAGE_KEYS.activeSessionId,
    DEFAULT_SESSION_ID
  );
  const [historySessions, setHistorySessions] = useState<ChatSessionSummary[]>([]);
  const [isHistoryDialogOpen, setIsHistoryDialogOpen] = useState(false);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [historySearchQuery, setHistorySearchQuery] = useState("");

  const createNewSession = useCallback(async () => {
    const nextSessionId = `session_${Date.now()}`;
    const emptyMessages: Msg[] = [];

    sendInterrupt();
    setAiResponseState('idle');
    setInput("");
    setPartialStt("");
    setMessages(emptyMessages);

    try {
      await fetch(`${API_BASE}/chat/history/${nextSessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: emptyMessages })
      });
    } catch (e) {
      console.error('[ChatHistory] 新建会话失败:', e);
    }

    try {
      localStorage.setItem(`multimodal_messages_${nextSessionId}`, JSON.stringify(emptyMessages));
      localStorage.setItem(STORAGE_KEYS.activeSessionId, JSON.stringify(nextSessionId));
    } catch (e) {
      console.error('[ChatHistory] 初始化本地会话失败:', e);
    }

    setActiveSessionId(nextSessionId);
  }, [sendInterrupt, setAiResponseState, setInput, setPartialStt, setMessages, setActiveSessionId]);

  const fetchHistorySessions = useCallback(async (query = "") => {
    setIsHistoryLoading(true);
    try {
      const normalizedQuery = query.trim();
      const url = normalizedQuery
        ? `${API_BASE}/chat/history?q=${encodeURIComponent(normalizedQuery)}`
        : `${API_BASE}/chat/history`;
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      const sessions = Array.isArray(data.sessions) ? data.sessions : [];
      sessions.sort((a: ChatSessionSummary, b: ChatSessionSummary) => {
        const timeA = new Date(a.updated_at || a.created_at || 0).getTime();
        const timeB = new Date(b.updated_at || b.created_at || 0).getTime();
        return timeB - timeA;
      });
      setHistorySessions(sessions);
      return sessions;
    } catch (error) {
      console.error('[ChatHistory] 加载历史会话列表失败:', error);
      setMessages((m) => [
        ...m,
        { role: "system", text: "加载历史对话失败，请稍后重试。", timestamp: Date.now() }
      ]);
      return [];
    } finally {
      setIsHistoryLoading(false);
    }
  }, [setMessages]);

  const openHistoryDialog = useCallback(async () => {
    setIsHistoryDialogOpen(true);
    await fetchHistorySessions(historySearchQuery);
  }, [fetchHistorySessions, historySearchQuery]);

  useEffect(() => {
    if (!isHistoryDialogOpen) return;
    const timer = window.setTimeout(() => {
      fetchHistorySessions(historySearchQuery);
    }, 180);
    return () => {
      clearTimeout(timer);
    };
  }, [fetchHistorySessions, historySearchQuery, isHistoryDialogOpen]);

  const closeHistoryDialog = useCallback(() => {
    setIsHistoryDialogOpen(false);
  }, []);

  const selectHistorySession = useCallback((sessionId: string) => {
    sendInterrupt();
    setActiveSessionId(sessionId);
    setMessages([]);
    setInput("");
    setPartialStt("");
    setAiResponseState('idle');
    setIsHistoryDialogOpen(false);
  }, [sendInterrupt, setActiveSessionId, setMessages, setInput, setPartialStt, setAiResponseState]);

  const renameHistorySession = useCallback(async (sessionId: string) => {
    const currentSession = historySessions.find((session) => session.session_id === sessionId);
    const nextTitle = window.prompt("请输入新的对话名称", currentSession?.title || "");
    if (nextTitle === null) return;
    const normalizedTitle = nextTitle.trim();

    try {
      const response = await fetch(`${API_BASE}/chat/history/${sessionId}/rename`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: normalizedTitle })
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      await fetchHistorySessions(historySearchQuery);
    } catch (error) {
      console.error("[ChatHistory] 重命名会话失败:", error);
      setMessages((m) => [
        ...m,
        { role: "system", text: "重命名历史对话失败，请稍后重试。", timestamp: Date.now() }
      ]);
    }
  }, [fetchHistorySessions, historySearchQuery, historySessions, setMessages]);

  const deleteHistorySession = useCallback(async (sessionId: string) => {
    const currentSession = historySessions.find((session) => session.session_id === sessionId);
    const confirmed = window.confirm(`确定删除“${currentSession?.title || currentSession?.session_id || sessionId}”吗？`);
    if (!confirmed) return;

    try {
      const response = await fetch(`${API_BASE}/chat/history/${sessionId}`, {
        method: "DELETE"
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      localStorage.removeItem(`multimodal_messages_${sessionId}`);

      if (activeSessionId === sessionId) {
        const emptyMessages: Msg[] = [];
        await fetch(`${API_BASE}/chat/history/${DEFAULT_SESSION_ID}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: emptyMessages })
        });
        localStorage.setItem(`multimodal_messages_${DEFAULT_SESSION_ID}`, JSON.stringify(emptyMessages));
        setActiveSessionId(DEFAULT_SESSION_ID);
        setMessages(emptyMessages);
        setInput("");
        setPartialStt("");
        setAiResponseState('idle');
      }

      await fetchHistorySessions(historySearchQuery);
    } catch (error) {
      console.error("[ChatHistory] 删除会话失败:", error);
      setMessages((m) => [
        ...m,
        { role: "system", text: "删除历史对话失败，请稍后重试。", timestamp: Date.now() }
      ]);
    }
  }, [activeSessionId, fetchHistorySessions, historySearchQuery, historySessions, setActiveSessionId, setMessages, setInput, setPartialStt, setAiResponseState]);

  return {
    activeSessionId,
    setActiveSessionId,
    historySessions,
    isHistoryDialogOpen,
    isHistoryLoading,
    historySearchQuery,
    setHistorySearchQuery,
    createNewSession,
    fetchHistorySessions,
    openHistoryDialog,
    closeHistoryDialog,
    selectHistorySession,
    renameHistorySession,
    deleteHistorySession,
  };
}
