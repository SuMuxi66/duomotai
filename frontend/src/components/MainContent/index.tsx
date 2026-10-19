import { useRef, useState, useEffect, forwardRef, useImperativeHandle, memo } from "react";
import { ChatPane, CookingTimerInfo, FileData } from "../ChatPane";
import { VideoPane, VideoPaneRef } from "../VideoPane";
import { Theme } from "../../lib/theme";
import { ChatSessionSummary, Msg } from "../../types";

export interface MainContentRef {
  captureNow: () => Promise<{ b64: string; width: number; height: number } | null>;
}

export interface MainContentProps {
  theme: Theme;
  messages: Msg[];
  partialStt: string;
  input: string;
  setInput: (input: string) => void;
  onSendText: (files?: FileData[]) => void;
  onStop?: () => void;
  ttsEnabled: boolean;
  isListening: boolean;
  isAiSpeaking: boolean;
  interruptMessage: string | null;
  speechRecognitionState: 'idle' | 'waiting' | 'listening' | 'analyzing';
  aiResponseState: 'idle' | 'thinking' | 'responding';
  wakeWordEnabled: boolean;
  wakeWordDetected: boolean;
  wakeWordStatusText?: string;
  wakeWordLiveTranscript?: string;
  selectedVideoDevice: string;
  vlmProvider: string;
  vlmModel: string;
  visionEnabled: boolean;
  visionFps: number;
  /** 本机画面旋转角度（权威在后端 CAMERA_ROTATION，仅 CSS 显示） */
  videoRotation?: number;
  isRecording: boolean;
  latestVisionObservation: string;
  isCookingScene?: boolean;
  cookingSuggestion?: string;
  chatPanelWidth: number;
  setChatPanelWidth: (width: number) => void;
  viewportWidth: number;
  continuousMode?: boolean;
  continuousSpeechPaused?: boolean;
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
  cookingTimers?: CookingTimerInfo[];
  onCancelCookingTimer?: () => void;
  useGlassesCamera?: boolean;
  glassesImgRef?: React.Ref<HTMLImageElement>;
  onSourceChange?: (source: 'local' | 'glasses') => void;
  useGlassesAudio?: boolean;
  glassesAudioConnected?: boolean;
  onAudioSourceChange?: (source: 'local' | 'glasses') => void;
  cookingActive?: boolean;
  currentRecipe?: any;
  currentStepNum?: number;
  completedStepNums?: number[];
  cookingStateStr?: string;
  /** 视觉分析开关回调（按钮位于视频流头部、分辨率右侧） */
  onVisionToggle?: () => void;
  /** 智能食材计算进度（逐项转圈卡片） */
  servingsCalc?: { requestId: string; labels: string[]; results: (string | null)[] } | null;
}

