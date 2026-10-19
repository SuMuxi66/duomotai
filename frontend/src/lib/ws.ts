export type Envelope = { type: string; payload: any };

import { STORAGE_KEYS } from "./storage";

type WSClientLifecycleHandlers = {
  onConnecting?: (isReconnect: boolean) => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent, manualClose: boolean) => void;
  onError?: (error: Event) => void;
  onReconnectScheduled?: (attempt: number, delay: number) => void;
};

/**
 * 获取后端 WebSocket URL
 * 
 * 开发环境：使用相对路径 /ws，让 Vite 代理处理
 * 生产环境：使用完整的后端地址
 */
export function getBackendUrl(apiBase: string = "", attachSession = false): string {
  let url: string;
  try {
    // 如果提供了 apiBase，使用它
    if (apiBase) {
      const normalizedBase = apiBase.endsWith("/") ? apiBase.slice(0, -1) : apiBase;
      const parsed = new URL(normalizedBase);
      const protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
      url = `${protocol}//${parsed.host}/ws`;
    } else {
      // 开发环境：使用相对路径，让 Vite 代理处理
      // 这样可以避免浏览器的自签名证书问题
      const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = typeof window !== "undefined" ? window.location.host : "localhost:5179";
      url = `${protocol}//${host}/ws`;
    }
  } catch (e) {
    console.error('[WS] 获取后端 URL 失败:', e);
    // 降级到相对路径
    const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = typeof window !== "undefined" ? window.location.host : "localhost:5179"; 
    url = `${protocol}//${host}/ws`;
  }

  // 附加当前会话 ID，让后端按会话恢复 AI 记忆
  if (attachSession) {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.activeSessionId);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (typeof parsed === "string" && parsed && parsed !== "default") {
          const sep = url.includes("?") ? "&" : "?";
          url += `${sep}session_id=${encodeURIComponent(parsed)}`;
        }
      }
    } catch (e) {
      console.error('[WS] 读取会话 ID 失败:', e);
    }
  }
  return url;
}

const MESSAGE_QUEUE_MAX_SIZE = 100;

export class WSClient {
  ws: WebSocket | null = null;
  onEvent: (evt: Envelope) => void;
  handlers?: WSClientLifecycleHandlers;
  messageQueue: Array<{ type: string; payload: any }> = [];
  url = "";
  reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  reconnectAttempts = 0;
  maxReconnectAttempts = 10;
  reconnectDelay = 2000;
  manualClose = false;
  hasConnectedOnce = false;
  destroyed = false;

  constructor(onEvent: (evt: Envelope) => void, handlers?: WSClientLifecycleHandlers) {
    this.onEvent = onEvent;
    this.handlers = handlers;
    this.destroyed = false;
  }

  setHandlers(handlers?: WSClientLifecycleHandlers) {
    this.handlers = handlers;
  }

  setOnEvent(onEvent: (evt: Envelope) => void) {
    this.onEvent = onEvent;
  }

  connect(url: string) {
    if (this.destroyed) return;
    this.url = url;
    this.manualClose = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const readyState = this.ws?.readyState;
    if (readyState === WebSocket.OPEN || readyState === WebSocket.CONNECTING) {
      return;
    }
    this.reconnectAttempts = 0;
    this.handlers?.onConnecting?.(this.hasConnectedOnce);
    this._connect();
  }

  private _connect() {
    try {
      // 关闭旧的 socket（如果存在且不在 OPEN/CONNECTING 状态）
      if (this.ws) {
        const oldReadyState = this.ws.readyState;
        if (oldReadyState === WebSocket.OPEN || oldReadyState === WebSocket.CONNECTING) {
          return; // 已有活跃连接，不重复创建
        }
        // 清理旧 socket 的事件处理器
        this.ws.onopen = null;
        this.ws.onmessage = null;
        this.ws.onerror = null;
        this.ws.onclose = null;
        try {
          this.ws.close();
        } catch (_) {
          // 忽略关闭错误
        }
      }

      const socket = new WebSocket(this.url);
      this.ws = socket;
      socket.onopen = () => {
        if (this.ws !== socket) {
          return;
        }
        this.hasConnectedOnce = true;
        this.reconnectAttempts = 0;
        this.flushMessageQueue();
        this.handlers?.onOpen?.();
      };
      socket.onmessage = (m) => {
        if (this.ws !== socket) {
          return;
        }
        try {
          const evt = JSON.parse(m.data);
          this.onEvent(evt);
        } catch (e) {
          console.error("[WS] 消息解析失败:", e, m.data);
        }
      };
      socket.onerror = (error) => {
        if (this.ws !== socket) {
          return;
        }
        console.error("[WS] 后端连接错误:", error);
        this.handlers?.onError?.(error);
      };
      socket.onclose = (event) => {
        if (this.ws === socket) {
          this.ws = null;
        }
        console.log(`[WS] 连接关闭: code=${event.code}, reason=${event.reason}`);
        this.handlers?.onClose?.(event, this.manualClose);
        if (!this.manualClose) {
          this._scheduleReconnect();
        }
      };
    } catch (e) {
      console.error("[WS] 连接异常:", e);
      if (!this.manualClose) {
        this._scheduleReconnect();
      }
    }
  }

  private _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error("[WS] 达到最大重连次数，停止重连");
      return;
    }
    if (this.reconnectTimer) {
      return;
    }
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1);
    this.handlers?.onReconnectScheduled?.(this.reconnectAttempts, delay);

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._connect();
    }, delay);
  }

  send(type: string, payload: any) {
    if (this.destroyed) return;
    if (!this.ws) {
      console.error("[WS] 未连接，无法发送消息");
      return;
    }

    if (this.ws.readyState !== WebSocket.OPEN) {
      if (this.messageQueue.length >= MESSAGE_QUEUE_MAX_SIZE) {
        const dropped = this.messageQueue.shift();
        console.warn("[WS] 消息队列已满，丢弃最旧消息:", dropped);
      }
      this.messageQueue.push({ type, payload });
      return;
    }

    this.ws.send(JSON.stringify({ type, payload }));
  }

  flushMessageQueue() {
    if (this.destroyed) return;
    const failed: Array<{ type: string; payload: any }> = [];
    while (this.messageQueue.length > 0) {
      const message = this.messageQueue.shift();
      if (message && this.ws && this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify(message));
        } catch {
          // 发送失败，保留消息等待重连后重试
          failed.push(message);
        }
      } else {
        // 连接不可用，保留消息
        if (message) failed.push(message);
      }
    }
    // 将发送失败的消息放回队列头部
    if (failed.length > 0) {
      this.messageQueue.unshift(...failed);
    }
  }

  close() {
    // 这里是明确的主动关闭，避免 cleanup 触发自动重连和假断线状态
    this.manualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      // 清理事件处理器，防止旧连接触发回调
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      const readyState = this.ws.readyState;
      // CONNECTING 阶段直接 close 会在开发态产生"建立前关闭"的噪声
      // 这里只断开引用，让这条 socket 自然结束
      if (readyState === WebSocket.OPEN || readyState === WebSocket.CONNECTING) {
        try {
          this.ws.close();
        } catch (_) {}
      }
      this.ws = null;
    }
    // 清空消息队列，防止重连时发送过期消息
    this.messageQueue = [];
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  isConnecting(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.CONNECTING;
  }

  getReadyState(): number {
    return this.ws ? this.ws.readyState : -1;
  }

  destroy() {
    this.destroyed = true;
    this.close();
    this.onEvent = () => {};
    this.handlers = undefined;
    this.messageQueue = [];
  }
}
