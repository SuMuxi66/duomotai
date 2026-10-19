import { createContext, useContext, useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Msg, ChatSessionSummary } from "../types";
import { usePersistedState } from "../hooks/usePersistedState";
import { STORAGE_KEYS } from "../lib/storage";

const API_BASE = "";
const DEFAULT_SESSION_ID = "default";

interface ChatContextValue {
  messages: Msg[];
  input: string;
  partialStt: string;
  activeSessionId: string;
  historySessions: ChatSessionSummary[];
  isHistoryDialogOpen: boolean;
  isHistoryLoading: boolean;
  historySearchQuery: string;
  setInput: (v: string) => void;
  setPartialStt: (v: string) => void;
  setMessages: React.Dispatch<React.SetStateAction<Msg[]>>;
  setActiveSessionId: (v: string) => void;
  setHistorySessions: React.Dispatch<React.SetStateAction<ChatSessionSummary[]>>;
  setIsHistoryDialogOpen: (v: boolean) => void;
  setIsHistoryLoading: (v: boolean) => void;
  setHistorySearchQuery: (v: string) => void;
  createNewSession: () => Promise<void>;
  fetchHistorySessions: (query?: string) => Promise<ChatSessionSummary[]>;
  openHistoryDialog: () => Promise<void>;
  closeHistoryDialog: () => void;
  selectHistorySession: (sessionId: string) => void;
  renameHistorySession: (sessionId: string) => Promise<void>;
  deleteHistorySession: (sessionId: string) => Promise<void>;
  loadMessagesFromBackend: (retries?: number, delay?: number) => Promise<boolean>;
  saveMessagesToBackend: (msgs: Msg[]) => Promise<void>;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [partialStt, setPartialStt] = useState("");
  const [activeSessionId, setActiveSessionId] = usePersistedState(STORAGE_KEYS.activeSessionId, DEFAULT_SESSION_ID);
  const [historySessions, setHistorySessions] = useState<ChatSessionSummary[]>([]);
  const [isHistoryDialogOpen, setIsHistoryDialogOpen] = useState(false);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [historySearchQuery, setHistorySearchQuery] = useState("");
  const persistTimerRef = useRef<number | null>(null);
  const lastSavedMessagesRef = useRef("");

  const createNewSession = useCallback(async () => {
    const nextSessionId = `session_${Date.now()}`;
    const emptyMessages: Msg[] = [];
    setMessages(emptyMessages);
    setInput("");
    setPartialStt("");
    try {
      await fetch(`${API_BASE}/chat/history/${nextSessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: emptyMessages })
      });
      localStorage.setItem(`multimodal_messages_${nextSessionId}`, JSON.stringify(emptyMessages));
      localStorage.setItem(STORAGE_KEYS.activeSessionId, JSON.stringify(nextSessionId));
    } catch (e) {
      console.error('[ChatHistory] 新建会话失败:', e);
    }
    setActiveSessionId(nextSessionId);
  }, [setActiveSessionId]);

  const fetchHistorySessions = useCallback(async (query = "") => {
    setIsHistoryLoading(true);
    try {
      const normalizedQuery = query.trim();
      const url = normalizedQuery
        ? `${API_BASE}/chat/history?q=${encodeURIComponent(normalizedQuery)}`
        : `${API_BASE}/chat/history`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
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
      return [];
    } finally {
      setIsHistoryLoading(false);
    }
  }, []);

  const openHistoryDialog = useCallback(async () => {
    setIsHistoryDialogOpen(true);
    await fetchHistorySessions(historySearchQuery);
  }, [fetchHistorySessions, historySearchQuery]);

  const closeHistoryDialog = useCallback(() => {
    setIsHistoryDialogOpen(false);
  }, []);

  const selectHistorySession = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId);
    setMessages([]);
    setInput("");
    setPartialStt("");
    setIsHistoryDialogOpen(false);
  }, [setActiveSessionId]);

  const renameHistorySession = useCallback(async (sessionId: string) => {
    const currentSession = historySessions.find((s) => s.session_id === sessionId);
    const nextTitle = window.prompt("请输入新的对话名称", currentSession?.title || "");
    if (nextTitle === null) return;
    try {
      const response = await fetch(`${API_BASE}/chat/history/${sessionId}/rename`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: nextTitle.trim() })
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await fetchHistorySessions(historySearchQuery);
    } catch (error) {
      console.error("[ChatHistory] 重命名会话失败:", error);
    }
  }, [historySessions, fetchHistorySessions, historySearchQuery]);

  const deleteHistorySession = useCallback(async (sessionId: string) => {
    const currentSession = historySessions.find((s) => s.session_id === sessionId);
    const confirmed = window.confirm(`确定删除"${currentSession?.title || sessionId}"吗？`);
    if (!confirmed) return;
    try {
      const response = await fetch(`${API_BASE}/chat/history/${sessionId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
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
      }
      await fetchHistorySessions(historySearchQuery);
    } catch (error) {
      console.error("[ChatHistory] 删除会话失败:", error);
    }
  }, [activeSessionId, historySessions, fetchHistorySessions, historySearchQuery, setActiveSessionId]);

  const loadMessagesFromBackend = useCallback(async (retries = 3, delay = 1000) => {
    const storageKey = `multimodal_messages_${activeSessionId}`;
    const loadFromLocal = (): Msg[] => {
      try {
        const stored = localStorage.getItem(storageKey);
        if (stored) {
          const msgs = JSON.parse(stored);
          if (Array.isArray(msgs) && msgs.length > 0) return msgs;
        }
      } catch (e) { console.error('[ChatHistory] 读取本地存储失败:', e); }
      return [];
    };
    setMessages(loadFromLocal());
    for (let i = 0; i < retries; i++) {
      try {
        const resp = await fetch(`${API_BASE}/chat/history/${activeSessionId}`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
            localStorage.setItem(storageKey, JSON.stringify(data.messages));
            return true;
          }
        }
      } catch (e) { console.error(`[ChatHistory] 加载失败 (尝试 ${i + 1}):`, e); }
      if (i < retries - 1) await new Promise(resolve => setTimeout(resolve, delay));
    }
    return false;
  }, [activeSessionId]);

