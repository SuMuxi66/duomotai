import { useEffect, useRef, useCallback } from "react";
import { MainContent, MainContentRef } from "./components/MainContent";
import { ModelSelector } from "./components/ModelSelector";
import { PromptSettingsModal } from "./components/PromptSettingsModal";
import { ToastContainer, ToastMessage } from "./components/Toast";
import { DangerAlarmOverlay } from "./components/DangerAlarmOverlay";
import { TopNavBar } from "./components/TopNavBar";
import { CallHud } from "./components/CallHud";
import { useTheme } from "./hooks/useTheme";
import { useCookingMode } from "./hooks/useCookingMode";
import { useFanControl } from "./hooks/useFanControl";
import { useDeviceManager } from "./hooks/useDeviceManager";
import { useModelConfig } from "./hooks/useModelConfig";
import { useChatEngine } from "./hooks/useChatEngine";
import { useSessionManager } from "./hooks/useSessionManager";
import type { WebSocketConnectionState } from "./hooks/useWebSocket";
import { printConfig } from "./lib/config";
import { checkAndResetConfig, saveRawToStorage, STORAGE_KEYS } from "./lib/storage";
import { API_BASE, GLASSES_AUDIO_ID } from "./lib/constants";
import type { Msg } from "./types";

checkAndResetConfig();

