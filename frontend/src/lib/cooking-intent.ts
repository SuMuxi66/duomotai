/**
 * 做饭意图识别模块
 * 用于识别用户是否想要开始做饭、退出、确认步骤或提问
 */

// 做饭相关关键词（未激活做饭时的判定基线，保持历史行为不变）
const COOKING_KEYWORDS = [
  '做菜', '做饭', '做菜吧', '开始做菜', '开始做饭', '我要做菜', '我想做菜',
];

// 明确的开始意图词（较长、误触发率低）
const COOKING_EXPLICIT_KEYWORDS = [
  '开始做菜', '开始做饭', '我要做菜', '我想做菜', '我要做饭', '我想做饭',
];

// 重新开始/换菜类强意图（做饭流程激活时仍允许重置状态机）
const COOKING_RESTART_KEYWORDS = [
  '重新开始', '重新做', '重做一遍', '再来一次', '再来一遍',
  '换个菜', '换一个菜', '换道菜',
];

// 停止做饭类强意图（比 EXIT_KEYWORDS 更长更明确，避免"算了/退出"等短词误触发）
const COOKING_STOP_KEYWORDS = [
  '退出做饭', '退出做菜', '停止做饭', '停止做菜',
  '结束做饭', '结束做菜', '不做饭了', '不做菜了',
];

// 退出相关关键词
const EXIT_KEYWORDS = [
  '不做了', '退出', '算了', '结束',
];

// 步骤确认相关关键词
const STEP_CONFIRM_KEYWORDS = [
  '好了', '完成了', '可以了', '搞定了', '下一步', '继续', '好了好了',
];

// 提问相关关键词
const QUESTION_KEYWORDS = [
  '怎么做', '什么意思', '为什么', '问下', '问一下',
];

export interface CookingIntentOptions {
  /**
   * 当前是否已处于做饭流程（面板激活或状态机非 IDLE）。
   * 为 true 时泛化短词（做饭/做菜/煮饭）不再判定为"开始做饭"意图，
   * 仅明确开始词或"重新开始/停止做饭"类强意图才生效，
   * 避免正常聊天中提到"做饭"就误触发流程重置。
   */
  cookingActive?: boolean;
}

export interface CookingIntent {
  isCookingIntent: boolean;
  isExitIntent: boolean;
  isStepConfirmIntent: boolean;
  isQuestionIntent: boolean;
  /** 文本包含明确的"重新开始/再来一次/换个菜"类强意图词 */
  isRestartIntent: boolean;
  /** 文本包含明确的"退出做饭/停止做饭"类强意图词 */
  isStopIntent: boolean;
  confidence: number;
}

/**
 * 移除文本中的标点符号
 */