  const saveMessagesToBackend = useCallback(async (msgs: Msg[]) => {
    const storageKey = `multimodal_messages_${activeSessionId}`;
    try { localStorage.setItem(storageKey, JSON.stringify(msgs)); } catch (e) { console.error('[ChatHistory] 保存到本地存储失败:', e); }
    try {
      await fetch(`${API_BASE}/chat/history/${activeSessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs })
      });
    } catch (e) { console.error('[ChatHistory] 保存到后端失败:', e); }
  }, [activeSessionId]);

  useEffect(() => {
    loadMessagesFromBackend();
  }, [loadMessagesFromBackend]);

  useEffect(() => {
    const serialized = JSON.stringify(messages);
    if (serialized === lastSavedMessagesRef.current) return;
    if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
    persistTimerRef.current = window.setTimeout(() => {
      saveMessagesToBackend(messages);
      lastSavedMessagesRef.current = serialized;
      persistTimerRef.current = null;
    }, 300);
    return () => { if (persistTimerRef.current) clearTimeout(persistTimerRef.current); };
  }, [messages, saveMessagesToBackend]);

  const contextValue = useMemo(() => ({
    messages, input, partialStt, activeSessionId, historySessions,
    isHistoryDialogOpen, isHistoryLoading, historySearchQuery,
    setInput, setPartialStt, setMessages, setActiveSessionId,
    setHistorySessions, setIsHistoryDialogOpen, setIsHistoryLoading, setHistorySearchQuery,
    createNewSession, fetchHistorySessions, openHistoryDialog, closeHistoryDialog,
    selectHistorySession, renameHistorySession, deleteHistorySession,
    loadMessagesFromBackend, saveMessagesToBackend,
  }), [
    messages, input, partialStt, activeSessionId, historySessions,
    isHistoryDialogOpen, isHistoryLoading, historySearchQuery,
    setInput, setPartialStt, setMessages, setActiveSessionId,
    setHistorySessions, setIsHistoryDialogOpen, setIsHistoryLoading, setHistorySearchQuery,
    createNewSession, fetchHistorySessions, openHistoryDialog, closeHistoryDialog,
    selectHistorySession, renameHistorySession, deleteHistorySession,
    loadMessagesFromBackend, saveMessagesToBackend,
  ]);

  return (
    <ChatContext.Provider value={contextValue}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
