export type FanAction = "on" | "off";

export type TemperatureData = {
  target: number;
  average: number;
  maximum: number;
  minimum: number;
};

export type WeightData = {
  weight: number;
  unit: string;
  stable: boolean;
  timestamp: string | null;
};

type FanStatusResponse = {
  success?: boolean;
  is_on?: boolean;
  message?: string;
};

/**
 * 获取风扇状态
 * 使用相对路径，让 Vite 代理处理请求
 */
export async function fetchFanStatus(_apiBase: string, enabled: boolean): Promise<boolean | null> {
  if (!enabled) {
    return null;
  }
  try {
    // 使用相对路径，让 Vite 代理处理请求
    const response = await fetch(`/fan/status`);
    const data = (await response.json()) as FanStatusResponse;
    if (data.success) {
      return !!data.is_on;
    }
  } catch (error) {
    console.error("[风扇] 获取状态失败:", error);
  }
  return null;
}

/**
 * 控制风扇
 * 使用相对路径，让 Vite 代理处理请求
 */
export async function controlFan(_apiBase: string, enabled: boolean, action: FanAction): Promise<boolean | null> {
  if (!enabled) {
    console.log("[风扇] 功能未启用");
    return null;
  }
  try {
    const endpoint = action === "on" ? `/fan/on` : `/fan/off`;
    console.log(`[风扇] 发送请求: ${endpoint}`);
    const response = await fetch(endpoint, { method: "POST" });
    const data = (await response.json()) as FanStatusResponse;
    console.log(`[风扇] 响应:`, data);
    
    if (data.success) {
      // 操作成功，立即获取最新状态
      await new Promise(resolve => setTimeout(resolve, 200)); // 等待设备响应
      const status = await fetchFanStatus(_apiBase, enabled);
      console.log(`[风扇] 最新状态:`, status);
      return status;
    }
  } catch (error) {
    console.error("[风扇] 操作失败:", error);
  }
  return null;
}

/**
 * 获取温度数据
 * 使用相对路径，让 Vite 代理处理请求
 */
export async function fetchTemperatureData(_apiBase: string, timeoutMs = 1500): Promise<TemperatureData | null> {
  try {
    // 使用相对路径，让 Vite 代理处理请求
    const response = await fetch(`/temperature`, { signal: AbortSignal.timeout(timeoutMs) });
    if (response.ok) {
      return (await response.json()) as TemperatureData;
    }
    console.warn("[温度检测] 温度数据获取失败:", response.status);
  } catch (error) {
    console.error("[温度检测] 温度数据获取错误:", error);
  }
  return null;
}

/**
 * 获取重量数据
 * 使用相对路径，让 Vite 代理处理请求
 */
export async function fetchWeightData(_apiBase: string, timeoutMs = 1500): Promise<WeightData | null> {
  try {
    // 使用相对路径，让 Vite 代理处理请求
    const response = await fetch(`/weight`, { signal: AbortSignal.timeout(timeoutMs) });
    if (response.ok) {
      return (await response.json()) as WeightData;
    }
    console.warn("[重量检测] 重量数据获取失败:", response.status);
  } catch (error) {
    console.error("[重量检测] 重量数据获取错误:", error);
  }
  return null;
}
