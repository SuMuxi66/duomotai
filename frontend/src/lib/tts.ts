/**
 * TTS 语音播报管理器
 * 封装浏览器内置 SpeechSynthesis API，提供语音播放控制功能
 */

/**
 * TTS 配置选项
 */
export interface TTSOptions {
  /** 语速 (0.1 - 10，默认 1) */
  rate?: number;
  /** 音调 (0 - 2，默认 1) */
  pitch?: number;
  /** 音量 (0 - 1，默认 1) */
  volume?: number;
  /** 语言 (默认 'zh-CN') */
  lang?: string;
  /** 语音名称 (可选，指定特定语音) */
  voiceName?: string;
}

/**
 * TTS 控制器接口
 */
export interface TTSController {
  /** 播放文本 */
  speak: (text: string) => void;
  /** 停止播放 */
  stop: () => void;
  /** 是否正在播放 */
  isSpeaking: boolean;
  /** 当前配置 */
  options: TTSOptions;
  /** 更新配置 */
  updateOptions: (options: Partial<TTSOptions>) => void;
  /** 获取可用语音列表 */
  getVoices: () => SpeechSynthesisVoice[];
  /** 播放完成回调 */
  onEnd?: () => void;
  /** 播放开始回调 */
  onStart?: () => void;
  /** 播放错误回调 */
  onError?: (error: string) => void;
  /** 销毁实例，清理所有资源 */
  destroy: () => void;
}

/**
 * Chrome 已知问题：紧跟 speechSynthesis.cancel() 的 speak() 可能整条丢弃新 utterance
 * （无 onstart/onend/onerror 任何事件）。因此 cancel 后要等旧 utterance 收尾再 speak 新的。
 * 这是等待上限（ms），与旧 utterance 的 onend/onerror 取先到；实测收尾事件通常几十毫秒内
 * 到达，超时只是保底。
 */
const CANCEL_SETTLE_TIMEOUT_MS = 120;

/**
 * onstart 看门狗时限：speak() 后若这么久内 utterance 一个事件都没来
 * （onstart/onend/onerror 均无），视为被浏览器整条吞掉，主动触发完成回调，
 * 保证上层流程（唤醒反馈后的开始收音/恢复编排）一定能续命，而不是永远死等。
 */
const ONSTART_WATCHDOG_MS = 3000;

/**
 * 创建 TTS 语音播报管理器
 * @param options TTS 配置选项
 * @param onEnd 播放完成回调
 * @returns TTSController 控制器实例
 */
