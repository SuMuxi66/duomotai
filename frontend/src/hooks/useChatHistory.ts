import { useState, useEffect, useCallback, useRef } from "react";
import { Msg } from "../types";

const SESSION_ID = "default";

export interface UseChatHistoryReturn {
  messages: Msg[];
  addMessage: (message: Msg) => void;
  updateLastMessage: (text: string) => void;
  clearMessages: () => void;
  loadMessages: () => Promise<void>;
}

export interface UseChatHistoryOptions {
  apiBase: string;
}

export function useChatHistory(options: UseChatHistoryOptions): UseChatHistoryReturn {
  const { apiBase } = options;

  const [messages, setMessages] = useState<Msg[]>([]);
  const persistTimerRef = useRef<number | null>(null);

  const storageKey = `multimodal_messages_${SESSION_ID}`;

  const loadFromLocalStorage = useCallback((): Msg[] => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const msgs = JSON.parse(stored);
        if (Array.isArray(msgs) && msgs.length > 0) {
          console.log(`[ChatHistory] 从本地存储加载 ${msgs.length} 条消息`);
          return msgs;
        }
      }
    } catch (e) {
      console.error('[ChatHistory] 读取本地存储失败:', e);
    }
    return [];
  }, [storageKey]);

  const saveToLocalStorage = useCallback((msgs: Msg[]) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(msgs));
    } catch (e) {
      console.error('[ChatHistory] 保存到本地存储失败:', e);
    }
  }, [storageKey]);

  const loadMessages = useCallback(async () => {
    setMessages(loadFromLocalStorage());

    for (let i = 0; i < 3; i++) {
      try {
        console.log(`[ChatHistory] 正在加载聊天记录 (尝试 ${i + 1}/3)`);
        // 使用相对路径，让 Vite 代理处理请求
        const resp = await fetch(`/chat/history/${SESSION_ID}`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
            saveToLocalStorage(data.messages);
            return;
          } else {
            const localMsgs = loadFromLocalStorage();
            if (localMsgs.length > 0) {
              setMessages(localMsgs);
            }
            return;
          }
        }
      } catch (e) {
        console.error(`[ChatHistory] 加载失败 (尝试 ${i + 1}):`, e);
      }

      if (i < 2) {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }

    console.warn('[ChatHistory] 聊天记录加载失败，使用本地缓存');
    const localMsgs = loadFromLocalStorage();
    if (localMsgs.length > 0) {
      setMessages(localMsgs);
    }
  }, [apiBase, loadFromLocalStorage, saveToLocalStorage]);

  const saveMessagesToBackend = useCallback(async (msgs: Msg[]) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(msgs));
    } catch (e) {
      console.error('[ChatHistory] 保存到本地存储失败:', e);
    }

    try {
      // 使用相对路径，让 Vite 代理处理请求
      await fetch(`/chat/history/${SESSION_ID}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs })
      });
    } catch (e) {
      console.error('[ChatHistory] 保存到后端失败:', e);
    }
  }, [apiBase, storageKey]);

  const addMessage = useCallback((message: Msg) => {
    setMessages(prev => [...prev, message]);
  }, []);

  const updateLastMessage = useCallback((text: string) => {
    setMessages(prev => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant_stream") {
        const newMessages = [...prev];
        newMessages[newMessages.length - 1] = { ...last, text };
        return newMessages;
      }
      return prev;
    });
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    localStorage.removeItem(storageKey);
  }, [storageKey]);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  // 缓存上次持久化的消息摘要，避免重复序列化相同数据
  const lastPersistedHashRef = useRef<string>("");

  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last?.role === "assistant_stream") return;

    // 快速diff：比较最后一条消息的role+text长度，避免频繁JSON.stringify
    const quickHash = `${messages.length}:${last.role}:${last.text?.length ?? 0}`;
    if (quickHash === lastPersistedHashRef.current) return;
    lastPersistedHashRef.current = quickHash;

    if (persistTimerRef.current) {
      clearTimeout(persistTimerRef.current);
    }

    persistTimerRef.current = window.setTimeout(() => {
      saveMessagesToBackend(messages);
      persistTimerRef.current = null;
    }, 250);

    return () => {
      if (persistTimerRef.current) {
        clearTimeout(persistTimerRef.current);
        persistTimerRef.current = null;
      }
    };
  }, [messages, saveMessagesToBackend]);

  return {
    messages,
    addMessage,
    updateLastMessage,
    clearMessages,
    loadMessages
  };
}