export default function App() {
  const { theme, themeMode, toggleThemeMode } = useTheme();

  // 启动时打印配置
  useEffect(() => {
    printConfig();
  }, []);

  // 后端优先：启动时从 /config 拉取后端配置并同步到前端 state/localStorage
  useEffect(() => {
    fetch(`${API_BASE}/config`)
      .then(r => r.json())
      .then(config => {
        console.log('[App] 从后端拉取配置成功:', config);
        modelConfig.syncFromBackendConfig(config);
      })
      .catch(e => {
        console.error('[App] 从后端拉取配置失败:', e);
      });
  }, []);

  // 解锁音频上下文（解决浏览器自动播放策略限制）
  useEffect(() => {
    const unlockAudio = () => {
      const audio = new Audio('/djs.mp3');
      audio.volume = 0; // 静音播放
      audio.play().catch(() => {}); // 忽略错误
      document.removeEventListener('click', unlockAudio);
      document.removeEventListener('touchstart', unlockAudio);
    };
    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('touchstart', unlockAudio, { once: true });
    return () => {
      document.removeEventListener('click', unlockAudio);
      document.removeEventListener('touchstart', unlockAudio);
    };
  }, []);

  // Refs - 在 hooks 之间共享，避免循环依赖
  const sendCookingActionRef = useRef<((action: string, payload?: Record<string, unknown>) => void) | null>(null);
  const canSendRef = useRef(false);
  const sendWsEventRef = useRef<((type: string, payload: any) => void) | null>(null);
  const connectionStateRef = useRef<WebSocketConnectionState>("idle");
  const reconnectWsRef = useRef<(() => void) | null>(null);
  const mainContentRef = useRef<MainContentRef | null>(null);

  // showToast ref - useDeviceManager 需要 showToast，但 showToast 由 useChatEngine 提供
  // 使用 ref 打破 useDeviceManager ↔ useChatEngine 的循环依赖
  const showToastRef = useRef<(text: string, type?: ToastMessage["type"]) => void>(() => {});
  const showToastStub = useCallback((text: string, type?: ToastMessage["type"]) => {
    showToastRef.current?.(text, type);
  }, []);

  // Hooks 调用顺序: cooking → fanControl → deviceMgr → modelConfig → chatEngine → sessionMgr
  const cooking = useCookingMode(sendCookingActionRef);
  const fanControl = useFanControl();
  const deviceMgr = useDeviceManager({ showToast: showToastStub, sendWsEventRef });
  const modelConfig = useModelConfig({ canSendRef, sendWsEventRef });
  const chatEngine = useChatEngine({
    cooking,
    sendCookingActionRef,
    fanControl,
    deviceMgr,
    modelConfig,
    mainContentRef,
    canSendRef,
    sendWsEventRef,
    connectionStateRef,
    reconnectWsRef,
  });
  const sessionMgr = useSessionManager({
    sendInterrupt: chatEngine.sendInterrupt,
    setMessages: chatEngine.setMessages,
    setInput: chatEngine.setInput,
    setPartialStt: chatEngine.setPartialStt,
    setAiResponseState: chatEngine.setAiResponseState,
  });

  // 设置 showToastRef - chatEngine.showToast 是稳定的 useCallback
  showToastRef.current = chatEngine.showToast;

  // ===== 聊天历史 load/save =====
  // 需要 activeSessionId（来自 sessionMgr）和 messages/setMessages（来自 chatEngine）
  // 放在 App 中处理以避免 useChatEngine ↔ useSessionManager 的循环依赖
  const persistTimerRef = useRef<number | null>(null);
  const lastSavedMessagesRef = useRef("");

  const loadMessagesFromBackend = useCallback(async (retries = 3, delay = 1000) => {
    const storageKey = `multimodal_messages_${sessionMgr.activeSessionId}`;

    const loadFromLocalStorage = (): Msg[] => {
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
    };

    const saveToLocalStorage = (msgs: Msg[]) => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(msgs));
      } catch (e) {
        console.error('[ChatHistory] 保存到本地存储失败:', e);
      }
    };

    chatEngine.setMessages(loadFromLocalStorage());

    for (let i = 0; i < retries; i++) {
      try {
        console.log(`[ChatHistory] 正在加载聊天记录 (尝试 ${i + 1}/${retries}): ${API_BASE}/chat/history/${sessionMgr.activeSessionId}`);
        const resp = await fetch(`${API_BASE}/chat/history/${sessionMgr.activeSessionId}`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.messages && data.messages.length > 0) {
            // 清理残留的 assistant_stream 消息（刷新时未完成的流式消息）
            const cleaned = data.messages.map((m: Msg) =>
              m.role === "assistant_stream" ? { ...m, role: "assistant" as const } : m
            );
            chatEngine.setMessages(cleaned);
            saveToLocalStorage(cleaned);
            return true;
          } else {
            const localMsgs = loadFromLocalStorage();
            if (localMsgs.length > 0) {
              chatEngine.setMessages(localMsgs);
            }
            return true;
          }
        }
      } catch (e) {
        console.error(`[ChatHistory] 加载失败 (尝试 ${i + 1}):`, e);
      }

      if (i < retries - 1) {
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }

    console.warn('[ChatHistory] 聊天记录加载失败，使用本地缓存');
    const localMsgs = loadFromLocalStorage();
    if (localMsgs.length > 0) {
      chatEngine.setMessages(localMsgs);
    }
    return false;
  }, [sessionMgr.activeSessionId, chatEngine.setMessages]);

  const saveMessagesToBackend = useCallback(async (msgs: Msg[]) => {
    const storageKey = `multimodal_messages_${sessionMgr.activeSessionId}`;

    try {
      localStorage.setItem(storageKey, JSON.stringify(msgs));
    } catch (e) {
      console.error('[ChatHistory] 保存到本地存储失败:', e);
    }

    try {
      await fetch(`${API_BASE}/chat/history/${sessionMgr.activeSessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs })
      });
    } catch (e) {
      console.error('[ChatHistory] 保存到后端失败:', e);
    }
  }, [sessionMgr.activeSessionId]);

  useEffect(() => {
    void loadMessagesFromBackend();
  }, [loadMessagesFromBackend]);

  // 过滤掉 assistant_stream 消息（未完成的流式消息不应持久化）
  const sanitizeMessagesForSave = useCallback((msgs: Msg[]): Msg[] => {
    return msgs
      .filter(m => m.role !== "assistant_stream" || m.text?.trim())
      .map(m => m.role === "assistant_stream"
        ? { ...m, role: "assistant" as const }
        : m
      );
  }, []);

  useEffect(() => {
    const sanitized = sanitizeMessagesForSave(chatEngine.messages);
    const serializedMessages = JSON.stringify(sanitized);
    if (serializedMessages === lastSavedMessagesRef.current) return;

    if (persistTimerRef.current) {
      clearTimeout(persistTimerRef.current);
    }

    persistTimerRef.current = window.setTimeout(() => {
      void saveMessagesToBackend(sanitized);
      lastSavedMessagesRef.current = serializedMessages;
      persistTimerRef.current = null;
    }, 300);

    return () => {
      if (persistTimerRef.current) {
        clearTimeout(persistTimerRef.current);
        persistTimerRef.current = null;
      }
    };
  }, [chatEngine.messages, saveMessagesToBackend, sanitizeMessagesForSave]);

  // 页面关闭/刷新时紧急保存
  useEffect(() => {
    const handleBeforeUnload = () => {
      const sanitized = sanitizeMessagesForSave(chatEngine.messages);
      const storageKey = `multimodal_messages_${sessionMgr.activeSessionId}`;
      try {
        localStorage.setItem(storageKey, JSON.stringify(sanitized));
      } catch (e) {
        // 忽略写入错误
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [chatEngine.messages, sessionMgr.activeSessionId, sanitizeMessagesForSave]);

  // 会话切换时强制重连 WS，让后端按会话恢复对应的 AI 记忆
  // 跳过首次执行（初始连接已携带持久化的会话 ID）
  const prevSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (prevSessionIdRef.current === null) {
      prevSessionIdRef.current = sessionMgr.activeSessionId;
      return;
    }
    if (prevSessionIdRef.current !== sessionMgr.activeSessionId) {
      prevSessionIdRef.current = sessionMgr.activeSessionId;
      try {
        localStorage.setItem(STORAGE_KEYS.activeSessionId, JSON.stringify(sessionMgr.activeSessionId));
      } catch (e) {
        // 忽略写入错误
      }
      reconnectWsRef.current?.();
    }
  }, [sessionMgr.activeSessionId]);

  // ===== Fan toggle handler (TopNavBar 按钮) =====
  const handleFanToggle = useCallback(() => {
    fanControl.toggleFan();
  }, [fanControl]);

  // ===== 视频画面旋转：角度权威在后端 CAMERA_ROTATION（后端源头转正，MJPEG 流与 VLM 抓帧同步跟随），
  // 设置菜单"旋转画面"按钮每点一次顺时针 +90°，经 /env/update 持久化；本地 state 仅作立即回显，/config 加载后对齐
  const videoRotation = modelConfig.cameraRotation;
  const handleRotateVideo = useCallback(() => {
    const next = (modelConfig.cameraRotation + 90) % 360;
    modelConfig.setCameraRotation(next);
    fetch('/env/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'CAMERA_ROTATION', value: String(next) }),
    }).catch((e) => console.error('[RotateVideo] 持久化失败:', e));
  }, [modelConfig]);

  // ===== Glasses camera source change handler =====
  const handleCameraSourceChange = useCallback((source: string) => {
    deviceMgr.setUseGlassesCamera(source === "glasses");
    if (source === "local") {
      saveRawToStorage('multimodal_selected_video_device', deviceMgr.selectedVideoDevice);
    }
  }, [deviceMgr]);

  // ===== Glasses audio source change handler =====
  // 通过 saveAudioOutputDevice 统一处理：既更新前端状态，也发送 control.set_audio_source 给后端
  const handleAudioSourceChange = useCallback((source: string) => {
    if (source === "glasses") {
      console.log('[音频来源] 切换到眼镜音频模式');
      deviceMgr.saveAudioOutputDevice(GLASSES_AUDIO_ID);
    } else {
      console.log('[音频来源] 切换到本地音频模式');
      // source 为 "local" 时，切回浏览器默认扬声器（空字符串表示默认）
      deviceMgr.saveAudioOutputDevice('');
    }
  }, [deviceMgr]);

  // ===== JSX =====
  return (
    <div style={{
      position: 'relative',
      zIndex: 1,
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      padding: '16px',
      gap: '16px',
      background: theme.bg
    }}>
      <div style={{ position: 'fixed', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 0, overflow: 'hidden' }}>
        <div style={{
          position: 'absolute',
          top: '-20%',
          left: '-10%',
          width: '600px',
          height: '600px',
          background: `radial-gradient(circle, ${theme.primaryLight} 0%, transparent 70%)`,
          animation: 'float 20s ease-in-out infinite'
        }}></div>
        <div style={{
          position: 'absolute',
          bottom: '-20%',
          right: '-10%',
          width: '500px',
          height: '500px',
          background: `radial-gradient(circle, ${theme.secondaryLight} 0%, transparent 70%)`,
          animation: 'float 25s ease-in-out infinite reverse'
        }}></div>
      </div>

      <TopNavBar
        theme={theme}
        themeMode={themeMode}
        deviceInfo={deviceMgr.deviceInfo}
        selectedAudioDevice={deviceMgr.useGlassesAudio ? "__GLASSES_AUDIO__" : deviceMgr.selectedAudioDevice}
        selectedAudioOutputDevice={deviceMgr.selectedAudioOutputDevice}
        selectedVideoDevice={deviceMgr.useGlassesCamera ? "__GLASSES_CAMERA__" : deviceMgr.selectedVideoDevice}
        visionEnabled={modelConfig.visionEnabled}
        ttsEnabled={modelConfig.ttsEnabled}
        wakeWordEnabled={chatEngine.wakeWordEnabled}
        continuousMode={chatEngine.continuousMode}
        fanStatus={fanControl.fanStatus}
        onVisionEnabledChange={modelConfig.setVisionEnabled}
        onTtsEnabledChange={modelConfig.setTtsEnabled}
        onWakeWordEnabledChange={chatEngine.setWakeWordEnabled}
        onContinuousModeChange={chatEngine.setContinuousMode}
        onAudioDeviceChange={deviceMgr.saveAudioDevice}
        onAudioOutputDeviceChange={deviceMgr.saveAudioOutputDevice}
        onVideoDeviceChange={deviceMgr.saveVideoDevice}
        onOpenModelSelector={() => chatEngine.setShowModelSelector(true)}
        onOpenPromptSettings={() => chatEngine.setShowPromptSettings(true)}
        onToggleThemeMode={toggleThemeMode}
        onFanToggle={handleFanToggle}
        videoRotation={videoRotation}
        onRotateVideo={handleRotateVideo}
        glassesCameraConnected={deviceMgr.glassesCameraConnected}
        glassesAudioConnected={deviceMgr.glassesAudioConnected}
        cameraSource={deviceMgr.useGlassesCamera ? "glasses" : "usb"}
        glassesIp=""
        onCameraSourceChange={handleCameraSourceChange}
        onGlassesIpChange={() => {}}
        onOpenHistory={sessionMgr.openHistoryDialog}
        onNewChat={sessionMgr.createNewSession}
        wakeWordDetected={chatEngine.wakeWordDetected}
        wakeWordLiveTranscript={chatEngine.wakeWordLiveTranscript}
        isAiSpeaking={chatEngine.isAiSpeaking}
      />

      {chatEngine.showModelSelector && (
        <ModelSelector
          theme={theme}
          vlmProvider={modelConfig.vlmProvider}
          vlmModel={modelConfig.vlmModel}
          visionFps={modelConfig.visionFps}
          onVisionFpsChange={modelConfig.setVisionFps}
          sttProvider={modelConfig.sttProvider}
          sttModel={modelConfig.sttModel}
          ttsProvider={modelConfig.ttsProvider}
          ttsModel={modelConfig.ttsModel}
          ttsVoice={modelConfig.ttsVoice}
          llmTemperature={modelConfig.llmTemperature}
          llmMaxTokens={modelConfig.llmMaxTokens}
          llmTopP={modelConfig.llmTopP}
          llmSampleCount={modelConfig.llmSampleCount}
          llmFrequencyPenalty={modelConfig.llmFrequencyPenalty}
          llmResponseFormat={modelConfig.llmResponseFormat}
          llmEnableThinking={modelConfig.llmEnableThinking}
          dangerAlertEnabled={modelConfig.dangerAlertEnabled}
          oilSmokeAlertEnabled={modelConfig.oilSmokeAlertEnabled}
          cameraSourceMode={modelConfig.cameraSourceMode}
          onCameraSourceModeChange={(mode) => {
            modelConfig.setCameraSourceMode(mode);
            fetch('/env/update', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ key: 'CAMERA_SOURCE_MODE', value: mode }),
            }).catch((e) => console.error('[CameraSource] 保存失败:', e));
          }}
          isOpen={chatEngine.showModelSelector}
          onClose={() => chatEngine.setShowModelSelector(false)}
          onTestTts={chatEngine.testTts}
          isTestingTts={chatEngine.isTestingTts}
          onVlmProviderChange={modelConfig.handleVlmProviderChange}
          onVlmModelChange={modelConfig.handleVlmModelChange}
          edgeKwsUrl={modelConfig.edgeKwsUrl}
          edgeAsrUrl={modelConfig.edgeAsrUrl}
          onEdgeUrlsChange={(kwsUrl, asrUrl) => {
            modelConfig.setEdgeKwsUrl(kwsUrl);
            modelConfig.setEdgeAsrUrl(asrUrl);
          }}
          sttPreferCloud={modelConfig.sttPreferCloud}
          onSttPreferCloudChange={modelConfig.setSttPreferCloud}
          onSttProviderChange={modelConfig.handleSttProviderChange}
          onSttModelChange={modelConfig.handleSttModelChange}
          onTtsProviderChange={modelConfig.handleTtsProviderChange}
          onTtsModelChange={modelConfig.handleTtsModelChange}
          onTtsVoiceChange={modelConfig.handleTtsVoiceChange}
          onApiKeyChange={modelConfig.handleApiKeyChange}
          onLlmGenerationChange={modelConfig.handleLlmGenerationChange}
          onDangerAlertChange={modelConfig.handleDangerAlertChange}
          onOilSmokeAlertChange={modelConfig.handleOilSmokeAlertChange}
          apiKeys={modelConfig.apiKeys}
        />
      )}

      {chatEngine.showPromptSettings && (
        <PromptSettingsModal
          theme={theme}
          customPrompt={modelConfig.customPrompt}
          setCustomPrompt={modelConfig.setCustomPrompt}
          onClose={() => chatEngine.setShowPromptSettings(false)}
        />
      )}

      <MainContent
        ref={mainContentRef}
        theme={theme}
        messages={chatEngine.messages}
        partialStt={chatEngine.partialStt}
        wakeWordLiveTranscript={chatEngine.wakeWordLiveTranscript}
        input={chatEngine.input}
        setInput={chatEngine.setInput}
        onSendText={chatEngine.onSendText}
        onStop={chatEngine.sendInterrupt}
        ttsEnabled={modelConfig.ttsEnabled}
        isListening={chatEngine.isListening}
        isAiSpeaking={chatEngine.isAiSpeaking}
        interruptMessage={chatEngine.interruptMessage}
        speechRecognitionState={chatEngine.speechRecognitionState}
        aiResponseState={chatEngine.aiResponseState}
        wakeWordEnabled={chatEngine.wakeWordEnabled}
        wakeWordDetected={chatEngine.wakeWordDetected}
        wakeWordStatusText={chatEngine.wakeWordStatusText}
        selectedVideoDevice={deviceMgr.selectedVideoDevice}
        vlmProvider={modelConfig.vlmProvider}
        vlmModel={modelConfig.vlmModel}
        visionEnabled={modelConfig.visionEnabled}
        visionFps={modelConfig.visionFps}
        videoRotation={videoRotation}
        isRecording={chatEngine.isRecording}
        latestVisionObservation={chatEngine.latestVisionObservation}
        onVisionToggle={() => modelConfig.setVisionEnabled(!modelConfig.visionEnabled)}
        isCookingScene={chatEngine.isCookingScene}
        cookingSuggestion={chatEngine.cookingSuggestion}
        chatPanelWidth={chatEngine.chatPanelWidth}
        setChatPanelWidth={chatEngine.setChatPanelWidth}
        viewportWidth={chatEngine.viewportWidth}
        continuousMode={chatEngine.continuousMode}
        continuousSpeechPaused={chatEngine.continuousSpeechPaused}
        cookingActive={cooking.isActive}
        currentRecipe={cooking.currentRecipe}
        currentStepNum={cooking.currentStep}
        completedStepNums={cooking.completedSteps}
        cookingStateStr={cooking.cookingState}
        cookingTimers={cooking.timers}
        onCancelCookingTimer={cooking.cancelTimer}
        servingsCalc={chatEngine.servingsCalc}
        onInterruptAndContinue={chatEngine.interruptAndContinue}
        onNewChat={sessionMgr.createNewSession}
        onOpenHistory={sessionMgr.openHistoryDialog}
        onCloseHistory={sessionMgr.closeHistoryDialog}
        onSelectHistorySession={sessionMgr.selectHistorySession}
        onDeleteHistorySession={sessionMgr.deleteHistorySession}
        onRenameHistorySession={sessionMgr.renameHistorySession}
        historySessions={sessionMgr.historySessions}
        activeSessionId={sessionMgr.activeSessionId}
        isHistoryDialogOpen={sessionMgr.isHistoryDialogOpen}
        isHistoryLoading={sessionMgr.isHistoryLoading}
        historySearchQuery={sessionMgr.historySearchQuery} 
        onHistorySearchChange={sessionMgr.setHistorySearchQuery}
        useGlassesCamera={deviceMgr.useGlassesCamera}
        glassesImgRef={deviceMgr.glassesImgRef}
        onSourceChange={handleCameraSourceChange}
        useGlassesAudio={deviceMgr.useGlassesAudio}
        glassesAudioConnected={deviceMgr.glassesAudioConnected}
        onAudioSourceChange={handleAudioSourceChange}
      />

      <ToastContainer toasts={chatEngine.toasts} onDismiss={chatEngine.dismissToast} theme={theme} />

      {/* 危险警报大弹窗：屏幕正中、8秒自动消失，高能危险附带警笛 */}
      {chatEngine.dangerAlert && (
        <DangerAlarmOverlay
          alert={chatEngine.dangerAlert}
          onDismiss={chatEngine.dismissDangerAlert}
          theme={theme}
        />
      )}

      {/* 通话模式胶囊浮窗（唤端即接听，挂断/长静默才退出） */}
      {chatEngine.callActive && (
        <CallHud
          theme={theme}
          onHangup={() => {
            chatEngine.hangupCall();
            chatEngine.sendInterrupt();
          }}
        />
      )}

    </div>
  );
}
