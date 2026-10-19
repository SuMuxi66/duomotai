/**
 * 唤醒词检测模块 - KWS 版（端侧 sherpa-onnx，替代云端 Web Speech API）
 *
 * 架构：
 *   LISTENING(KWS WS推流) → 命中 → AWAITING_FEEDBACK → startCollecting →
 *   COLLECTING(Web Speech指令识别) → 静音/超时 → sendResult → 回 LISTENING(KWS)
 *
 * 唤醒词检测走 RK3588 sherpa-onnx KWS（ws://192.168.1.127:8767/kws，真 KWS 模型，
 * ~100ms 延迟，断网可用，低误唤醒）；唤醒后的指令识别仍用浏览器 Web Speech
 * （单次 ASR，可接受云端）。两套音频交替使用麦克风，不冲突。
 *
 * 接口与旧版完全兼容，useWakeWord.ts 无需改动。
 */

export interface SimpleWakeWordListenerConfig {
  wakeWords: string[];
  onWakeWordDetected: (recognizedText: string) => void;
  audioDeviceId?: string;
  onStatusChange?: (status: string) => void;
  onError?: (errorType: string, message: string) => void;
  onTranscriptChange?: (text: string) => void;
  onWakeWordTriggered?: () => void; // 检测到唤醒词时立即触发（播放"你好，我在"）
  kwsUrl?: string; // KWS WS 服务地址，默认 ws://192.168.1.248:8767/kws
}

enum State {
  IDLE = 'IDLE',
  LISTENING = 'IDLE_KWS', // KWS 持续推流监听唤醒词
  AWAITING_FEEDBACK = 'AWAITING_FEEDBACK',
  COLLECTING = 'COLLECTING',
}

// 繁简转换（指令文本处理，保留旧版逻辑）
const traditionalToSimplified: Record<string, string> = {
  '廚': '厨', '櫃': '柜', '顏': '颜', '麼': '么', '個': '个',
  '這': '这', '嗎': '吗', '會': '会', '說': '说', '來': '来',
};
function toSimplifiedChinese(text: string): string {
  return text.replace(/./g, c => traditionalToSimplified[c] || c);
}

const DEFAULT_KWS_URL = 'ws://192.168.1.248:8767/kws';