export const MainContent = memo(forwardRef<MainContentRef, MainContentProps>(({
  theme,
  messages,
  partialStt,
  input,
  setInput,
  onSendText,
  onStop,
  ttsEnabled,
  isListening,
  isAiSpeaking,
  interruptMessage,
  speechRecognitionState,
  aiResponseState,
  wakeWordEnabled,
  wakeWordDetected,
  wakeWordStatusText,
  wakeWordLiveTranscript,
  selectedVideoDevice,
  vlmProvider,
  vlmModel,
  visionEnabled: _visionEnabled,
  visionFps,
  videoRotation,
  isRecording,
  latestVisionObservation,
  isCookingScene,
  cookingSuggestion,
  chatPanelWidth,
  setChatPanelWidth,
  viewportWidth,
  continuousMode,
  continuousSpeechPaused,
  onInterruptAndContinue,
  onNewChat,
  onOpenHistory,
  onCloseHistory,
  onSelectHistorySession,
  onDeleteHistorySession,
  onRenameHistorySession,
  historySessions,
  activeSessionId,
  isHistoryDialogOpen,
  isHistoryLoading,
  historySearchQuery,
  onHistorySearchChange,
  cookingTimers,
  onCancelCookingTimer,
  useGlassesCamera,
  glassesImgRef,
  onSourceChange,
  useGlassesAudio,
  glassesAudioConnected,
  onAudioSourceChange,
  cookingActive,
  currentRecipe,
  currentStepNum,
  completedStepNums,
  cookingStateStr,
  onVisionToggle,
  servingsCalc,
}, ref) => {
  const mainContentRef = useRef<HTMLDivElement>(null);
  const videoPaneRef = useRef<VideoPaneRef>(null);
  const resizingRef = useRef(false);
  const [isResizingPanels, setIsResizingPanels] = useState(false);
  const [narrowRatio, setNarrowRatio] = useState(50);

  const isNarrowLayout = viewportWidth < 1200;

  useImperativeHandle(ref, () => ({
    captureNow: async () => {
      console.log('[MainContent] captureNow 被调用, videoPaneRef.current:', !!videoPaneRef.current);
      if (videoPaneRef.current) {
        console.log('[MainContent] 使用 VideoPane');
        return videoPaneRef.current.captureNow();
      }
      console.log('[MainContent] 没有可用的视频组件');
      return null;
    }
  }), []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!resizingRef.current || !mainContentRef.current) return;
      const rect = mainContentRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const minPx = 300;
      const maxPx = rect.width - 300;
      if (isNarrowLayout) {
        const pct = (x / rect.width) * 100;
        setNarrowRatio(Math.max(20, Math.min(80, pct)));
      } else {
        const next = Math.max(minPx, Math.min(x, maxPx));
        setChatPanelWidth(next);
      }
    };

    const onMouseUp = () => {
      if (!resizingRef.current) return;
      resizingRef.current = false;
      setIsResizingPanels(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [setChatPanelWidth, setNarrowRatio, isNarrowLayout]);

  return (
    <div
      ref={mainContentRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        flex: 1,
        minHeight: 0,
        zIndex: 1,
        background: 'transparent',
        borderRadius: 0,
        border: 'none',
        backdropFilter: 'none',
        boxShadow: 'none',
        overflow: 'hidden',
        position: 'relative'
      }}
    >
      <div style={{
        display: 'flex',
        flexDirection: 'row',
        gap: 0,
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        position: 'relative'
      }}>
      <div style={{
        width: isNarrowLayout ? `${narrowRatio}%` : `${chatPanelWidth}px`,
        minWidth: isNarrowLayout ? '30%' : '0px',
        maxWidth: isNarrowLayout ? '80%' : `${chatPanelWidth + 200}px`,
        flex: '0 0 auto',
        display: 'flex',
        flexDirection: 'column',
        background: 'transparent',
        borderRadius: 0,
        border: 'none',
        borderRight: '1px solid rgba(148, 163, 184, 0.08)',
        backdropFilter: 'none',
        boxShadow: 'none',
        overflow: 'hidden',
        position: 'relative'
      }}>
        <ChatPane
          messages={messages}
          partialStt={partialStt}
          input={input}
          setInput={setInput}
          onSendText={onSendText}
          onStop={onStop}
          ttsEnabled={ttsEnabled}
          theme={theme}
          isListening={isListening}
          isAiSpeaking={isAiSpeaking}
          interruptMessage={interruptMessage}
          speechRecognitionState={speechRecognitionState}
          aiResponseState={aiResponseState}
          wakeWordEnabled={wakeWordEnabled}
          wakeWordDetected={wakeWordDetected}
          wakeWordStatusText={wakeWordStatusText}
          wakeWordLiveTranscript={wakeWordLiveTranscript}
          continuousMode={continuousMode}
          continuousSpeechPaused={continuousSpeechPaused}
          onInterruptAndContinue={onInterruptAndContinue}
          onNewChat={onNewChat}
          onOpenHistory={onOpenHistory}
          onCloseHistory={onCloseHistory}
          onSelectHistorySession={onSelectHistorySession}
          onDeleteHistorySession={onDeleteHistorySession}
          onRenameHistorySession={onRenameHistorySession}
          historySessions={historySessions}
          activeSessionId={activeSessionId}
          isHistoryDialogOpen={isHistoryDialogOpen}
          isHistoryLoading={isHistoryLoading}
          historySearchQuery={historySearchQuery}
          onHistorySearchChange={onHistorySearchChange}
          cookingActive={cookingActive}
          currentRecipe={currentRecipe}
          currentStepNum={currentStepNum}
          completedStepNums={completedStepNums}
          cookingStateStr={cookingStateStr}
          servingsCalc={servingsCalc}
        />

        {!isNarrowLayout && (
          <div
            onMouseDown={() => {
              resizingRef.current = true;
              setIsResizingPanels(true);
              document.body.style.cursor = 'col-resize';
              document.body.style.userSelect = 'none';
            }}
            style={{
              position: 'absolute',
              top: 0,
              right: '-4px',
              width: '8px',
              height: '100%',
              cursor: 'default',
              zIndex: 20,
              background: 'transparent',
            }}
          />
        )}
      </div>

      {/* Narrow layout: vertical resize handle between chat and video */}
      {isNarrowLayout && (
        <div
          onMouseDown={() => {
            resizingRef.current = true;
            setIsResizingPanels(true);
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
          }}
          style={{
            width: '4px',
            flexShrink: 0,
            cursor: 'col-resize',
            zIndex: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: isResizingPanels ? `${theme.primary}22` : 'transparent',
            transition: 'background 0.15s',
          }}
          title="拖动调整左右比例"
        >
          <div style={{
            width: '1px',
            height: '24px',
            borderRadius: '1px',
            background: theme.borderLight,
            opacity: 0.5,
            transition: 'background 0.15s, height 0.15s',
          }} />
        </div>
      )}

      {/* 右侧视频区域 */}
      <div className="right-panel-container" style={{
        flex: 1,
        minWidth: isNarrowLayout ? '30%' : 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden'
      }}>
        <div style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          background: 'transparent',
          borderRadius: 0,
          border: 'none',
          backdropFilter: 'none',
          boxShadow: 'none',
          overflow: 'hidden',
          position: 'relative'
        }}>
          <VideoPane
            ref={videoPaneRef}
            enabled={_visionEnabled}
            fps={visionFps}
            videoRotation={videoRotation}
            maxWidth={1920}
            jpegQuality={0.8}
            videoDeviceId={selectedVideoDevice || undefined}
            currentProvider={vlmProvider}
            currentModel={vlmModel}
            theme={theme}
            isRecording={isRecording}
            latestObservation={latestVisionObservation}
            isCookingScene={isCookingScene}
            cookingSuggestion={cookingSuggestion}
            cookingTimers={cookingTimers}
            onCancelCookingTimer={onCancelCookingTimer}
            useGlassesCamera={useGlassesCamera}
            glassesImgRef={glassesImgRef}
            onSourceChange={onSourceChange}
            useGlassesAudio={useGlassesAudio}
            glassesAudioConnected={glassesAudioConnected}
            onAudioSourceChange={onAudioSourceChange}
            onVisionToggle={onVisionToggle}
          />
        </div>
      </div>
    </div>
    </div>
  );
}));
