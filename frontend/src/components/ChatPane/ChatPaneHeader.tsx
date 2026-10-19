/**
 * ChatPaneHeader - 聊天头部
 *
 * 标题、历史对话按钮、新建对话按钮、状态指示器（唤醒词/持续识别）。
 */
import { CustomIcon } from "@/components/CustomIcon";
import { Button } from "@/components/ui/Button";
import type { Theme } from "@/lib/theme";

export interface ChatPaneHeaderProps {
  theme: Theme;
  onOpenHistory?: () => void;
  onNewChat?: () => void;
  continuousMode?: boolean;
  continuousSpeechPaused?: boolean;
  wakeWordEnabled?: boolean;
  wakeWordDetected?: boolean;
  wakeWordStatusText?: string;
  wakeWordLiveTranscript?: string;
  isAiSpeaking?: boolean;
  onInterruptAndContinue?: () => void;
}

export function ChatPaneHeader(props: ChatPaneHeaderProps) {
  const { theme } = props;
  const hasWakeWordLiveTranscript = !!props.wakeWordLiveTranscript?.trim();

  // ─── 状态指示器 ───
  // 绿=正常(检测到唤醒词), 红=报错/未开启, 黄=AI输出中, 蓝=监听中
  const statusColor =
    props.isAiSpeaking
      ? '#f59e0b'
      : props.wakeWordDetected || hasWakeWordLiveTranscript
        ? '#22c55e'
        : props.wakeWordEnabled
          ? '#3b82f6'
          : '#ef4444';

  const statusAnimation =
    (hasWakeWordLiveTranscript || props.wakeWordDetected || props.isAiSpeaking)
      ? 'blink 1.5s ease-in-out infinite'
      : 'none';

  const statusTooltip = props.isAiSpeaking
    ? 'AI 回复中'
    : props.wakeWordDetected
      ? '已检测到提示词'
      : hasWakeWordLiveTranscript
        ? '识别中'
        : props.wakeWordEnabled
          ? '麦克风监听中'
          : '唤醒词未开启';

  function renderStatusDot() {
    return (
      <span title={statusTooltip} style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
        <span
          style={{
            display: 'inline-block',
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: statusColor,
            boxShadow: `0 0 6px ${statusColor}80`,
            animation: statusAnimation,
            transition: 'all 0.3s ease',
          }}
        />
      </span>
    );
  }

  return (
    <div style={{
      padding: '20px 24px',
      borderBottom: '1px solid ' + theme.borderLight,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: theme.bgSecondary
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <Button
          variant="secondary"
          size="sm"
          theme={theme}
          icon={<CustomIcon name="history" size={16} color={props.onOpenHistory ? theme.primary : theme.textMuted} />}
          onClick={props.onOpenHistory}
          disabled={!props.onOpenHistory}
          aria-label="历史对话"
          title="历史对话"
        />

        <Button
          variant="secondary"
          size="sm"
          theme={theme}
          icon={<CustomIcon name="plus" size={16} color={props.onNewChat ? theme.primary : theme.textMuted} />}
          onClick={props.onNewChat}
          disabled={!props.onNewChat}
          aria-label="新建对话"
          title="新建对话"
        />

        {/* 状态指示器 — 简化版 */}
        {renderStatusDot()}
      </div>
    </div>
  );
}
