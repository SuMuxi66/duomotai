/**
 * 云端 STT「良性失败」占位文案（唯一权威版本）
 *
 * 从 wakeword-stt.ts 抽取。源文件内原本有两份完全相同的清单
 * （模块级 CLOUD_FAILURE_MARKERS + createSttWakeWordListener 内部的
 * CLOUD_FAILURE_TEXTS），这里合并为一份权威定义。
 */

/**
 * 后端「良性失败」占位文案：识别不出来时下发的可过滤文本，不能当识别结果用。
 * 持续识别用它判断"云端到底是在工作还是已经挂了"（后端转写异常时只会回这个，
 * 不会另发错误事件），是「云端失效 → 切回端侧」的判定依据。
 */
export const CLOUD_FAILURE_MARKERS = ["未识别", "识别失败", "没有接收到音频", "未启用 STT", "模型不可用"];

export function looksLikeCloudFailure(text: string): boolean {
  return CLOUD_FAILURE_MARKERS.some((marker) => text.includes(marker));
}
