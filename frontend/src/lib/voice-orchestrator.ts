export type VoiceMode = "idle" | "wakeword" | "continuous";
export type VoiceLogLevel = "log" | "warn" | "error";

export interface WakeWordListenerController {
  start: () => Promise<void>;
  stop: () => void;
  destroy: () => void;
  startCollecting?: () => void;
}

export interface ContinuousSpeechListenerController {
  start: () => Promise<void>;
  stop: () => void;
  pause: () => void;
  resume: () => Promise<void>;
}

export type VoiceOrchestrationAction =
  | { type: "none" }
  | { type: "startWakeWord"; forceRestart: boolean }
  | { type: "ensureContinuous"; paused: boolean };

export interface VoiceOrchestrationLog {
  key: string;
  message: string;
  level: VoiceLogLevel;
  throttleMs: number;
}

export interface CookingVoiceContext {
  enabled: boolean;
  state: string;
  recipeName?: string | null;
  currentStep?: number | null;
  currentServings?: number | null;
  awaitingServings?: boolean;
}

export interface VoiceOrchestrationPlan {
  kind: "idle" | "ws-waiting" | "continuous-paused" | "continuous-active" | "wakeword-ai-paused" | "wakeword-active";
  mode: VoiceMode;
  destroyWakeWord: boolean;
  stopContinuousSpeech: boolean;
  resetWakeWordUi: boolean;
  nextWakeWordSuspended: boolean;
  status: string;
  action: VoiceOrchestrationAction;
  log?: VoiceOrchestrationLog;
}

export interface VoiceOrchestrationContext {
  wakeWordEnabled: boolean;
  continuousMode: boolean;
  wsReady: boolean;
  aiSpeaking: boolean;
  manualPause: boolean;
  hasWakeWordListener: boolean;
  hasContinuousSpeechListener: boolean;
  wakeWordSuspended: boolean;
  cooking?: CookingVoiceContext;
  reason?: string;
}

export function getDesiredVoiceMode(options: Pick<VoiceOrchestrationContext, "wakeWordEnabled" | "continuousMode">): VoiceMode {
  if (options.continuousMode) {
    return "continuous";
  }
  if (options.wakeWordEnabled) {
    return "wakeword";
  }
  return "idle";
}

function buildCookingSceneStatus(cooking?: CookingVoiceContext): string | null {
  if (!cooking?.enabled) {
    return null;
  }

  const recipeName = cooking.recipeName?.trim() || "";
  const recipeLabel = recipeName ? `《${recipeName}》` : "";
  const currentStep = typeof cooking.currentStep === "number" && cooking.currentStep > 0 ? cooking.currentStep : null;

  if (cooking.awaitingServings || cooking.state === "AWAITING_SERVINGS") {
    return recipeLabel ? `做饭中，${recipeLabel}等待你告诉我几人份` : "做饭中，等待你告诉我几人份";
  }

  if (cooking.state === "RECOMMENDING") {
    return "做饭中，正在推荐适合的菜谱";
  }

  if (cooking.state === "COLLECTING_INGREDIENTS") {
    return "做饭中，请告诉我你有什么食材";
  }

  if (cooking.state === "COOKING" || cooking.state === "STEP_WAITING") {
    if (recipeLabel && currentStep) {
      return `做饭中，${recipeLabel}第${currentStep}步`;
    }
    if (recipeLabel) {
      return `做饭中，正在引导${recipeLabel}`;
    }
    if (currentStep) {
      return `做饭中，正在引导第${currentStep}步`;
    }
    return "做饭中，正在进行步骤式引导";
  }

  if (cooking.state === "FINISHED") {
    return recipeLabel ? `做饭中，${recipeLabel}已完成` : "做饭中，做饭流程已完成";
  }

  if (recipeLabel) {
    return `做饭中，正在引导${recipeLabel}`;
  }

  return "做饭中，已进入步骤式引导";
}

function mergeCookingStatus(baseStatus: string, cooking?: CookingVoiceContext): string {
  const cookingStatus = buildCookingSceneStatus(cooking);
  if (!cookingStatus) {
    return baseStatus;
  }
  if (baseStatus.includes("做饭中")) {
    return baseStatus;
  }
  return `${cookingStatus}；${baseStatus}`;
}

