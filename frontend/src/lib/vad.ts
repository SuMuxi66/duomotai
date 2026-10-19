/**
 * VAD (Voice Activity Detection) 语音激活检测模块
 * 基于 Web Audio API 实现实时音量检测和人声/静音判断
 */

/**
 * VAD 配置参数接口
 */
export interface VADConfig {
  /** 音量阈值 (0-100)，超过此值判定为说话 */
  volumeThreshold: number;
  /** 静音检测帧数，连续 N 帧低于阈值判定为静音（已废弃，使用 silenceSeconds） */
  silenceFrames?: number;
  /** 静音检测秒数，声音低于阈值持续 N 秒后判定为静音 */
  silenceSeconds?: number;
  /** 采样间隔（毫秒），音量检测的频率 */
  sampleInterval: number;
  /** 是否启用调试日志 */
  debug?: boolean;
  /** 外部传入的 MediaStream，若提供则不再自行获取麦克风流 */
  stream?: MediaStream;
}

/**
 * VAD 状态回调接口
 */
export interface VADCallbacks {
  /** 检测到说话开始时触发 */
  onSpeechStart?: () => void;
  /** 检测到说话结束时触发 */
  onSpeechEnd?: () => void;
  /** 音量变化时触发，返回当前音量 (0-100) */
  onVolumeChange?: (volume: number) => void;
  /** 错误发生时触发 */
  onError?: (error: Error) => void;
}

/**
 * VAD 状态枚举
 */
export enum VADState {
  /** 静音状态 */
  SILENCE = 'silence',
  /** 说话状态 */
  SPEECH = 'speech',
}

/**
 * VAD 控制器接口
 */
export interface VADController {
  /** 启动 VAD 检测 */
  start: () => Promise<void>;
  /** 停止 VAD 检测 */
  stop: () => void;
  /** 获取当前 VAD 状态 */
  getState: () => VADState;
  /** 获取当前音量 */
  getVolume: () => number;
  /** 更新配置参数 */
  updateConfig: (config: Partial<VADConfig>) => void;
}

/**
 * 默认 VAD 配置
 */
const DEFAULT_CONFIG: VADConfig = {
  volumeThreshold: 40,
  silenceSeconds: 10,
  sampleInterval: 50,
  debug: false,
};

/**
 * 创建 VAD 控制器
 * @param config VAD 配置参数
 * @param callbacks 状态回调函数
 * @returns VAD 控制器实例
 */
