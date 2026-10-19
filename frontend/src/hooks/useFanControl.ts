import { useState, useCallback, useEffect, useRef } from "react";
import {
  fetchFanStatus as fetchFanStatusRequest,
  type FanAction,
  type TemperatureData,
  type WeightData,
} from "../lib/iot-controller";

const FAN_ENABLED = true;

export interface UseFanControlReturn {
  fanStatus: boolean | null;
  fetchFanStatus: () => Promise<boolean | null>;
  toggleFan: () => void;
  controlFan: (action: FanAction) => Promise<void>;
  temperatureData: TemperatureData | null;
  weightData: WeightData | null;
}

/**
 * 风扇/IoT 控制逻辑
 * - 管理 fanStatus 状态
 * - 提供 fetchFanStatus / toggleFan / controlFan 函数
 * - 预留 temperatureData / weightData（当前由 VideoPane 轮询，此处保持 null）
 */
export function useFanControl(): UseFanControlReturn {
  const [fanStatus, setFanStatus] = useState<boolean | null>(null);
  // temperature 和 weight 已由 VideoPane 持续轮询，此处仅保留接口占位
  const [temperatureData] = useState<TemperatureData | null>(null);
  const [weightData] = useState<WeightData | null>(null);

  const fanStatusRef = useRef(fanStatus);
  fanStatusRef.current = fanStatus;

  const fetchFanStatus = useCallback(async () => {
    console.log('[风扇] 正在获取状态...');
    const status = await fetchFanStatusRequest("", FAN_ENABLED);
    console.log('[风扇] 获取到状态:', status);
    setFanStatus(status);
    return status;
  }, []);

  // 直接执行风扇操作（用于按钮点击和语音控制）
  const executeFanAction = useCallback(async (action: 'on' | 'off') => {
    if (!FAN_ENABLED) {
      console.log('[风扇] 功能未启用');
      return;
    }

    console.log('[风扇] 执行操作:', action, '当前状态:', fanStatusRef.current);

    try {
      const endpoint = action === 'on' ? '/fan/on' : '/fan/off';
      console.log('[风扇] 发送请求:', endpoint);
      const response = await fetch(endpoint, { method: 'POST' });
      const data = await response.json();
      console.log('[风扇] 响应:', data);

      if (data.success) {
        // 操作成功，等待设备响应后更新状态
        await new Promise(resolve => setTimeout(resolve, 300));
        const newStatus = await fetchFanStatus();
        console.log('[风扇] 更新后状态:', newStatus);
      }
    } catch (error) {
      console.error('[风扇] 操作失败:', error);
    }
  }, [fetchFanStatus]);

  // 按钮点击：切换状态
  const toggleFan = useCallback(() => {
    const action = fanStatusRef.current ? 'off' : 'on';
    console.log('[风扇] 按钮点击，切换为:', action);
    void executeFanAction(action);
  }, [executeFanAction]);

  // 语音控制：指定操作
  const controlFan = useCallback(async (action: FanAction) => {
    console.log('[风扇] 语音控制:', action);
    await executeFanAction(action);
  }, [executeFanAction]);

  // 页面加载时获取风扇状态，带重试机制
  useEffect(() => {
    if (!FAN_ENABLED) return;

    const loadFanStatus = async () => {
      console.log('[风扇] 页面加载，开始获取状态...');
      // 延迟一下确保后端已就绪
      await new Promise(resolve => setTimeout(resolve, 500));
      const status = await fetchFanStatus();
      console.log('[风扇] 初始状态加载完成:', status);

      // 如果第一次失败，2秒后重试
      if (status === null) {
        console.log('[风扇] 状态获取失败，2秒后重试...');
        await new Promise(resolve => setTimeout(resolve, 2000));
        const retryStatus = await fetchFanStatus();
        console.log('[风扇] 重试后状态:', retryStatus);
      }
    };

    void loadFanStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    fanStatus,
    fetchFanStatus,
    toggleFan,
    controlFan,
    temperatureData,
    weightData,
  };
}
