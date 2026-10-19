/**
 * 唤醒词文本工具（繁简转换 / 归一化 / 唤醒词匹配与剥离）
 *
 * 从 wakeword-stt.ts 抽取。与源文件的差别：这些函数原本闭包捕获 config.wakeWords，
 * 模块版把 wakeWords 作为第一个参数显式传入。
 *
 * ⚠️ 语义约定（承重，勿改）：
 * - extractAfterWakeWord：未找到唤醒词时返回 ''（调用方有预检，空串安全）；
 * - trimWakeWordPrefix：未找到唤醒词时返回原文（调用方没有预检唤醒词，
 *   返回空串会把用户整句话丢掉——两处语义差异是有意设计）。
 */

// 繁体转简体
const traditionalToSimplified: Record<string, string> = {
  '廚': '厨', '櫃': '柜', '顏': '颜', '麼': '么', '個': '个',
  '這': '这', '嗎': '吗', '會': '会', '說': '说', '來': '来',
};

export function toSimplifiedChinese(text: string): string {
  return text.replace(/./g, c => traditionalToSimplified[c] || c);
}

const simplifiedToTraditional: Record<string, string> = {
  '厨': '廚',
};

export function toTraditional(s: string): string {
  return s.replace(/./g, c => simplifiedToTraditional[c] || c);
}

export function normalizeWakeWordText(text: string): string {
  return text.replace(/[，,。.!！?？、；;：:\s]/g, '').trim();
}

/**
 * 归一化索引 → 原始索引对齐的唯一实现（算法与 wakeword-stt.ts 的
 * extractAfterWakeWord 逐字一致）：在"去标点/空白后的归一化文本"里找唤醒词，
 * 再把归一化下标折算回原始文本下标，取唤醒词之后的剩余内容（.trim()）。
 */
function splitAfterWakeWord(wakeWords: string[], text: string): { found: boolean; rest: string } {
  const traditionalText = toTraditional(text);
  for (const ww of wakeWords) {
    const traditionalWw = normalizeWakeWordText(toTraditional(ww));
    const normalizedText = normalizeWakeWordText(traditionalText);
    const idx = normalizedText.indexOf(traditionalWw);
    if (idx !== -1) {
      let rawIndex = 0;
      let normalizedCount = 0;
      while (rawIndex < text.length && normalizedCount < idx + traditionalWw.length) {
        const currentChar = traditionalText[rawIndex] || "";
        if (!/[，,。.!！?？、；;：:\s]/.test(currentChar)) {
          normalizedCount += 1;
        }
        rawIndex += 1;
      }
      return { found: true, rest: text.substring(rawIndex).trim() };
    }
  }
  return { found: false, rest: '' };
}

export function containsWakeWord(wakeWords: string[], text: string): boolean {
  return splitAfterWakeWord(wakeWords, text).found;
}

export function extractAfterWakeWord(wakeWords: string[], text: string): string {
  const { found, rest } = splitAfterWakeWord(wakeWords, text);
  // 未找到唤醒词时返回 ''（与源文件 extractAfterWakeWord 行为一致）
  return found ? rest : '';
}

export function trimWakeWordPrefix(wakeWords: string[], text: string): string {
  const { found, rest } = splitAfterWakeWord(wakeWords, text);
  // 未找到唤醒词时必须原样返回输入：调用方没有预检唤醒词，返回空串会把用户整句话丢掉
  return found ? rest : text;
}