export function createVAD(
  config: Partial<VADConfig> = {},
  callbacks: VADCallbacks = {}
): VADController {
  // 合并默认配置
  let vadConfig: VADConfig = { ...DEFAULT_CONFIG, ...config };

  // 内部状态
  let state: VADState = VADState.SILENCE;
  let currentVolume: number = 0;
  let silenceStartTime: number = 0;
  let isRunning: boolean = false;

  // Audio API 相关对象
  let audioContext: AudioContext | null = null;
  let mediaStream: MediaStream | null = null;
  let analyser: AnalyserNode | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let animationFrameId: number | null = null;
  let ownStream = false;

  // 数据缓冲区
  let dataArray: Uint8Array | null = null;

  /**
   * 计算当前音量 (RMS)
   */
  function calculateVolume(): number {
    if (!analyser || !dataArray) return 0;

    analyser.getByteTimeDomainData(dataArray as Uint8Array<ArrayBuffer>);

    // 计算 RMS (均方根) 音量
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const normalized = (dataArray[i] - 128) / 128;
      sum += normalized * normalized;
    }
    const rms = Math.sqrt(sum / dataArray.length);

    // 转换为 0-100 的音量值
    return Math.min(100, Math.round(rms * 200));
  }

  /**
   * 处理音量检测逻辑
   */
  function processVolume(): void {
    if (!isRunning) return;

    currentVolume = calculateVolume();

    // 触发音量变化回调
    callbacks.onVolumeChange?.(currentVolume);

    // 计算静音秒数
    const silenceSecondsElapsed = silenceStartTime > 0 
      ? (Date.now() - silenceStartTime) / 1000 
      : 0;

    if (vadConfig.debug && state === VADState.SPEECH) {
      console.log(`[VAD] 音量: ${currentVolume}, 静音时间: ${silenceSecondsElapsed.toFixed(1)}秒`);
    }

    // 状态机逻辑
    if (state === VADState.SILENCE) {
      // 静音状态下，检测到音量超过阈值 -> 切换到说话状态
      if (currentVolume >= vadConfig.volumeThreshold) {
        state = VADState.SPEECH;
        silenceStartTime = 0;
        callbacks.onSpeechStart?.();

        if (vadConfig.debug) {
          console.log('[VAD] 检测到说话开始');
        }
      }
    } else if (state === VADState.SPEECH) {
      // 说话状态下，检测音量是否低于阈值
      if (currentVolume < vadConfig.volumeThreshold) {
        // 开始计时静音
        if (silenceStartTime === 0) {
          silenceStartTime = Date.now();
        }

        // 静音时间超过阈值 -> 切换到静音状态
        if (silenceSecondsElapsed >= (vadConfig.silenceSeconds || 10)) {
          state = VADState.SILENCE;
          silenceStartTime = 0;
          callbacks.onSpeechEnd?.();

          if (vadConfig.debug) {
            console.log('[VAD] 检测到说话结束，静音超过', vadConfig.silenceSeconds, '秒');
          }
        }
      } else {
        // 音量仍然高于阈值，重置静音计时
        silenceStartTime = 0;
      }
    }

    // 继续下一帧检测
    animationFrameId = requestAnimationFrame(processVolume);
  }

  /**
   * 启动 VAD 检测
   */
  async function start(): Promise<void> {
    if (isRunning) {
      if (vadConfig.debug) {
        console.log('[VAD] 已经在运行中，跳过启动');
      }
      return;
    }

    try {
      // 获取麦克风权限：优先使用外部传入的 stream
      if (vadConfig.stream) {
        mediaStream = vadConfig.stream;
        ownStream = false;
      } else {
        mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
          video: false,
        });
        ownStream = true;
      }

      // 创建 AudioContext
      audioContext = new AudioContext();

      // 创建分析器节点
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.8;

      // 创建数据缓冲区
      dataArray = new Uint8Array(analyser.frequencyBinCount);

      // 连接音频源
      source = audioContext.createMediaStreamSource(mediaStream);
      source.connect(analyser);

      // 标记为运行状态
      isRunning = true;
      state = VADState.SILENCE;
      silenceStartTime = 0;
      currentVolume = 0;

      if (vadConfig.debug) {
        console.log('[VAD] 启动成功，开始检测');
      }

      // 开始检测循环
      processVolume();
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      callbacks.onError?.(err);

      if (vadConfig.debug) {
        console.error('[VAD] 启动失败:', err);
      }

      // 清理资源
      stop();
      throw err;
    }
  }

  /**
   * 停止 VAD 检测
   */
  function stop(): void {
    if (!isRunning) return;

    isRunning = false;

    // 停止动画帧
    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }

    // 断开音频连接
    source?.disconnect();
    analyser?.disconnect();

    // 只有自己创建的 stream 才关闭 AudioContext 和停止 tracks
    if (ownStream) {
      if (audioContext && audioContext.state !== 'closed') {
        audioContext.close();
      }
      mediaStream?.getTracks().forEach((track) => track.stop());
    }

    // 重置状态
    source = null;
    analyser = null;
    audioContext = null;
    mediaStream = null;
    dataArray = null;
    ownStream = false;
    state = VADState.SILENCE;
    silenceStartTime = 0;
    currentVolume = 0;

    if (vadConfig.debug) {
      console.log('[VAD] 已停止');
    }
  }

  /**
   * 获取当前 VAD 状态
   */
  function getState(): VADState {
    return state;
  }

  /**
   * 获取当前音量
   */
  function getVolume(): number {
    return currentVolume;
  }

  /**
   * 更新配置参数
   */
  function updateConfig(newConfig: Partial<VADConfig>): void {
    vadConfig = { ...vadConfig, ...newConfig };

    if (vadConfig.debug) {
      console.log('[VAD] 配置已更新:', vadConfig);
    }
  }

  return {
    start,
    stop,
    getState,
    getVolume,
    updateConfig,
  };
}

/**
 * 创建简化的音量检测器（仅检测音量，不进行状态判断）
 * @param onVolume 音量变化回调
 * @param sampleInterval 采样间隔（毫秒）
 * @returns 控制器实例
 */
export function createVolumeDetector(
  onVolume: (volume: number) => void,
  sampleInterval: number = 50
): VADController {
  return createVAD(
    { sampleInterval, volumeThreshold: 0, silenceSeconds: 0 },
    { onVolumeChange: onVolume }
  );
}
