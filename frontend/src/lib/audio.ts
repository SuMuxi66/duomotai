import { createVAD } from './vad';

/**
 * 音频降采样处理
 * @param buffer 原始音频数据
 * @param inputRate 输入采样率
 * @param outputRate 输出采样率
 * @returns 降采样后的音频数据
 */
function downsampleBuffer(buffer: Float32Array, inputRate: number, outputRate: number) {
  if (outputRate === inputRate) return buffer;
  const ratio = inputRate / outputRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  let offset = 0;
  for (let i = 0; i < newLen; i++) {
    const nextOffset = Math.round((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = offset; j < nextOffset && j < buffer.length; j++) {
      sum += buffer[j];
      count++;
    }
    result[i] = sum / Math.max(1, count);
    offset = nextOffset;
  }
  return result;
}

/**
 * Float32 数组转 16位 PCM
 * @param float32 Float32 音频数据
 * @returns Int16Array PCM 数据
 */
function floatTo16BitPCM(float32: Float32Array) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    let s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * 字节数组转 Base64 字符串
 * @param bytes 字节数组
 * @returns Base64 编码字符串
 */
function toBase64(bytes: Uint8Array) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

/**
 * 按键说话模式音频发送器接口
 */
export type AudioSender = {
  start: (opts?: { audioDeviceId?: string }) => Promise<void>;
  stop: () => Promise<void>;
};

/**
 * 连续录音模式配置接口
 */
export interface ContinuousRecorderConfig {
  /** 音频分块发送回调 */
  onChunk: (seq: number, pcm16_b64: string) => void;
  /** 录音开始回调 */
  onRecordingStart?: () => void;
  /** 录音结束回调 */
  onRecordingEnd?: () => void;
  /** 检测到用户说话回调（用于打断） */
  onSpeechDetected?: () => void;
  /** 音频分块时长（毫秒），默认 500ms */
  chunkMs?: number;
  /** 目标采样率，默认 16000 */
  targetSampleRate?: number;
  /** VAD 音量阈值（0-100），默认 15 */
  volumeThreshold?: number;
  /** VAD 静音检测秒数，默认 3秒 */
  silenceSeconds?: number;
  /** 是否启用调试日志 */
  debug?: boolean;
  /** 音频设备ID */
  audioDeviceId?: string;
  /** 检测到静音时是否自动停止录音，默认 true */
  autoStopOnSilence?: boolean;
}

/**
 * 连续录音控制器接口
 */
export interface ContinuousRecorder {
  /** 启动连续录音 */
  start: (opts?: { audioDeviceId?: string }) => Promise<void>;
  /** 停止连续录音 */
  stop: () => void;
  /** 当前是否正在录音 */
  readonly isRecording: boolean;
}

/**
 * 创建按键说话模式音频发送器
 * @param opts 配置选项
 * @returns AudioSender 实例
 */
export function createPushToTalkSender(opts: {
  onChunk: (seq: number, pcm16_b64: string, isLast: boolean) => void;
  onEnd: (lastSeq: number) => void;
  chunkMs?: number;
  targetSampleRate?: number;
  audioDeviceId?: string;
}) : AudioSender {
  const chunkMs = opts.chunkMs ?? 500;
  const targetRate = opts.targetSampleRate ?? 16000;
  const audioDeviceId = opts.audioDeviceId;

  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;

  let seq = 0;
  let lastFlush = 0;
  let acc: Float32Array[] = [];

  async function start() {
    seq = 0;
    lastFlush = performance.now();
    acc = [];

    // 获取音频流，可以指定设备ID
    const audioConstraints: MediaTrackConstraints = {
      sampleRate: targetRate,
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    
    if (audioDeviceId) {
      stream = await navigator.mediaDevices.getUserMedia({ 
        audio: { ...audioConstraints, deviceId: { exact: audioDeviceId } } 
      });
    } else {
      stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
    }
    ctx = new AudioContext();
    source = ctx.createMediaStreamSource(stream);
    // @ts-ignore ScriptProcessor is deprecated but simple for MVP
    processor = ctx.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      acc.push(new Float32Array(input));

      const now = performance.now();
      if (now - lastFlush >= chunkMs) {
        // concat
        const total = acc.reduce((n, a) => n + a.length, 0);
        const buf = new Float32Array(total);
        let off = 0;
        for (const a of acc) { buf.set(a, off); off += a.length; }
        acc = [];
        lastFlush = now;

        const ds = downsampleBuffer(buf, ctx!.sampleRate, targetRate);
        const pcm16 = floatTo16BitPCM(ds);
        const b64 = toBase64(new Uint8Array(pcm16.buffer));
        opts.onChunk(seq++, b64, false);
      }
    };

    source.connect(processor);
    processor.connect(ctx.destination);
  }

  async function stop() {
    // flush remaining
    if (ctx && acc.length) {
      const total = acc.reduce((n, a) => n + a.length, 0);
      const buf = new Float32Array(total);
      let off = 0;
      for (const a of acc) { buf.set(a, off); off += a.length; }
      acc = [];
      const ds = downsampleBuffer(buf, ctx.sampleRate, targetRate);
      const pcm16 = floatTo16BitPCM(ds);
      const b64 = toBase64(new Uint8Array(pcm16.buffer));
      opts.onChunk(seq++, b64, true);
    }

    const lastSeq = seq - 1;
    opts.onEnd(lastSeq);

    processor?.disconnect();
    source?.disconnect();
    ctx?.close();
    stream?.getTracks().forEach((t) => t.stop());

    processor = null;
    source = null;
    ctx = null;
    stream = null;
  }

  return { start, stop };
}

/**
 * 创建连续录音模式录音器
 * 集成 VAD 模块，实现自动开始/停止录音
 * 支持音频分块发送和打断检测
 * 
 * @param config 配置选项
 * @returns ContinuousRecorder 实例
 */
export function createContinuousRecorder(config: ContinuousRecorderConfig): ContinuousRecorder {
  const {
    onChunk,
    onRecordingStart,
    onRecordingEnd,
    onSpeechDetected,
    chunkMs = 500,
    targetSampleRate = 16000,
    volumeThreshold = 15,
    silenceSeconds = 3,
    debug = false,
    autoStopOnSilence = true,
  } = config;

  // 内部状态
  let _isRecording = false;
  let isInterrupted = false;

  // Audio API 相关对象
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;

  // 音频处理相关
  let seq = 0;
  let lastFlush = 0;
  let acc: Float32Array[] = [];

  // VAD 控制器
  let vadController: ReturnType<typeof createVAD> | null = null;

  // 是否已触发录音开始回调
  let hasTriggeredStart = false;

  /**
   * 处理音频数据并发送分块
   */
  function processAndSendChunk(_isLast: boolean = false) {
    if (!ctx || acc.length === 0) return;

    // 合并累积的音频数据
    const total = acc.reduce((n, a) => n + a.length, 0);
    const buf = new Float32Array(total);
    let off = 0;
    for (const a of acc) {
      buf.set(a, off);
      off += a.length;
    }
    acc = [];

    // 降采样并转换为 PCM16
    const ds = downsampleBuffer(buf, ctx.sampleRate, targetSampleRate);
    const pcm16 = floatTo16BitPCM(ds);
    const b64 = toBase64(new Uint8Array(pcm16.buffer));

    // 发送音频分块
    onChunk(seq++, b64);

    if (debug) {
      console.log(`[ContinuousRecorder] 发送音频分块 seq=${seq - 1}, 大小=${b64.length}bytes`);
    }
  }

  /**
   * 启动连续录音
   */
  async function start(): Promise<void> {
    if (_isRecording) {
      if (debug) {
        console.log('[ContinuousRecorder] 已经在录音中，跳过启动');
      }
      return;
    }

    try {
      // 重置状态
      _isRecording = true;
      isInterrupted = false;
      seq = 0;
      lastFlush = performance.now();
      acc = [];
      hasTriggeredStart = false;

      // 获取麦克风权限，支持指定设备ID
      const audioConfig = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      };
      
      if (config.audioDeviceId) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: { ...audioConfig, deviceId: { exact: config.audioDeviceId } },
            video: false,
          });
        } catch (err: unknown) {
          if (err instanceof DOMException) {
            if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
              throw new Error("麦克风权限被拒绝，请在浏览器设置中允许访问麦克风");
            }
            if (err.name === "NotFoundError" || err.name === "DevicesNotFoundError") {
              throw new Error("未找到指定的麦克风设备，请检查设备连接");
            }
            if (err.name === "NotReadableError" || err.name === "TrackStartError") {
              throw new Error("麦克风设备被其他应用占用，请关闭其他使用麦克风的应用");
            }
          }
          throw new Error(`无法启动录音: ${err instanceof Error ? err.message : String(err)}`);
        }
      } else {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConfig,
          video: false,
        });
      }

      // 创建 AudioContext
      ctx = new AudioContext();
      source = ctx.createMediaStreamSource(stream);

      // @ts-ignore ScriptProcessor is deprecated but simple for MVP
      processor = ctx.createScriptProcessor(4096, 1, 1);

      // 创建 VAD 控制器，共享录音器的 MediaStream
      vadController = createVAD(
        {
          volumeThreshold,
          silenceSeconds,
          sampleInterval: 50,
          debug,
          stream: stream!,
        },
        {
          onSpeechStart: () => {
            if (debug) {
              console.log('[ContinuousRecorder] VAD 检测到说话开始');
            }

            // 触发录音开始回调（仅第一次）
            if (!hasTriggeredStart) {
              hasTriggeredStart = true;
              onRecordingStart?.();
            }

            // 触发说话检测回调（用于打断）
            onSpeechDetected?.();
          },
          onSpeechEnd: () => {
            if (debug) {
              console.log('[ContinuousRecorder] VAD 检测到说话结束');
            }

            // 只有在 autoStopOnSilence 为 true 时才自动停止
            if (autoStopOnSilence && _isRecording && !isInterrupted) {
              processAndSendChunk(true);
              onRecordingEnd?.();
              stop();
            }
          },
          onError: (error) => {
            console.error('[ContinuousRecorder] VAD 错误:', error);
          },
        }
      );

      // 音频处理回调
      processor.onaudioprocess = (e) => {
        if (!_isRecording || isInterrupted) return;

        const input = e.inputBuffer.getChannelData(0);
        acc.push(new Float32Array(input));

        const now = performance.now();
        if (now - lastFlush >= chunkMs) {
          processAndSendChunk();
          lastFlush = now;
        }
      };

      // 连接音频节点
      source.connect(processor);
      processor.connect(ctx.destination);

      // 启动 VAD 检测
      await vadController.start();

      if (debug) {
        console.log('[ContinuousRecorder] 录音已启动');
      }
    } catch (error) {
      console.error('[ContinuousRecorder] 启动失败:', error);
      stop();
      throw error;
    }
  }

  /**
   * 停止连续录音
   */
  function stop(): void {
    if (!_isRecording) return;

    _isRecording = false;
    isInterrupted = true;

    // 发送剩余数据
    if (acc.length > 0) {
      processAndSendChunk(true);
    }

    // 停止 VAD
    vadController?.stop();
    vadController = null;

    // 断开音频连接
    processor?.disconnect();
    source?.disconnect();
    ctx?.close();
    stream?.getTracks().forEach((t) => t.stop());

    // 重置状态
    processor = null;
    source = null;
    ctx = null;
    stream = null;
    acc = [];

    if (debug) {
      console.log('[ContinuousRecorder] 录音已停止');
    }
  }

  return {
    start,
    stop,
    get isRecording() {
      return _isRecording;
    },
  };
}
