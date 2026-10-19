import type { ApiKeys } from "../types";
import type { WebSocketConnectionState } from "../hooks/useWebSocket";

/**
 * 去除 Markdown 格式符号，返回纯文本（用于 TTS 播放）
 */
export function stripMarkdown(text: string): string {
  if (!text) return "";
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/_(.+?)_/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/!\[.*?\]\(.+?\)/g, '[图片]')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*>\s+/gm, '')
    .replace(/^\s*[-=]{3,}\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 根据提供商名称获取对应的 API Key
 */
export function getApiKey(apiKeys: ApiKeys, provider: string): string {
  const keyMap: Record<string, string> = {
    modelscope: apiKeys.modelscope,
    siliconflow: apiKeys.siliconflow,
    dashscope: apiKeys.dashscope,
    xiaomi: apiKeys.xiaomi,
    zhipu: apiKeys.zhipu,
    sensenova: apiKeys.sensenova,
    deepseek: apiKeys.deepseek,
  };
  return keyMap[provider] || "";
}

/**
 * 获取 WebSocket 连接状态的中文标签
 */
export function getWsStateLabel(state: WebSocketConnectionState): string {
  switch (state) {
    case "connecting":
      return "连接中";
    case "connected":
      return "已连接";
    case "reconnecting":
      return "重连中";
    case "closed":
      return "已关闭";
    case "error":
      return "连接异常";
    default:
      return "未连接";
  }
}
