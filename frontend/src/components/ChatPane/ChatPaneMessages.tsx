/**
 * ChatPaneMessages - 消息列表区域
 *
 * 空状态展示 + 虚拟列表渲染。
 */
import { CustomIcon } from "@/components/CustomIcon";
import { VirtualMessageList } from "@/components/VirtualMessageList";
import type { Theme } from "@/lib/theme";
import type { Msg } from "@/types";

export interface ChatPaneMessagesProps {
  messages: Msg[];
  theme: Theme;
}

export function ChatPaneMessages({ messages, theme }: ChatPaneMessagesProps) {
  return (
    <div style={{
      flex: 1,
      minHeight: 0,
      padding: '24px',
      display: 'flex',
      flexDirection: 'column'
    }}>
      {messages.length === 0 ? (
        <div style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center'
        }}>
          <div style={{ textAlign: 'center', color: theme.textMuted }}>
            <div style={{
              fontSize: '64px',
              marginBottom: '20px',
              opacity: 0.6,
              animation: 'float 3s ease-in-out infinite'
            }}>
              <CustomIcon name="bot" size={64} color={theme.textMuted} />
            </div>
            <div style={{
              fontSize: '18px',
              fontWeight: '600',
              marginBottom: '8px',
              color: theme.textSecondary
            }}>
              开始对话吧
            </div>
            <div style={{
              fontSize: '13px',
              opacity: 0.7,
              maxWidth: '280px',
              margin: '0 auto',
              lineHeight: '1.6'
            }}>
              输入文字或按住语音按钮开始对话
            </div>
          </div>
        </div>
      ) : (
        <VirtualMessageList messages={messages} theme={theme} />
      )}
    </div>
  );
}