export async function createSimpleWakeWordListener(
  config: SimpleWakeWordListenerConfig
): Promise<{ start: () => Promise<void>; stop: () => void; destroy: () => void; startCollecting: () => void }> {
  const { onWakeWordDetected, onStatusChange, onError, onTranscriptChange, onWakeWordTriggered, audioDeviceId } = config;
  const kwsUrl = config.kwsUrl || DEFAULT_KWS_URL;

  let state: State = State.IDLE;
  let textBuffer = '';
  let silenceTimer: any = null;
  let maxTimer: any = null;
  let lastStatus = '';

  // KWS 相关
  let audioCtx: AudioContext | null = null;
  let mediaStream: MediaStream | null = null;
  let scriptNode: ScriptProcessorNode | null = null;
  let ws: WebSocket | null = null;
  let kwsRunning = false;

  // Web Speech（指令收集用）
  let recognition: any = null;
  let isStarting = false;
  let restartTimer: any = null;
  let rapidRestartCount = 0;
  let lastStartAt = 0;

  const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

  function updateStatus(status: string) {
    if (lastStatus === status) return;
    lastStatus = status;
    onStatusChange?.(status);
  }
  function updateTranscript(text: string) { onTranscriptChange?.(text.trim()); }

  // ============ KWS 推流（唤醒词检测）============
  async function startKws(): Promise<void> {
    if (kwsRunning) return;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: 16000,
          deviceId: audioDeviceId ? { exact: audioDeviceId } : undefined,
        } as any,
        video: false,
      });
      // AudioContext 16kHz
      const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
      const ctx: AudioContext = new Ctx({ sampleRate: 16000 });
      audioCtx = ctx;
      const source = ctx.createMediaStreamSource(mediaStream);
      // ScriptProcessor 兼容性好；buffer 4096 samples
      const snode = ctx.createScriptProcessor(4096, 1, 1);
      scriptNode = snode;
      snode.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!kwsRunning || !ws || ws.readyState !== WebSocket.OPEN) return;
        const input = e.inputBuffer.getChannelData(0); // float32 -1~1
        // float32 -> int16 PCM
        const len = input.length;
        const pcm16 = new Int16Array(len);
        for (let i = 0; i < len; i++) {
          let s = Math.max(-1, Math.min(1, input[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        ws.send(pcm16.buffer);
      };
      source.connect(snode);
      snode.connect(ctx.destination);

      // WS 连接
      ws = new WebSocket(kwsUrl);
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => {
        kwsRunning = true;
        updateStatus('唤醒词监听已启动(KWS)');
        console.log('[WakeWord] KWS WS 已连接，开始推流');
      };
      ws.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
          if (msg.event === 'wakeword') {
            console.log('[WakeWord] KWS 命中:', msg.keyword);
            onWakeWordTriggered?.();
            updateStatus('检测到唤醒词，等待反馈播放');
            state = State.AWAITING_FEEDBACK;
            textBuffer = '';
            updateTranscript('');
          }
        } catch (err) {
          // 忽略非 JSON
        }
      };
      ws.onerror = () => {
        console.warn('[WakeWord] KWS WS 错误');
        onError?.('kws-network', 'KWS WS error');
        // 重连
        scheduleKwsReconnect();
      };
      ws.onclose = () => {
        if (state === State.LISTENING) {
          console.warn('[WakeWord] KWS WS 断开，重连');
          scheduleKwsReconnect();
        }
      };
    } catch (e: any) {
      updateStatus('麦克风启动失败');
      onError?.('audio-capture', e?.message || 'mic failed');
      console.error('[WakeWord] KWS 启动失败:', e);
    }
  }

  let kwsReconnectTimer: any = null;
  function scheduleKwsReconnect(delay = 1000) {
    if (kwsReconnectTimer) return;
    updateStatus(`唤醒词连接中断，${Math.round(delay / 1000)}秒后恢复...`);
    kwsReconnectTimer = setTimeout(() => {
      kwsReconnectTimer = null;
      if (state !== State.LISTENING) return;
      stopKwsStream();
      startKws();
    }, delay);
  }

  function stopKwsStream() {
    kwsRunning = false;
    if (kwsReconnectTimer) { clearTimeout(kwsReconnectTimer); kwsReconnectTimer = null; }
    if (ws) { try { ws.onopen = null; ws.onmessage = null; ws.onerror = null; ws.onclose = null; ws.close(); } catch (e) {} ws = null; }
    if (scriptNode) { try { scriptNode.disconnect(); } catch (e) {} scriptNode = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
  }

  // ============ Web Speech（唤醒后指令收集）============
  function createRecognition() {
    if (recognition) {
      try { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; recognition.stop(); } catch (e) {}
    }
    recognition = new SpeechRecognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onstart = () => {
      isStarting = false;
      lastStartAt = Date.now();
      console.log('[WakeWord] 指令识别已启动');
    };
    recognition.onresult = (event: any) => {
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t; else interimText = t;
      }
      if (state === State.COLLECTING) {
        if (finalText) {
          textBuffer += finalText;
          resetSilenceTimer(3000);
        }
        updateTranscript(`${textBuffer}${interimText}`.trim());
        if (interimText) resetSilenceTimer(3000);
      }
    };
    recognition.onerror = (event: any) => {
      if (event.error === 'no-speech' || event.error === 'aborted') return;
      if (event.error === 'network') {
        onError?.('network', '指令识别网络错误');
        scheduleRecognitionRestart('network');
        return;
      }
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        updateStatus('麦克风权限被拒绝');
        onError?.(event.error, 'permission denied');
        return;
      }
      onError?.(event.error, `SpeechRecognition: ${event.error}`);
    };
    recognition.onend = () => {
      if (state === State.COLLECTING && !isStarting) scheduleRecognitionRestart('end');
    };
  }

  function scheduleRecognitionRestart(reason: 'network' | 'end') {
    if (state !== State.COLLECTING || isStarting || restartTimer) return;
    const now = Date.now();
    if (lastStartAt && now - lastStartAt < 4000) rapidRestartCount = Math.min(rapidRestartCount + 1, 6);
    else rapidRestartCount = 0;
    const delay = Math.min(4000, 800 + rapidRestartCount * 500);
    isStarting = true;
    updateStatus(reason === 'network' ? `指令识别网络异常，${Math.round(delay / 1000)}秒后重试...` : '指令识别恢复中...');
    restartTimer = setTimeout(() => {
      restartTimer = null;
      if (state !== State.COLLECTING) { isStarting = false; return; }
      try { recognition?.start(); } catch (e) { isStarting = false; scheduleRecognitionRestart(reason); }
    }, delay);
  }

  function startSilenceTimer(ms: number) {
    if (silenceTimer) clearTimeout(silenceTimer);
    silenceTimer = setTimeout(sendResult, ms);
  }
  function resetSilenceTimer(ms: number) {
    if (silenceTimer) clearTimeout(silenceTimer);
    silenceTimer = setTimeout(sendResult, ms);
  }
  function startMaxTimer() {
    if (maxTimer) clearTimeout(maxTimer);
    maxTimer = setTimeout(sendResult, 15000);
  }

  function sendResult() {
    let result = textBuffer.trim();
    result = result.replace(/^。|。$/g, '').replace(/([\u4e00-\u9fa5])。/g, '$1');
    result = result.replace(/^你好，我在[，,。.!！?？、；;：:\s]*/g, '').replace(/^你好，我在/g, '');
    if (result) {
      const simplified = toSimplifiedChinese(result);
      console.log('[WakeWord] 指令发送:', simplified);
      onWakeWordDetected(simplified);
    } else {
      onWakeWordDetected('');
    }
    textBuffer = '';
    updateTranscript('');
    // 停指令识别，切回 KWS 监听
    stopRecognition();
    if (state === State.COLLECTING) {
      state = State.LISTENING;
      startKws();
    }
  }

  function stopRecognition() {
    if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
    if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
    if (recognition) {
      try { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; recognition.stop(); } catch (e) {}
      recognition = null;
    }
    isStarting = false;
    rapidRestartCount = 0;
  }

  // ============ 对外接口 ============
  async function start(): Promise<void> {
    if (state !== State.IDLE) return;
    state = State.LISTENING;
    textBuffer = '';
    isStarting = false;
    rapidRestartCount = 0;
    updateStatus('唤醒词正在启动(KWS)...');
    await startKws();
  }

  function stop(): void {
    state = State.IDLE;
    stopKwsStream();
    stopRecognition();
    textBuffer = '';
    updateTranscript('');
    updateStatus('唤醒词监听已停止');
    console.log('[WakeWord] 已停止');
  }

  function destroy(): void {
    console.log('[WakeWord] 销毁监听器');
    stop();
    state = State.IDLE;
  }

  function startCollecting(): void {
    // 反馈播放完成，开始收集用户指令：停 KWS，启 Web Speech
    if (state === State.AWAITING_FEEDBACK) {
      console.log('[WakeWord] 反馈完成，切到指令收集');
      stopKwsStream();
      state = State.COLLECTING;
      updateStatus('请开始说话');
      if (!SpeechRecognition) {
        // 无 Web Speech 支持，直接回 KWS
        onError?.('unsupported', '浏览器不支持指令识别');
        sendResult();
        return;
      }
      createRecognition();
      try { isStarting = true; recognition.start(); } catch (e) { isStarting = false; }
      startSilenceTimer(5000);
      startMaxTimer();
    }
  }

  return { start, stop, destroy, startCollecting };
}

