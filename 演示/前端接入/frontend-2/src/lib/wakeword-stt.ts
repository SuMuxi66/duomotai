// @ts-nocheck
/**
 * ★ 演示版 —— wakeword-stt.ts（大任务：语音交互·三大功能）
 *
 * 仅保留三大功能的核心代码：
 *   ① 降噪唤醒 —— 音量计算 + 有效人声推流 + KWS 命中"小云小云"
 *   ② 流式识别 —— 分片推流给端侧 ASR（16kHz PCM16），边走边转写
 *   ③ 随时打断 —— 回声能量门控，真人插话即打断 AI 播报
 *
 * 连续对话窗口、通话模式、后端 STT 回退等全功能已移除。
 * 完整实现见 frontend-2/src/lib/wakeword-stt.ts
 */
// =========================================================
// ★ 演示要点
//   ① 降噪唤醒  ★演示①-1 音频解锁 / ①-2 音量滤噪 / ①-3 KWS命中
//   ② 流式识别  ★演示②
//   ③ 随时打断  ★演示③
// =========================================================

/* =========================================================
 * ★演示①-1 浏览器音频解锁（关键坑）
 * Chrome 自动播放策略会把非用户手势创建的 AudioContext 挂起为
 * suspended，导致 onaudioprocess 不触发。需用户首次点击页面时
 * 强制 resume —— 所以演示前必须先"点一下页面"。
 * ========================================================= */
let ctx: AudioContext | null = null;   // 全局音频上下文
window.addEventListener('pointerdown', () => {   // 用户点击页面 → 解锁
  if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
}, { once: true });

/* =========================================================
 * ★演示①-2 降噪：实时音量计算，滤掉炒菜声/环境风噪，只留有效人声
 * ========================================================= */
function calculateRmsVolume(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / samples.length);
  return Math.min(100, Math.round(rms * 200));   // 归一化到 0-100 音量刻度
}

/* =========================================================
 * ★演示①-3 唤醒：只把有效人声推给端侧 KWS，命中"小云小云"才应答
 * ========================================================= */
const KWS_URL = "ws://192.168.1.127:8767/kws";   // 端侧唤醒词服务（RK3588）
const kwsWs = new WebSocket(KWS_URL);
kwsWs.binaryType = "arraybuffer";
kwsWs.onmessage = (ev) => {
  const msg = JSON.parse(ev.data as string);
  if (msg.event === "wakeword") onKwsHit();      // 命中唤醒词 → 应答"你好我在"
};

/* =========================================================
 * ★演示② 流式识别：按帧降采样 16kHz → 转 PCM16 → 推给端侧 ASR，
 * 边走边转写（partial 结果实时上屏）—— 无需等整句说完
 * ========================================================= */
const ASR_URL = "ws://192.168.1.248:8768/asr";   // 端侧流式识别服务
const asrWs = new WebSocket(ASR_URL);
asrWs.binaryType = "arraybuffer";

// ★演示②-0 节点2语音入口：开麦 → 建音频上下文 → 挂分片回调 → 识别结果上屏
async function startStreamingASR(): Promise<void> {
  if (!ctx) ctx = new AudioContext();                  // ① 建音频上下文
  await ctx.resume();                                  //    解锁（用户已点击过页面）
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); // ② 开麦
  const source = ctx.createMediaStreamSource(stream);  // ③ 音频源
  const processor = ctx.createScriptProcessor(4096, 1, 1); // 100ms级分片
  processor.onaudioprocess = (e) => {                  // ④ 核心：边走边转
    const input = e.inputBuffer.getChannelData(0);
    if (!echoGate.gateFrame(input)) return;            // ★演示③：AI播报期间回声被抑制
    const ds = downsampleBuffer(input, ctx!.sampleRate, 16000); // 降采样16k
    const pcm16 = floatTo16BitPCM(ds);                          // 转16bit PCM
    asrWs.send(pcm16.buffer);                                   // 推给端侧ASR
  };
  source.connect(processor);
  processor.connect(ctx.destination);
}

// ★演示②-1 语音入口的另一半：ASR 识别结果实时回写上屏
asrWs.onmessage = (ev) => {
  const msg = JSON.parse(ev.data as string);
  if (msg.type === "asr") {
    // partial = 未说完的中间结果，也实时上屏（边走边转写）
    setSttText(msg.isPartial ? msg.text : msg.text.trim());
  }
};

function downsampleBuffer(buffer: Float32Array, inputRate: number, outputRate: number) {
  const ratio = inputRate / outputRate;
  const out = new Float32Array(Math.round(buffer.length / ratio));
  for (let i = 0; i < out.length; i++) out[i] = buffer[Math.round(i * ratio)];
  return out;
}

function floatTo16BitPCM(float32: Float32Array): Int16Array {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

// ★节点2调用入口：await startStreamingASR() 即开始流式识别（processor 已内部挂载）

/* =========================================================
 * ★演示③ 随时打断：回声能量门控
 * AI 播报期间麦克风不停流，真人语音能量显著高于底噪 → 放行并打断；
 * AI 自身回声能量低 → 抑制，不会自己打断自己。
 * ========================================================= */
function createEchoGate() {
  let noiseFloor = 6;      // 底噪基线（无语音帧电平的滑动均值）
  const GATE_RISE = 14;    // 真人语音相对基线需达到的能量涨幅（经验值）
  let aiSpeaking = false;  // AI 是否正在播报

  function gateFrame(samples: Float32Array): boolean {
    const rms = calculateRmsVolume(samples);
    if (!aiSpeaking) {
      noiseFloor = noiseFloor * 0.85 + rms * 0.15;   // 平滑更新底噪
      noiseFloor = Math.min(noiseFloor, 20);
      return true;                                   // 未播报：照常推流
    }
    // AI 播报期间：真人 = 能量显著高于基线 → 放行 + 打断
    if (rms > noiseFloor + GATE_RISE) {
      onHumanSpeechGate();                           // 真人插话 → 打断AI播报
      return true;                                   // 放行推流
    }
    return false;                                    // AI 回声：抑制推流
  }

  return { setAiSpeaking, gateFrame };
  function setAiSpeaking(s: boolean) { aiSpeaking = s; }
}

const echoGate = createEchoGate();   // 播报状态变化时：echoGate.setAiSpeaking(...)