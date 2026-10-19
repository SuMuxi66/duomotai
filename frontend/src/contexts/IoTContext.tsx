import { createContext, useContext, useState, useCallback, useEffect } from "react";
import { fetchFanStatus as fetchFanStatusRequest, fetchTemperatureData, fetchWeightData, type FanAction, type TemperatureData, type WeightData } from "../lib/iot-controller";

const API_BASE = "";
const FAN_ENABLED = true;

interface IoTContextValue {
  fanStatus: boolean | null;
  temperatureData: TemperatureData | null;
  weightData: WeightData | null;
  fetchFanStatus: () => Promise<boolean | null>;
  fetchTemperature: () => Promise<TemperatureData | null>;
  fetchWeight: () => Promise<WeightData | null>;
  controlFan: (action: FanAction) => Promise<void>;
}

const IoTContext = createContext<IoTContextValue | null>(null);

export function IoTProvider({ children }: { children: React.ReactNode }) {
  const [fanStatus, setFanStatus] = useState<boolean | null>(null);
  const [temperatureData, setTemperatureData] = useState<TemperatureData | null>(null);
  const [weightData, setWeightData] = useState<WeightData | null>(null);

  const fetchFanStatus = useCallback(async () => {
    const status = await fetchFanStatusRequest(API_BASE, FAN_ENABLED);
    setFanStatus(status);
    return status;
  }, []);

  const fetchTemperature = useCallback(async () => {
    const data = await fetchTemperatureData(API_BASE, 1500);
    setTemperatureData(data);
    return data;
  }, []);

  const fetchWeight = useCallback(async () => {
    const data = await fetchWeightData(API_BASE, 1500);
    setWeightData(data);
    return data;
  }, []);

  const controlFan = useCallback(async (action: FanAction) => {
    if (!FAN_ENABLED) return;
    try {
      const endpoint = action === 'on' ? '/fan/on' : '/fan/off';
      const response = await fetch(endpoint, { method: 'POST' });
      const data = await response.json();
      if (data.success) {
        await new Promise(resolve => setTimeout(resolve, 300));
        await fetchFanStatus();
      }
    } catch (error) {
      console.error('[IoT] 控制风扇失败:', error);
    }
  }, [fetchFanStatus]);

  useEffect(() => {
    if (!FAN_ENABLED) return;
    const loadFanStatus = async () => {
      await new Promise(resolve => setTimeout(resolve, 500));
      const status = await fetchFanStatus();
      if (status === null) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        await fetchFanStatus();
      }
    };
    loadFanStatus();
  }, [fetchFanStatus]);

  return (
    <IoTContext.Provider value={{
      fanStatus,
      temperatureData,
      weightData,
      fetchFanStatus,
      fetchTemperature,
      fetchWeight,
      controlFan,
    }}>
      {children}
    </IoTContext.Provider>
  );
}

export function useIoT() {
  const ctx = useContext(IoTContext);
  if (!ctx) throw new Error("useIoT must be used within IoTProvider");
  return ctx;
}
