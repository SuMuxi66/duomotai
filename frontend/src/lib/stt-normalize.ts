// STT 文本归一化：清洗语音识别原始文本，供 sendTextMessage 发送前使用。
// 与后端 stt_corrector.correct_stt_text 保持同一套保守清洗策略：
// - 全角→半角归一、去除控制字符/零宽字符、合并空白
// - 剥除首尾标点与纯标点/纯语气词（防误发给 LLM）
// - 简繁转换（本地 ASR/浏览器识别偶有繁体输出）
// - 保留单字确认词（好/要/开/嗯等，烟雾确认拦截依赖），不做语义改写
const traditionalToSimplified: Record<string, string> = {
  '廚': '厨', '櫃': '柜', '顏': '颜', '麼': '么', '個': '个',
  '這': '这', '嗎': '吗', '會': '会', '說': '说', '來': '来',
  '鍋': '锅', '煙': '烟', '開': '开', '關': '关', '問': '问',
  '聽': '听', '畫': '画', '風': '风', '溫': '温', '油': '油',
};

function toSimplifiedChinese(text: string): string {
  return text.replace(/./g, c => traditionalToSimplified[c] || c);
}

const TRIM_PUNCT = '。！？!?，,、；;：:．.…～~·•〈〉《》【】[]()（）|-_—/\\';

const EMPTYISH = /^[\s\u3000\u00a0。！？!?，,、；;：:．."'\-—/\\|()（）\[\]{}<>《》【】]*$/;
const ONLY_FILLER = /^[嗯哪哦呃哎唉啊哈咿唔欸！!？?\s]*$/;
const REPEATED = /(.)\1{3,}/g;

function normalizeWidth(text: string): string {
  let out = '';
  for (const ch of text) {
    if (ch === '\u3000') { out += ' '; continue; }
    if (/[\u4e00-\u9fff，。！？、；：""''（）【】《》]/.test(ch)) { out += ch; continue; }
    out += ch.normalize('NFKC');
  }
  return out;
}

export function normalizeSttText(raw: string): string {
  if (!raw) return '';
  // 1. 去控制字符/零宽字符
  let text = raw.replace(/[\u0000-\u001f\u007f\u200b\u200c\u200d\ufeff]/g, '');
  // 2. 全角→半角、全角空格→空格 + 简繁归一
  text = toSimplifiedChinese(normalizeWidth(text));
  // 3. 合并空白
  text = text.replace(/\s+/g, ' ').trim();
  // 4. 剥除首尾标点
  text = text.replace(new RegExp(`^[${TRIM_PUNCT}]+`), '').replace(new RegExp(`[${TRIM_PUNCT}]+$`), '').trim();
  if (!text) return '';
  // 5. 纯标点/纯语气词 → 空（"嗯嗯嗯"折叠为"嗯嗯"仍视为确认，但不主动清空非纯填充）
  if (EMPTYISH.test(text)) return '';
  if (ONLY_FILLER.test(text)) return '';
  // 6. 叠字噪音折叠（哈哈哈→哈哈）
  text = text.replace(REPEATED, m => m[0] + m[0]);
  return text.trim();
}