/**
 * 浏览器语音识别模块
 * 封装 Web Speech API（SpeechRecognition / webkitSpeechRecognition）
 * 支持 interim results（中间结果）和 continuous mode（连续识别）
 * 提供完整的 TypeScript 类型定义和错误处理
 */

/**
 * 语音识别结果接口
 */
export interface SpeechRecognitionResult {
  /** 是否为最终结果 */
  isFinal: boolean;
  /** 识别的文本内容 */
  transcript: string;
}

/**
 * 语音识别错误信息
 */
export interface SpeechRecognitionError {
  /** 错误类型 */
  error: string;
  /** 错误消息 */
  message: string;
}

/**
 * 浏览器语音识别控制器接口
 */
export interface BrowserSpeechRecognition {
  /** 启动语音识别 */
  start(): void;
  /** 停止语音识别 */
  stop(): void;
  /** 中止语音识别（立即停止，不等待当前语句完成） */
  abort(): void;
  /** 识别结果回调 */
  onResult: (result: SpeechRecognitionResult) => void;
  /** 错误回调 */
  onError: (error: string) => void;
  /** 结束回调 */
  onEnd: () => void;
  /** 开始回调 */
  onStart: () => void;
  /** 当前是否正在识别 */
  readonly isListening: boolean;
  /** 更新配置 */
  updateConfig: (config: Partial<SpeechRecognitionConfig>) => void;
  /** 销毁实例，清理所有资源 */
  destroy: () => void;
}

/**
 * 语音识别配置接口
 */
export interface SpeechRecognitionConfig {
  /** 语言代码，默认 'zh-CN' */
  lang: string;
  /** 是否连续识别，默认 false */
  continuous: boolean;
  /** 是否返回中间结果，默认 true */
  interimResults: boolean;
  /** 是否启用调试日志，默认 false */
  debug: boolean;
}

/**
 * 默认配置
 */
const DEFAULT_CONFIG: SpeechRecognitionConfig = {
  lang: 'zh-CN',
  continuous: false,
  interimResults: true,
  debug: false,
};

/**
 * 获取 SpeechRecognition 构造函数
 */
function getSpeechRecognitionConstructor(): any {
  if ('SpeechRecognition' in window) {
    return (window as any).SpeechRecognition;
  }
  if ('webkitSpeechRecognition' in window) {
    return (window as any).webkitSpeechRecognition;
  }
  return null;
}

/**
 * 创建浏览器语音识别控制器
 * 
 * @param config 配置选项
 * @param callbacks 回调函数
 * @returns BrowserSpeechRecognition 控制器实例
 * 
 * @example
 * ```typescript
 * const speech = createBrowserSpeechRecognition(
 *   { lang: 'zh-CN', continuous: true, interimResults: true },
 *   {
 *     onResult: (result) => {
 *       if (result.isFinal) {
 *         console.log('最终结果:', result.transcript);
 *       } else {
 *         console.log('中间结果:', result.transcript);
 *       }
 *     },
 *     onError: (error) => console.error('错误:', error),
 *     onEnd: () => console.log('识别结束'),
 *   }
 * );
 * 
 * speech.start();
 * ```
 */
