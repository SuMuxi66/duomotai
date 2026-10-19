/**
 * 场景检测模块
 * 用于从文本中提取业务场景（如：炒菜场景）并返回相应的业务建议。
 */

// 炒菜相关关键词
const COOKING_KEYWORDS = [
  '炒菜', '做饭', '烹饪', '烧菜', '煎炒', '炒', '煮', '炸', '炖', '蒸',
  '厨房', '灶台', '锅', '铲', '油', '火', '温度', '火候',
  '下厨', '烹饪', '料理', '做饭菜', '烧饭', '炒菜锅', '炒锅', '炒菜铲'
];

// 温度相关关键词
const TEMPERATURE_KEYWORDS = ['温度', '几度', '多少度', '热', '冷'];

/**
 * 检测文本是否包含炒菜场景的关键词
 * @param text 输入的语音识别文本或用户文本
 * @returns 是否包含炒菜场景
 */
export function isCookingSceneText(text: string): boolean {
  if (!text) return false;
  
  return COOKING_KEYWORDS.some(keyword => 
    text.toLowerCase().includes(keyword.toLowerCase())
  );
}

/**
 * 检测文本是否提到温度相关的词
 * @param text 输入文本
 * @returns 是否提到温度
 */
export function mentionsTemperatureText(text: string): boolean {
  if (!text) return false;
  
  return TEMPERATURE_KEYWORDS.some(keyword => 
    text.toLowerCase().includes(keyword.toLowerCase())
  );
}

/**
 * 获取针对炒菜场景的辅助建议
 * @param text 输入文本
 * @param temperature 当前温度
 * @returns 辅助建议文本，如果未检测到则返回空字符串
 */
export function getCookingSuggestion(text: string, temperature?: number | null): string {
  if (!isCookingSceneText(text)) {
    return "";
  }
  
  if (temperature !== undefined && temperature !== null && temperature > 40) {
    return `检测到您可能在炒菜，当前灶台温度较高 (${temperature}°C)，请注意火候，小心烫伤！需要我帮您打开抽油烟机或风扇吗？`;
  }
  
  return "检测到您可能在炒菜，请注意火候！需要我帮您打开抽油烟机或风扇吗？";
}

