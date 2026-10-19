/**
 * ChatPaneHistory - 历史对话弹窗
 *
 * 模态层：搜索 + 会话列表（选择/重命名/删除）。
 */
import { CustomIcon } from "@/components/CustomIcon";
import { Button } from "@/components/ui/Button";
import type { Theme } from "@/lib/theme";
import type { ChatSessionSummary } from "@/types";

export interface ChatPaneHistoryProps {
  theme: Theme;
  isDark: boolean;
  isOpen?: boolean;
  onClose?: () => void;
  isLoading?: boolean;
  sessions?: ChatSessionSummary[];
  activeSessionId?: string;
  searchQuery?: string;
  onSearchChange?: (query: string) => void;
  onSelectSession?: (sessionId: string) => void;
  onDeleteSession?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string) => void;
}

function formatSessionTime(value?: string) {
  if (!value) return "暂无时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function getSessionLabel(session: ChatSessionSummary) {
  if (session.title?.trim()) return session.title.trim();
  if (session.session_id === "default") return "默认对话";
  return session.session_id.replace(/^session_/, "对话 ");
}

function getSessionPreview(session: ChatSessionSummary) {
  if (session.preview?.trim()) return session.preview.trim();
  return "暂无聊天内容";
}

export function ChatPaneHistory(props: ChatPaneHistoryProps) {
  const { theme, isDark } = props;
  if (!props.isOpen) return null;

  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      zIndex: 30,
      background: 'rgba(15, 23, 42, 0.18)',
      backdropFilter: 'blur(8px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '24px'
    }}>
      <div style={{
        width: '100%',
        maxWidth: '520px',
        maxHeight: '70%',
        display: 'flex',
        flexDirection: 'column',
        borderRadius: '28px',
        overflow: 'hidden',
        border: '1px solid ' + theme.borderLight,
        background: isDark ? theme.bgSecondary : 'rgba(255, 255, 255, 0.96)',
        boxShadow: isDark ? '0 28px 72px rgba(0, 0, 0, 0.45)' : '0 24px 64px rgba(15, 23, 42, 0.22)'
      }}>
        {/* 弹窗头部 */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '18px 20px',
          borderBottom: '1px solid ' + theme.borderLight
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ fontSize: '16px', fontWeight: 700, color: theme.text }}>历史对话</div>
            <div style={{ fontSize: '12px', color: theme.textMuted }}>选择需要恢复的聊天记录</div>
          </div>
          <Button variant="secondary" size="sm" theme={theme} onClick={props.onClose}>
            关闭
          </Button>
        </div>

        {/* 弹窗主体 */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          {/* 搜索框 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '0 2px 4px' }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              flex: 1,
              padding: '0 14px',
              height: '44px',
              borderRadius: '20px',
              border: '1px solid ' + theme.borderLight,
              background: isDark ? theme.bgTertiary : 'rgba(248, 250, 252, 0.98)',
              boxShadow: isDark ? 'none' : 'inset 0 1px 0 rgba(255, 255, 255, 0.8)'
            }}>
              <CustomIcon name="search" size={16} color={theme.textMuted} />
              <input
                value={props.searchQuery || ""}
                onChange={(e) => props.onSearchChange?.(e.target.value)}
                placeholder="搜索历史标题或聊天内容"
                style={{
                  flex: 1,
                  border: 'none',
                  outline: 'none',
                  background: 'transparent',
                  color: theme.text,
                  fontSize: '13px'
                }}
              />
            </div>
          </div>

          {/* 加载/空/列表 */}
          {props.isLoading ? (
            <div style={{ padding: '48px 16px', textAlign: 'center', color: theme.textMuted, fontSize: '13px' }}>
              正在加载历史对话...
            </div>
          ) : (props.sessions?.length || 0) === 0 ? (
            <div style={{ padding: '48px 16px', textAlign: 'center', color: theme.textMuted, fontSize: '13px' }}>
              暂无匹配的历史对话
            </div>
          ) : props.sessions!.map((session) => {
            const isActive = props.activeSessionId === session.session_id;
            return (
              <div
                key={session.session_id}
                onClick={() => props.onSelectSession?.(session.session_id)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'stretch',
                  gap: '8px',
                  padding: '16px',
                  borderRadius: '22px',
                  border: '1px solid ' + (isActive ? theme.primary : theme.borderLight),
                  background: isActive
                    ? 'linear-gradient(135deg, rgba(106, 159, 181, 0.16), rgba(106, 159, 181, 0.08))'
                    : 'rgba(248, 250, 252, 0.95)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.25s ease',
                  boxShadow: isActive
                    ? '0 10px 24px rgba(106, 159, 181, 0.16)'
                    : '0 4px 12px rgba(15, 23, 42, 0.06)'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
                  <div style={{ fontSize: '14px', fontWeight: 600, color: theme.text }}>
                    {getSessionLabel(session)}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div style={{
                      padding: '4px 10px',
                      borderRadius: '999px',
                      fontSize: '12px',
                      color: isActive ? theme.primary : theme.textSecondary,
                      background: isDark ? theme.buttonSecondary : (isActive ? 'rgba(255, 255, 255, 0.7)' : 'rgba(255, 255, 255, 0.85)'),
                      border: '1px solid ' + (isActive ? `${theme.primary}33` : theme.borderLight)
                    }}>
                      {session.message_count} 条消息
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      theme={theme}
                      icon={<CustomIcon name="pencil" size={14} color={theme.textSecondary} />}
                      onClick={(e) => {
                        e.stopPropagation();
                        props.onRenameSession?.(session.session_id);
                      }}
                      title="重命名"
                      aria-label="重命名"
                      style={{ width: 32, height: 32, padding: 0 }}
                    />
                    <Button
                      variant="secondary"
                      size="sm"
                      theme={theme}
                      icon={<CustomIcon name="trash" size={14} color="#C17E7E" />}
                      onClick={(e) => {
                        e.stopPropagation();
                        props.onDeleteSession?.(session.session_id);
                      }}
                      title="删除"
                      aria-label="删除"
                      style={{ width: 32, height: 32, padding: 0, border: '1px solid rgba(193, 126, 126, 0.35)' }}
                    />
                  </div>
                </div>
                <div style={{ fontSize: '12px', color: theme.textSecondary }}>
                  最近更新：{formatSessionTime(session.updated_at || session.created_at)}
                </div>
                <div style={{
                  fontSize: '13px',
                  color: theme.text,
                  lineHeight: '1.6',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis'
                }}>
                  {getSessionPreview(session)}
                </div>
                <div style={{ fontSize: '12px', color: theme.textMuted, wordBreak: 'break-all' }}>
                  会话 ID：{session.session_id}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