export function createTTSController(
  options: TTSOptions = {},
  onEnd?: () => void
): TTSController {
  // 默认配置
  const defaultOptions: Required<Omit<TTSOptions, 'voiceName'>> = {
    rate: 1,
    pitch: 1,
    volume: 1,
    lang: 'zh-CN',
  };

  // 合并配置
  let currentOptions: TTSOptions = { ...defaultOptions, ...options };

  // 播放状态
  let speaking = false;

  // 当前语音实例
  let currentUtterance: SpeechSynthesisUtterance | null = null;

  // 回调函数
  let endCallback = onEnd;
  let startCallback: (() => void) | undefined;
  let errorCallback: ((error: string) => void) | undefined;

  // cancel 收尾等待定时器：speak() 让路旧 utterance 后，延迟发出新 utterance 的挂起句柄
  let cancelSettleTimer: number | null = null;
  // onstart 看门狗定时器
  let watchdogTimer: number | null = null;
  // 本轮播报的完成回调是否已触发（看门狗与迟到 onend 竞态下保证只触发一次）
  let endCallbackFired = false;

  function clearCancelSettleTimer(): void {
    if (cancelSettleTimer !== null) {
      clearTimeout(cancelSettleTimer);
      cancelSettleTimer = null;
    }
  }

  function clearWatchdog(): void {
    if (watchdogTimer !== null) {
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }
  }

  function fireEndCallback(): void {
    if (endCallbackFired) return;
    endCallbackFired = true;
    endCallback?.();
  }

  // voiceschanged 监听器引用
  let voicesChangedHandler: (() => void) | null = null;

  /**
   * 获取匹配的语音
   */
  function getMatchingVoice(lang: string, voiceName?: string): SpeechSynthesisVoice | null {
    const voices = speechSynthesis.getVoices();

    if (voices.length === 0) {
      return null;
    }

    // 如果指定了语音名称，优先匹配
    if (voiceName) {
      const namedVoice = voices.find(v => v.name === voiceName);
      if (namedVoice) return namedVoice;
    }

    // 匹配语言
    const langVoice = voices.find(v => v.lang.startsWith(lang));
    if (langVoice) return langVoice;

    // 回退到默认语音
    return voices.find(v => v.default) || voices[0];
  }

  /**
   * 结束本轮播报：复位状态并触发完成回调（fired 标志保证只触发一次）
   */
  function finishSpeak(utterance: SpeechSynthesisUtterance): void {
    speaking = false;
    if (currentUtterance === utterance) {
      currentUtterance = null;
    }
    fireEndCallback();
  }

  /**
   * 实际提交新 utterance 给 speechSynthesis（带 onstart 看门狗）
   */
  function startSpeak(text: string): void {
    // 新一轮播报：重置完成回调标志
    endCallbackFired = false;

    const utterance = new SpeechSynthesisUtterance(text);

    // 设置参数
    utterance.rate = currentOptions.rate ?? 1;
    utterance.pitch = currentOptions.pitch ?? 1;
    utterance.volume = currentOptions.volume ?? 1;
    utterance.lang = currentOptions.lang ?? 'zh-CN';

    // 设置语音
    const voice = getMatchingVoice(currentOptions.lang ?? 'zh-CN', currentOptions.voiceName);
    if (voice) {
      utterance.voice = voice;
    }

    // 监听事件（任一事件到达即清看门狗）
    utterance.onstart = () => {
      clearWatchdog();
      speaking = true;
      startCallback?.();
    };

    utterance.onend = () => {
      clearWatchdog();
      finishSpeak(utterance);
    };

    utterance.onerror = (event) => {
      clearWatchdog();
      // 'interrupted'/'canceled' 是 speechSynthesis.cancel() 主动中断的预期结果
      // （唤醒词 barge-in、新 speak 覆盖旧播放、stop() 等场景），不是真正的错误，
      // 也不触发 endCallback——流式逐句场景旧 utterance 被新 speak 顶替时，
      // 提前打完"说完"语义会打乱上层流程；由正常 onend / 看门狗 /
      // 下一轮 speak 的 cancel 收尾等待兜底
      if (event.error === 'interrupted' || event.error === 'canceled') {
        speaking = false;
        if (currentUtterance === utterance) {
          currentUtterance = null;
        }
        return;
      }
      speaking = false;
      if (currentUtterance === utterance) {
        currentUtterance = null;
      }
      const errorMsg = `语音播放错误: ${event.error}`;
      console.error(errorMsg);
      errorCallback?.(errorMsg);
    };

    currentUtterance = utterance;

    // 开始播放
    try {
      speechSynthesis.speak(utterance);
    } catch (error) {
      clearWatchdog();
      speaking = false;
      currentUtterance = null;
      const errorMsg = `语音播放启动失败: ${error instanceof Error ? error.message : String(error)}`;
      console.error(errorMsg);
      errorCallback?.(errorMsg);
      return;
    }

    // onstart 看门狗：speak 提交后若时限内 onstart/onend/onerror 均未触发，
    // 视为被浏览器整条吞掉（Chrome 已知问题），主动触发完成回调，
    // 保证上层流程（如唤醒反馈后的开始收音）一定能续命而不是死等
    watchdogTimer = window.setTimeout(() => {
      watchdogTimer = null;
      if (currentUtterance !== utterance) {
        // 本轮 utterance 已被取代/清理（stop/destroy/新 speak 顶替），无需处理
        return;
      }
      console.warn('[TTS] onstart 看门狗触发：utterance 未产生任何事件（疑似被浏览器吞掉），主动结束本轮播报');
      finishSpeak(utterance);
    }, ONSTART_WATCHDOG_MS);
  }

  /**
   * 让路旧 utterance：先 cancel 并等其收尾（onend/onerror 或 120ms 超时，取先到）
   * 再 speak 新文本——规避 Chrome cancel() 后立即 speak() 整条丢弃新 utterance 的竞态
   */
  function cancelAndSpeakLater(text: string): void {
    clearWatchdog();
    const old = currentUtterance;
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      clearCancelSettleTimer();
      startSpeak(text);
    };
    if (old) {
      // 给旧 utterance 挂一次性收尾监听：cancel 后 Chrome 通常会派发 onend 或
      // onerror('interrupted'/'canceled')，先到即视为收尾完成；不派发任何事件的
      // 情况由 120ms 超时保底。覆盖原回调——旧 utterance 的结束不应触发本轮
      // endCallback（"说完"语义属于接续的新 utterance）
      old.onstart = null;
      old.onend = settle;
      old.onerror = settle;
    }
    speechSynthesis.cancel();
    speaking = false;
    currentUtterance = null;
    cancelSettleTimer = window.setTimeout(settle, CANCEL_SETTLE_TIMEOUT_MS);
  }

  /**
   * 播放文本
   */
  function speak(text: string): void {
    // 检查浏览器支持
    if (!('speechSynthesis' in window)) {
      const error = '当前浏览器不支持语音合成功能';
      console.error(error);
      errorCallback?.(error);
      return;
    }

    // 上一轮 speak 若还在 cancel 收尾等待中（新 utterance 尚未发出），本轮取代之
    const wasSettling = cancelSettleTimer !== null;
    clearCancelSettleTimer();

    // 有 utterance 在途（播放中/待播），或刚 cancel 完收尾事件未确认时，不能直接
    // speak——先走 cancel 收尾等待，再播新文本
    if (speaking || currentUtterance || wasSettling) {
      cancelAndSpeakLater(text);
      return;
    }
    startSpeak(text);
  }

  /**
   * 停止播放
   */
  function stop(): void {
    if (!('speechSynthesis' in window)) {
      return;
    }

    // 取消尚在 cancel 收尾等待中的延迟 speak（stop 语义 = 不再播任何东西）
    clearCancelSettleTimer();
    clearWatchdog();

    const utterance = currentUtterance;
    // 取消所有正在播放的语音
    speechSynthesis.cancel();

    // 重置状态
    speaking = false;
    currentUtterance = null;

    // 摘掉旧 utterance 的事件回调：被 cancel 的 utterance 仍可能异步派发
    // onend/onerror，不摘掉会误触发 endCallback/errorCallback
    if (utterance) {
      utterance.onstart = null;
      utterance.onend = null;
      utterance.onerror = null;
    }
  }

  /**
   * 获取可用语音列表
   */
  function getVoices(): SpeechSynthesisVoice[] {
    if (!('speechSynthesis' in window)) {
      return [];
    }
    return speechSynthesis.getVoices();
  }

  /**
   * 更新配置
   */
  function updateOptions(newOptions: Partial<TTSOptions>): void {
    currentOptions = { ...currentOptions, ...newOptions };
  }

  /**
   * 销毁实例，清理所有资源
   */
  function destroy(): void {
    // 停止播放
    stop();
    // 移除voiceschanged监听
    if (voicesChangedHandler && 'speechSynthesis' in window) {
      speechSynthesis.onvoiceschanged = null;
      voicesChangedHandler = null;
    }
    // 清空回调
    endCallback = undefined;
    startCallback = undefined;
    errorCallback = undefined;
    // 清空当前utterance的回调
    if (currentUtterance) {
      currentUtterance.onstart = null;
      currentUtterance.onend = null;
      currentUtterance.onerror = null;
      currentUtterance = null;
    }
  }

  // 返回控制器实例
  const controller: TTSController = {
    speak,
    stop,
    destroy,
    get isSpeaking() {
      return speaking;
    },
    get options() {
      return { ...currentOptions };
    },
    updateOptions,
    getVoices,
    set onEnd(callback: (() => void) | undefined) {
      endCallback = callback;
    },
    set onStart(callback: (() => void) | undefined) {
      startCallback = callback;
    },
    set onError(callback: ((error: string) => void) | undefined) {
      errorCallback = callback;
    },
  };

  // 监听语音列表加载完成（某些浏览器需要异步加载语音列表）
  if ('speechSynthesis' in window) {
    voicesChangedHandler = () => {
      // 语音列表已加载
    };
    speechSynthesis.onvoiceschanged = voicesChangedHandler;
  }

  return controller;
}

/**
 * 检查浏览器是否支持 TTS
 */
export function isTTSSupported(): boolean {
  return 'speechSynthesis' in window;
}

/**
 * 获取中文语音列表
 */
export function getChineseVoices(): SpeechSynthesisVoice[] {
  if (!('speechSynthesis' in window)) {
    return [];
  }
  return speechSynthesis.getVoices().filter(voice =>
    voice.lang.startsWith('zh') || voice.lang.includes('CN')
  );
}