export function createBrowserSpeechRecognition(
  config: Partial<SpeechRecognitionConfig> = {},
  callbacks: {
    onResult?: (result: SpeechRecognitionResult) => void;
    onError?: (error: string) => void;
    onEnd?: () => void;
    onStart?: () => void;
  } = {}
): BrowserSpeechRecognition {
  // 合并默认配置
  let speechConfig: SpeechRecognitionConfig = { ...DEFAULT_CONFIG, ...config };

  // 内部状态
  let isListening = false;
  let recognition: any = null;
  let isManualStop = false;
  let errorRestartTimer: number | null = null;

  // 回调函数
  let resultCallback = callbacks.onResult;
  let errorCallback = callbacks.onError;
  let endCallback = callbacks.onEnd;
  let startCallback = callbacks.onStart;

  /**
   * 初始化 SpeechRecognition 实例
   */
  function initRecognition(): void {
    if (!isSpeechRecognitionSupported()) {
      const errorMsg = '当前浏览器不支持语音识别功能';
      console.error(errorMsg);
      errorCallback?.(errorMsg);
      return;
    }

    const SpeechRecognitionConstructor = getSpeechRecognitionConstructor();
    if (!SpeechRecognitionConstructor) {
      const errorMsg = '无法获取 SpeechRecognition 构造函数';
      console.error(errorMsg);
      errorCallback?.(errorMsg);
      return;
    }

    try {
      // 创建识别实例
      recognition = new SpeechRecognitionConstructor();

      // 设置参数
      recognition.lang = speechConfig.lang;
      recognition.continuous = speechConfig.continuous;
      recognition.interimResults = speechConfig.interimResults;

      // 识别结果处理
      recognition.onresult = (event: any) => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 收到结果:', event);
        }

        // 遍历所有结果（支持多语句）
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          const transcript = result[0].transcript;
          const isFinal = result.isFinal;

          if (speechConfig.debug) {
            console.log(`[SpeechRecognition] 结果 ${i}:`, {
              transcript,
              isFinal,
            });
          }

          // 触发结果回调
          resultCallback?.({
            isFinal,
            transcript,
          });
        }
      };

      // 识别开始
      recognition.onstart = () => {
        isListening = true;
        isManualStop = false;

        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 识别已启动');
        }

        startCallback?.();
      };

      // 识别结束
      recognition.onend = () => {
        isListening = false;

        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 识别已结束，手动停止:', isManualStop);
        }

        // 如果是连续模式且不是手动停止，自动重启
        if (speechConfig.continuous && !isManualStop) {
          if (speechConfig.debug) {
            console.log('[SpeechRecognition] 连续模式，自动重启识别');
          }
          try {
            recognition.start();
          } catch (error) {
            console.error('[SpeechRecognition] 自动重启失败:', error);
          }
        } else {
          endCallback?.();
        }
      };

      // 识别错误
      recognition.onerror = (event: any) => {
        const errorType = event.error || 'unknown';
        let errorMessage = `语音识别错误：${errorType}`;

        // 详细错误信息映射
        switch (errorType) {
          case 'no-speech':
            errorMessage = '未检测到语音，请说话';
            break;
          case 'audio-capture':
            errorMessage = '无法访问麦克风，请检查权限';
            break;
          case 'not-allowed':
            errorMessage = '麦克风权限被拒绝，请在浏览器设置中允许';
            break;
          case 'network':
            errorMessage = '网络错误，请检查网络连接';
            break;
          case 'aborted':
            errorMessage = '语音识别被中止';
            break;
          case 'service-not-allowed':
            errorMessage = '语音识别服务被阻止';
            break;
          case 'bad-grammar':
            errorMessage = '语法错误';
            break;
          case 'language-not-supported':
            errorMessage = `不支持的语言：${speechConfig.lang}`;
            break;
          default:
            errorMessage = `语音识别错误：${errorType} (${event.message || '未知错误'})`;
        }

        if (speechConfig.debug) {
          console.error('[SpeechRecognition] 错误详情:', event);
        }

        console.error(errorMessage);
        errorCallback?.(errorMessage);

        // 非手动停止时，如果是连续模式，尝试重启
        if (speechConfig.continuous && !isManualStop && errorType !== 'not-allowed') {
          errorRestartTimer = window.setTimeout(() => {
            errorRestartTimer = null;
            try {
              recognition.start();
            } catch (e) {
              console.error('[SpeechRecognition] 错误后重启失败:', e);
            }
          }, 1000);
        }
      };

      // 识别结束（旧版兼容性）
      recognition.onaudiostart = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 音频输入开始');
        }
      };

      recognition.onaudioend = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 音频输入结束');
        }
      };

      recognition.onsoundstart = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 检测到声音');
        }
      };

      recognition.onsoundend = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 声音结束');
        }
      };

      recognition.onspeechstart = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 检测到语音');
        }
      };

      recognition.onspeechend = () => {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 语音结束');
        }
      };

    } catch (error) {
      const errorMsg = `初始化语音识别失败：${error instanceof Error ? error.message : String(error)}`;
      console.error(errorMsg);
      errorCallback?.(errorMsg);
    }
  }

  /**
   * 启动语音识别
   */
  function start(): void {
    if (isListening) {
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 已经在监听中，跳过启动');
      }
      return;
    }

    // 如果实例不存在或已关闭，重新初始化
    if (!recognition) {
      initRecognition();
    }

    try {
      recognition.start();
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 启动成功');
      }
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      
      // 如果是"recognition has already started"错误，先中止再重启
      if (errorMsg.includes('recognition has already started')) {
        if (speechConfig.debug) {
          console.log('[SpeechRecognition] 识别已在运行，中止后重新启动');
        }
        try {
          recognition.abort();
        } catch (e) {
          // 忽略中止错误
        }
        // 重新初始化
        recognition = null;
        initRecognition();
        try {
          recognition?.start();
          isListening = true;
        } catch (e) {
          console.error('[SpeechRecognition] 重新启动失败:', e);
        }
        return;
      }
      
      const fullErrorMsg = `启动语音识别失败：${errorMsg}`;
      console.error(fullErrorMsg);
      errorCallback?.(fullErrorMsg);
    }
  }

  /**
   * 停止语音识别（等待当前语句完成）
   */
  function stop(): void {
    if (!recognition) {
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 识别实例不存在，跳过停止');
      }
      return;
    }

    isManualStop = true;
    isListening = false;  // 立即设置为false，避免异步问题

    // 清理错误重启定时器
    if (errorRestartTimer !== null) {
      clearTimeout(errorRestartTimer);
      errorRestartTimer = null;
    }

    try {
      recognition.stop();
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 停止成功');
      }
    } catch (error) {
      const errorMsg = `停止语音识别失败：${error instanceof Error ? error.message : String(error)}`;
      console.error(errorMsg);
      errorCallback?.(errorMsg);
    }
  }

  /**
   * 中止语音识别（立即停止，不等待当前语句完成）
   */
  function abort(): void {
    if (!recognition) {
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 识别实例不存在，跳过中止');
      }
      return;
    }

    isManualStop = true;
    isListening = false;  // 立即设置为false，避免异步问题

    // 清理错误重启定时器
    if (errorRestartTimer !== null) {
      clearTimeout(errorRestartTimer);
      errorRestartTimer = null;
    }

    try {
      recognition.abort();
      if (speechConfig.debug) {
        console.log('[SpeechRecognition] 中止成功');
      }
    } catch (error) {
      const errorMsg = `中止语音识别失败：${error instanceof Error ? error.message : String(error)}`;
      console.error(errorMsg);
      errorCallback?.(errorMsg);
    }
  }

  /**
   * 更新配置
   */
  function updateConfig(newConfig: Partial<SpeechRecognitionConfig>): void {
    const wasListening = isListening;

    // 如果正在监听，需要先停止
    if (wasListening) {
      stop();
    }

    // 更新配置
    speechConfig = { ...speechConfig, ...newConfig };

    // 如果之前在监听，重新启动
    if (wasListening) {
      // 重新初始化以应用新配置
      recognition = null;
      initRecognition();
      start();
    }

    if (speechConfig.debug) {
      console.log('[SpeechRecognition] 配置已更新:', speechConfig);
    }
  }

  /**
   * 销毁实例，清理所有资源
   */
  function destroy(): void {
    // 清理定时器
    if (errorRestartTimer !== null) {
      clearTimeout(errorRestartTimer);
      errorRestartTimer = null;
    }
    // 停止识别
    if (recognition) {
      isManualStop = true;
      try { recognition.abort(); } catch (e) { /* ignore */ }
      recognition.onresult = null;
      recognition.onstart = null;
      recognition.onend = null;
      recognition.onerror = null;
      recognition.onaudiostart = null;
      recognition.onaudioend = null;
      recognition.onsoundstart = null;
      recognition.onsoundend = null;
      recognition.onspeechstart = null;
      recognition.onspeechend = null;
      recognition = null;
    }
    // 清空回调
    resultCallback = undefined;
    errorCallback = undefined;
    endCallback = undefined;
    startCallback = undefined;
    isListening = false;
  }

  // 初始化识别实例
  initRecognition();

  // 返回控制器实例
  return {
    start,
    stop,
    abort,
    updateConfig,
    destroy,
    get isListening() {
      return isListening;
    },
    set onResult(callback: (result: SpeechRecognitionResult) => void) {
      resultCallback = callback;
    },
    set onError(callback: (error: string) => void) {
      errorCallback = callback;
    },
    set onEnd(callback: () => void) {
      endCallback = callback;
    },
    set onStart(callback: () => void) {
      startCallback = callback;
    },
  };
}

