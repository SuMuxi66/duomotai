import { isCookingSceneText, mentionsTemperatureText } from "./scene-detector";
import type { FanAction, TemperatureData } from "./iot-controller";

export type LocalCommandAnalysis = {
  isCooking: boolean;
  mentionsTemperature: boolean;
  wantsTemperatureReport: boolean;
  fanAction: FanAction | null;
};

// 风扇口令匹配：动词 + 设备名（排气扇/排风扇/风扇三种叫法都支持，
// 替代项按长度优先排列，避免“排气扇”被“风扇”抢先部分匹配）
const FAN_CONTROL_REGEX = /(开启|打开|启动|关闭|停止|关掉)\s*(排气扇|排风扇|风扇)/;
const TEMPERATURE_QUERY_REGEX = /(检测|查看|告诉我)\s*(一下)?\s*(现在)?\s*温度\s*(是多少度)?/;

export function analyzeLocalCommandText(text: string, fanEnabled: boolean): LocalCommandAnalysis {
  const fanMatch = fanEnabled ? text.match(FAN_CONTROL_REGEX) : null;
  const fanAction =
    fanMatch && ["开启", "打开", "启动"].includes(fanMatch[1])
      ? "on"
      : fanMatch
        ? "off"
        : null;

  return {
    isCooking: isCookingSceneText(text),
    mentionsTemperature: mentionsTemperatureText(text),
    wantsTemperatureReport: TEMPERATURE_QUERY_REGEX.test(text),
    fanAction,
  };
}

export function buildLocalResponses(
  analysis: LocalCommandAnalysis,
  temperatureData: TemperatureData | null
): string[] {
  const responses: string[] = [];

  if (analysis.fanAction === "on") {
    responses.push("已为您开启风扇");
  } else if (analysis.fanAction === "off") {
    responses.push("已为您关闭风扇");
  }

  if (analysis.wantsTemperatureReport && temperatureData) {
    responses.push(
      `当前温度：目标温度 ${temperatureData.target}°C，平均温度 ${temperatureData.average}°C，最高温度 ${temperatureData.maximum}°C，最低温度 ${temperatureData.minimum}°C`
    );
  }

  return responses;
}