function removePunctuation(text: string): string {
  return text.replace(/[.,!?;:'"''""''，。！？；：""'']/g, '');
}

/**
 * 将繁体中文转换为简体中文
 */
function toSimplifiedChinese(text: string): string {
  const traditionalToSimplified: Record<string, string> = {
    '飯': '饭',
    '開': '开',
    '結': '结',
    '繼': '继',
    '續': '续',
    '麼': '么',
    '為': '为',
    '問': '问',
  };

  return text.replace(/[\u4e00-\u9fa5]/g, (char) => traditionalToSimplified[char] || char);
}

/**
 * 检查字符串是否包含子串
 */
function stringContains(str: string, substr: string): boolean {
  return str.indexOf(substr) !== -1;
}

/**
 * 计算关键词匹配的置信度
 * @param matchCount 匹配的关键词数量
 * @param totalCount 总关键词数量
 * @returns 置信度 0-1
 */
function calculateConfidence(matchCount: number, totalCount: number): number {
  if (matchCount === 0) return 0;
  const baseConfidence = Math.min(matchCount * 0.3 + 0.4, 0.9);
  return Math.min(baseConfidence + (matchCount / totalCount) * 0.1, 1);
}

/**
 * 检查文本是否包含指定关键词列表中的任意一个
 */
function containsAnyKeyword(text: string, keywords: string[]): { matched: boolean; matchedKeywords: string[] } {
  const normalizedText = toSimplifiedChinese(removePunctuation(text.toLowerCase()));
  const matchedKeywords: string[] = [];

  for (const keyword of keywords) {
    const normalizedKeyword = toSimplifiedChinese(removePunctuation(keyword.toLowerCase()));
    if (stringContains(normalizedText, normalizedKeyword)) {
      matchedKeywords.push(keyword);
    }
  }

  return {
    matched: matchedKeywords.length > 0,
    matchedKeywords,
  };
}

/**
 * 识别做饭意图
 * @param text 用户输入文本
 * @param options 可选门控参数：cookingActive 表示做饭流程是否已激活
 * @returns 意图识别结果
 */
export function recognizeCookingIntent(text: string, options?: CookingIntentOptions): CookingIntent {
  if (!text || typeof text !== 'string') {
    return {
      isCookingIntent: false,
      isExitIntent: false,
      isStepConfirmIntent: false,
      isQuestionIntent: false,
      isRestartIntent: false,
      isStopIntent: false,
      confidence: 0,
    };
  }

  const cookingActive = options?.cookingActive === true;
  const trimmedText = text.trim();

  // 检测做饭意图（基线关键词：未激活时沿用历史行为）
  const cookingResult = containsAnyKeyword(trimmedText, COOKING_KEYWORDS);

  // 激活状态下改用分层关键词：泛化短词不再作为"开始做饭"的证据
  const explicitResult = containsAnyKeyword(trimmedText, COOKING_EXPLICIT_KEYWORDS);
  const restartResult = containsAnyKeyword(trimmedText, COOKING_RESTART_KEYWORDS);
  const stopResult = containsAnyKeyword(trimmedText, COOKING_STOP_KEYWORDS);

  // 检测退出意图
  const exitResult = containsAnyKeyword(trimmedText, EXIT_KEYWORDS);

  // 检测步骤确认意图
  const stepConfirmResult = containsAnyKeyword(trimmedText, STEP_CONFIRM_KEYWORDS);

  // 检测提问意图
  const questionResult = containsAnyKeyword(trimmedText, QUESTION_KEYWORDS);

  // 计算各意图的置信度
  let cookingConfidence: number;
  if (cookingActive) {
    // 激活中：仅明确开始词 / 重新开始词参与置信度，泛化短词不计入
    const strongCount = explicitResult.matchedKeywords.length + restartResult.matchedKeywords.length;
    cookingConfidence = strongCount > 0 ? calculateConfidence(strongCount, 2) : 0;
  } else {
    cookingConfidence = calculateConfidence(cookingResult.matchedKeywords.length, COOKING_KEYWORDS.length);
  }
  const exitConfidence = calculateConfidence(exitResult.matchedKeywords.length, EXIT_KEYWORDS.length);
  const stepConfirmConfidence = calculateConfidence(stepConfirmResult.matchedKeywords.length, STEP_CONFIRM_KEYWORDS.length);
  const questionConfidence = calculateConfidence(questionResult.matchedKeywords.length, QUESTION_KEYWORDS.length);
  const restartConfidence = calculateConfidence(restartResult.matchedKeywords.length, COOKING_RESTART_KEYWORDS.length);
  const stopConfidence = calculateConfidence(stopResult.matchedKeywords.length, COOKING_STOP_KEYWORDS.length);

  // 确定最终意图（取置信度最高的）
  const maxConfidence = Math.max(
    cookingConfidence,
    exitConfidence,
    stepConfirmConfidence,
    questionConfidence,
    restartConfidence,
    stopConfidence,
  );

  // 是否为"开始做饭"意图：
  // 未激活时沿用基线关键词 + 最高置信度判定（保持初次进入做饭的行为不变）；
  // 激活时改用分层判定：仅明确开始词（COOKING_EXPLICIT_KEYWORDS）或重新开始类
  // 强意图生效，泛化短词（做饭/做菜/煮饭等基线词的子串命中）被门控跳过，
  // 避免正常聊天提到"做饭"（如"这个菜做饭要注意什么"）就误触发流程重置
  const isCookingIntent = cookingActive
    ? (explicitResult.matched || restartResult.matched)
    : (cookingResult.matched && cookingConfidence === maxConfidence);

  return {
    isCookingIntent,
    isExitIntent: exitResult.matched && exitConfidence === maxConfidence,
    isStepConfirmIntent: stepConfirmResult.matched && stepConfirmConfidence === maxConfidence,
    isQuestionIntent: questionResult.matched && questionConfidence === maxConfidence,
    isRestartIntent: restartResult.matched,
    isStopIntent: stopResult.matched,
    confidence: maxConfidence,
  };
}
