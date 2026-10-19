/**
 * 16k PCM16 采集/编码工具
 *
 * 从 wakeword-stt.ts 抽取的基础音频工具，供端侧 KWS/ASR 与云端 STT 推流共用：
 * 音量检测（RMS）、重采样降采样、Float32 → Int16 PCM 编码、字节流 base64 编码。
 */

/**
 * 简单的内联音量检测（基于 PCM Float32 数据），避免 VAD 模块重复获取麦克风
 */
export function calculateRmsVolume(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    sum += samples[i] * samples[i];
  }
  const rms = Math.sqrt(sum / samples.length);
  return Math.min(100, Math.round(rms * 200));
}

export function downsampleBuffer(buffer: Float32Array, inputRate: number, outputRate: number) {
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

export function floatTo16BitPCM(float32: Float32Array) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    let s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function toBase64(bytes: Uint8Array) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}