export function createVoiceOrchestrationPlan(context: VoiceOrchestrationContext): VoiceOrchestrationPlan {
  const desiredMode = getDesiredVoiceMode(context);
  const reason = context.reason ?? "sync";

  if (desiredMode === "idle") {
    return {
      kind: "idle",
      mode: desiredMode,
      destroyWakeWord: context.hasWakeWordListener,
      stopContinuousSpeech: context.hasContinuousSpeechListener,
      resetWakeWordUi: true,
      nextWakeWordSuspended: false,
      status: mergeCookingStatus("唤醒词未开启", context.cooking),
      action: { type: "none" },
    };
  }

  if (!context.wsReady) {
    // 唤醒词检测（端侧 KWS/ASR 或浏览器 Web Speech）独立于主聊天 WS，
    // 主链路短暂抖动（如 ws code=1000 重连）不应销毁监听，否则会漏检唤醒词。
    // 仅持续识别模式真正依赖主 WS 发送音频，才需要挂起。
    if (desiredMode === "wakeword") {
      return {
        kind: "wakeword-active",
        mode: desiredMode,
        destroyWakeWord: false,
        stopContinuousSpeech: context.hasContinuousSpeechListener,
        resetWakeWordUi: false,
        nextWakeWordSuspended: context.wakeWordSuspended,
        status: mergeCookingStatus("唤醒词监听中（主链路重连中）", context.cooking),
        action: context.hasWakeWordListener ? { type: "none" } : { type: "startWakeWord", forceRestart: false },
        log: {
          key: "voice-ws-wakeword-independent",
          message: `[语音状态机] ${reason}：主 WS 未连接，但唤醒词（端侧）保持监听不销毁`,
          level: "log",
          throttleMs: 1500,
        },
      };
    }
    return {
      kind: "ws-waiting",
      mode: desiredMode,
      destroyWakeWord: context.hasWakeWordListener,
      // 通话/持续识别依赖主 WS 发送音频：WS 短暂抖动时不要销毁监听器，
      // 否则会误触发挂断('stop')并在重连后重新接听，体验上等于“通话被瞬断网打断”。
      // 改为保留监听器（下方 action 对其执行 pause），WS 恢复后 resume，
      // 与电话通话不被瞬时断网打断的体验一致。
      stopContinuousSpeech: false,
      resetWakeWordUi: true,
      nextWakeWordSuspended: true,
      status: mergeCookingStatus("后端连接中，持续识别等待恢复", context.cooking),
      // 让 reconcile 对已有监听器执行 pause（保留通话 session，不挂断）
      action: { type: "ensureContinuous", paused: true },
      log: {
        key: `voice-ws-${desiredMode}`,
        message: `[语音状态机] ${reason}：后端未连接，持续识别挂起（保留通话）`,
        level: "log",
        throttleMs: 1500,
      },
    };
  }

  if (desiredMode === "continuous") {
    // 只有手动暂停才挂起持续识别；AI 播报时保持监听，
    // 以便用户随时说话触发 barge-in（onUserSpeechInDialog -> sendInterrupt）打断播报。
    // 若在此 pause，asrWs 推流会停，用户说话设备 VAD 收不到，就无法"随时打断"。
    const paused = context.manualPause;
    return {
      kind: paused ? "continuous-paused" : "continuous-active",
      mode: desiredMode,
      destroyWakeWord: context.hasWakeWordListener,
      stopContinuousSpeech: false,
      resetWakeWordUi: true,
      nextWakeWordSuspended: false,
      status: mergeCookingStatus(
        context.manualPause ? "持续识别已手动暂停" : context.aiSpeaking ? "AI 播报中，可随时说话打断" : "持续识别已开启",
        context.cooking
      ),
      action: { type: "ensureContinuous", paused },
      log: {
        key: paused ? `continuous-pause-${context.manualPause ? "manual" : "ai"}` : "continuous-active",
        message: `[语音状态机] ${reason}：${paused ? "持续识别手动暂停" : "持续识别监听中（AI播报也可打断）"}`,
        level: "log",
        throttleMs: 1200,
      },
    };
  }

  if (context.aiSpeaking) {
    // AI 播报期间不销毁唤醒词监听：KWS 持续推流以支持"唤醒打断"(barge-in)，
    // 否则 AI 说话时无法被唤醒词中断，属于漏检。
    return {
      kind: "wakeword-active",
      mode: desiredMode,
      destroyWakeWord: false,
      stopContinuousSpeech: context.hasContinuousSpeechListener,
      resetWakeWordUi: false,
      nextWakeWordSuspended: context.wakeWordSuspended,
      status: mergeCookingStatus("AI 播报中，唤醒词保持监听（可打断）", context.cooking),
      action: context.hasWakeWordListener ? { type: "none" } : { type: "startWakeWord", forceRestart: false },
      log: {
        key: "wakeword-ai-listening",
        message: `[语音状态机] ${reason}：AI 播报期间唤醒词保持监听以支持打断`,
        level: "log",
        throttleMs: 1200,
      },
    };
  }

    return {
      kind: "wakeword-active",
      mode: desiredMode,
      destroyWakeWord: false,
      stopContinuousSpeech: context.hasContinuousSpeechListener,
      resetWakeWordUi: true,
      nextWakeWordSuspended: context.wakeWordSuspended,
      status: mergeCookingStatus("唤醒词监听已启动", context.cooking),
      action: { type: "startWakeWord", forceRestart: context.wakeWordSuspended },
      log: {
        key: "wakeword-active",
        message: `[语音状态机] ${reason}：唤醒词处于唯一语音入口`,
      level: "log",
      throttleMs: 1200,
    },
  };
}