/**
 * 持续语音识别监听器（保留 Web Speech 实现，用于非唤醒场景；后续可换端侧 ASR）
 */
export function createContinuousSpeechListener(config: {
  onResult: (text: string) => void;
  onStart?: () => void;
  onEnd?: () => void;
  audioDeviceId?: string;
  silenceSeconds?: number;
  debug?: boolean;
}) {
  let recognition: any = null;
  let isListening = false;
  let isPaused = false;
  let isStarting = false;
  let restartTimer: ReturnType<typeof setTimeout> | null = null;
  let networkRetryCount = 0;
  const MAX_NETWORK_RETRIES = 5;
  let lastLogAt: Record<string, number> = {};

  function log(message: string, level: 'log' | 'warn' | 'error' = 'log', throttleMs = 1000) {
    const now = Date.now();
    const lastAt = lastLogAt[message] ?? 0;
    if (now - lastAt < throttleMs) return;
    lastLogAt[message] = now;
    console[level](message);
  }
  function clearRestartTimer() {
    if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
  }

  const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.error('[ContinuousSpeech] 浏览器不支持 SpeechRecognition');
    return { start: async () => {}, stop: () => {}, pause: () => {}, resume: async () => Promise.resolve() };
  }

  function startRecognition(delay = 0) {
    if (!isListening || isPaused) return;
    clearRestartTimer();
    restartTimer = setTimeout(() => {
      restartTimer = null;
      if (!isListening || isPaused || isStarting) return;
      if (!recognition) createRecognition();
      try { isStarting = true; recognition.start(); } catch (e) { isStarting = false; log('[ContinuousSpeech] 恢复失败', 'warn', 1500); startRecognition(600); }
    }, delay);
  }

  function createRecognition() {
    if (recognition) {
      try { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; recognition.stop(); } catch (e) {}
    }
    recognition = new SpeechRecognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onstart = () => { isStarting = false; networkRetryCount = 0; log('[ContinuousSpeech] 已开启', 'log', 1200); config.onStart?.(); };
    recognition.onresult = (event: any) => {
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) finalText += event.results[i][0].transcript;
      }
      if (finalText) {
        finalText = finalText.replace(/^。|。$/g, '').replace(/([\u4e00-\u9fa5])。/g, '$1').trim();
        if (finalText) { console.log('[ContinuousSpeech] 识别:', finalText); config.onResult(finalText); }
      }
    };
    recognition.onerror = (event: any) => {
      if (event.error === 'aborted' || event.error === 'no-speech') return;
      isStarting = false;
      log(`[ContinuousSpeech] 异常: ${event.error}`, 'warn', 1500);
      if (event.error === 'network' && !isPaused) {
        networkRetryCount++;
        if (networkRetryCount > MAX_NETWORK_RETRIES) { console.error('[ContinuousSpeech] 网络重试超限'); config.onResult = () => {}; isListening = false; return; }
        startRecognition(800);
      }
    };
    recognition.onend = () => { isStarting = false; config.onEnd?.(); if (isListening && !isPaused) startRecognition(400); };
  }

  async function start() { if (isListening) return; isListening = true; isPaused = false; createRecognition(); startRecognition(0); }
  function pause() { isPaused = true; clearRestartTimer(); if (recognition) { try { recognition.stop(); } catch (e) {} } log('[ContinuousSpeech] 已挂起', 'log', 1200); }
  function resume() { if (isListening) { isPaused = false; startRecognition(120); } return Promise.resolve(); }
  function stop() {
    isListening = false; isPaused = false; isStarting = false; clearRestartTimer();
    if (recognition) { try { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; recognition.stop(); } catch (e) {} recognition = null; }
    log('[ContinuousSpeech] 已停止', 'log', 1200);
  }

  return { start, stop, pause, resume };
}
