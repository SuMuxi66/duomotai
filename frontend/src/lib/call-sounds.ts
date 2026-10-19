/**
 * 通话模式提示音（纯 WebAudio 合成，无需外部音频文件）
 * - playAnswerTone：接听"嘟"（接通瞬间的轻快双音）
 * - playHangupTone：挂断"咔"（下行短促音）
 */

let sharedCtx: AudioContext | null = null;

function getCtx(): AudioContext {
  if (!sharedCtx) {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    sharedCtx = new Ctor();
  }
  // 浏览器自动播放策略：若被 suspend，尝试恢复
  if (sharedCtx.state === "suspended") {
    void sharedCtx.resume();
  }
  return sharedCtx;
}

function blip(
  ctx: AudioContext,
  freq: number,
  startOffset: number,
  duration: number,
  gainPeak: number,
  type: OscillatorType = "sine",
) {
  const now = ctx.currentTime + startOffset;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, now);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(gainPeak, now + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
  osc.connect(gain).connect(ctx.destination);
  osc.start(now);
  osc.stop(now + duration + 0.02);
}

export function playAnswerTone() {
  try {
    const ctx = getCtx();
    // 轻快的两声"嘟"：660Hz → 880Hz
    blip(ctx, 660, 0, 0.11, 0.25, "sine");
    blip(ctx, 880, 0.12, 0.11, 0.25, "sine");
  } catch (e) {
    console.warn("[通话模式] 接听提示音播放失败:", e);
  }
}

export function playHangupTone() {
  try {
    const ctx = getCtx();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.setValueAtTime(440, now);
    osc.frequency.exponentialRampToValueAtTime(110, now + 0.18);
    gain.gain.setValueAtTime(0.2, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.2);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.22);
  } catch (e) {
    console.warn("[通话模式] 挂断提示音播放失败:", e);
  }
}
