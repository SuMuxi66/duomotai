/**
 * ChatPane - 智能对话面板（主组件）
 *
 * 编排子组件：Header / History / Messages / Input。
 * 保持与原 ChatPane.tsx 完全相同的 props 接口。
 */
import { useEffect, useRef, memo } from "react";
import type { Theme } from "@/lib/theme";
import type { ChatSessionSummary, Msg } from "@/types";
import { ChatPaneHistory } from "./ChatPaneHistory";
import { ChatPaneMessages } from "./ChatPaneMessages";
import { ChatPaneInput, FileData } from "./ChatPaneInput";
import { ServingsCalcCard } from "../ServingsCalcCard";
import { CookingStepsPanel } from "../CookingStepsPanel";
import type { CookingTimerInfo } from "./CookingTimerBadge";

export type { CookingTimerInfo, FileData };

export interface ChatPaneProps {
  messages: Msg[];
  partialStt: string;
  input: string;
  setInput: (s: string) => void;
  onSendText: (files?: FileData[]) => void;
  onStop?: () => void;
  ttsEnabled: boolean;
  theme: Theme;
  isListening?: boolean;
  isAiSpeaking?: boolean;
  interruptMessage?: string | null;
  currentModel?: string;
  speechRecognitionState?: 'idle' | 'waiting' | 'listening' | 'analyzing';
  aiResponseState?: 'idle' | 'thinking' | 'responding';
  sttPipeline?: 'none' | 'push_to_talk';
  wakeWordEnabled?: boolean;
  wakeWordDetected?: boolean;
  wakeWordStatusText?: string;
  wakeWordLiveTranscript?: string;
  continuousMode?: boolean;
  continuousSpeechPaused?: boolean;
  cookingTimers?: CookingTimerInfo[];
  onCancelCookingTimer?: () => void;
  onInterruptAndContinue?: () => void;
  onNewChat?: () => void;
  onOpenHistory?: () => void;
  onCloseHistory?: () => void;
  onSelectHistorySession?: (sessionId: string) => void;
  onDeleteHistorySession?: (sessionId: string) => void;
  onRenameHistorySession?: (sessionId: string) => void;
  historySessions?: ChatSessionSummary[];
  activeSessionId?: string;
  isHistoryDialogOpen?: boolean;
  isHistoryLoading?: boolean;
  historySearchQuery?: string;
  onHistorySearchChange?: (query: string) => void;
  cookingActive?: boolean;
  currentRecipe?: any;
  currentStepNum?: number;
  completedStepNums?: number[];
  cookingStateStr?: string;
  servingsCalc?: { requestId: string; labels: string[]; results: (string | null)[] } | null;
}

export const ChatPane = memo(function ChatPane(props: ChatPaneProps) {
  const isDark = props.theme.mode === 'dark';
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [props.messages]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative" }}>
      <ChatPaneHistory
        theme={props.theme}
        isDark={isDark}
        isOpen={props.isHistoryDialogOpen}
        onClose={props.onCloseHistory}
        isLoading={props.isHistoryLoading}
        sessions={props.historySessions}
        activeSessionId={props.activeSessionId}
        searchQuery={props.historySearchQuery}
        onSearchChange={props.onHistorySearchChange}
        onSelectSession={props.onSelectHistorySession}
        onDeleteSession={props.onDeleteHistorySession}
        onRenameSession={props.onRenameHistorySession}
      />

      <ChatPaneMessages messages={props.messages} theme={props.theme} />

      {props.servingsCalc && props.servingsCalc.labels.length > 0 && (
        <ServingsCalcCard theme={props.theme} progress={props.servingsCalc} />
      )}

      {props.cookingActive && props.currentRecipe && (props.cookingStateStr === "COOKING" || props.cookingStateStr === "FINISHED") && (
        <CookingStepsPanel
          theme={props.theme}
          recipe={props.currentRecipe}
          currentStep={props.currentStepNum ?? 0}
          completedSteps={props.completedStepNums ?? []}
          state={props.cookingStateStr}
        />
      )}

      <ChatPaneInput
        theme={props.theme}
        isDark={isDark}
        input={props.input}
        setInput={props.setInput}
        onSendText={props.onSendText}
        onStop={props.onStop}
        ttsEnabled={props.ttsEnabled}
        partialStt={props.partialStt}
        isAiSpeaking={props.isAiSpeaking}
        aiResponseState={props.aiResponseState}
        isListening={props.isListening}
        speechRecognitionState={props.speechRecognitionState}
        sttPipeline={props.sttPipeline}
        cookingTimers={props.cookingTimers}
        onCancelCookingTimer={props.onCancelCookingTimer}
      />

      {/* CSS 动画 */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes marquee-scroll {
          0% { transform: translateX(0); }
          100% { transform: translateX(calc(-50% - 16px)); }
        }
      `}</style>
    </div>
  );
});