/**
 * 检查浏览器是否支持语音识别
 */
export function isSpeechRecognitionSupported(): boolean {
  return 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window;
}

/**
 * 获取支持的语音识别语言列表
 * 注意：这个列表是 Web Speech API 规范中定义的标准语言代码
 * 实际支持情况因浏览器而异
 */
export function getSupportedLanguages(): string[] {
  if (!isSpeechRecognitionSupported()) {
    return [];
  }

  // 常见支持的语言列表
  const languages = [
    'zh-CN',      // 中文（中国）
    'zh-TW',      // 中文（台湾）
    'zh-HK',      // 中文（香港）
    'en-US',      // 英语（美国）
    'en-GB',      // 英语（英国）
    'ja-JP',      // 日语
    'ko-KR',      // 韩语
    'es-ES',      // 西班牙语
    'fr-FR',      // 法语
    'de-DE',      // 德语
    'it-IT',      // 意大利语
    'pt-BR',      // 葡萄牙语（巴西）
    'ru-RU',      // 俄语
    'ar-SA',      // 阿拉伯语
    'hi-IN',      // 印地语
  ];

  return languages;
}

/**
 * 创建简化的语音识别器（仅获取最终结果）
 * 
 * @param onFinalResult 最终结果回调
 * @param lang 语言代码
 * @returns BrowserSpeechRecognition 实例
 */
export function createSimpleSpeechRecognition(
  onFinalResult: (text: string) => void,
  lang: string = 'zh-CN'
): BrowserSpeechRecognition {
  return createBrowserSpeechRecognition(
    {
      lang,
      continuous: false,
      interimResults: false,
      debug: false,
    },
    {
      onResult: (result) => {
        if (result.isFinal) {
          onFinalResult(result.transcript);
        }
      },
    }
  );
}
