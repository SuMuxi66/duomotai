import { createContext, useContext, useState, useCallback, useEffect, useMemo } from "react";
import { requestDevicePermissions, DeviceInfo } from "../lib/device";
import { loadDeviceFromStorage, saveRawToStorage } from "../lib/storage";

interface DeviceContextValue {
  deviceInfo: DeviceInfo | null;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  isRecording: boolean;
  requestPermissions: () => Promise<DeviceInfo | null>;
  selectAudioDevice: (deviceId: string) => void;
  selectAudioOutputDevice: (deviceId: string) => void;
  selectVideoDevice: (deviceId: string) => void;
}

const DeviceContext = createContext<DeviceContextValue | null>(null);

export function DeviceProvider({ children }: { children: React.ReactNode }) {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [selectedAudioDevice, setSelectedAudioDevice] = useState<string>(() =>
    loadDeviceFromStorage('multimodal_selected_audio_device', '')
  );
  const [selectedAudioOutputDevice, setSelectedAudioOutputDevice] = useState<string>(() =>
    loadDeviceFromStorage('multimodal_selected_audio_output_device', '')
  );
  const [selectedVideoDevice, setSelectedVideoDevice] = useState<string>(() =>
    loadDeviceFromStorage('multimodal_selected_video_device', '')
  );
  const [isRecording] = useState(false);

  const requestPermissions = useCallback(async () => {
    try {
      const info = await requestDevicePermissions();
      setDeviceInfo(info);
      return info;
    } catch (error) {
      console.error('[Device] 请求设备权限失败:', error);
      return null;
    }
  }, []);

  const selectAudioDevice = useCallback((deviceId: string) => {
    setSelectedAudioDevice(deviceId);
    saveRawToStorage('multimodal_selected_audio_device', deviceId);
  }, []);

  const selectAudioOutputDevice = useCallback((deviceId: string) => {
    setSelectedAudioOutputDevice(deviceId);
    saveRawToStorage('multimodal_selected_audio_output_device', deviceId);
  }, []);

  const selectVideoDevice = useCallback((deviceId: string) => {
    setSelectedVideoDevice(deviceId);
    saveRawToStorage('multimodal_selected_video_device', deviceId);
  }, []);

  useEffect(() => {
    console.log('[设备] 正在初始化设备信息...');
    requestPermissions()
      .then(info => console.log('[设备] 设备信息获取成功:', info))
      .catch(err => console.error('[设备] 初始化失败:', err));
  }, [requestPermissions]);

  const contextValue = useMemo(() => ({
    deviceInfo,
    selectedAudioDevice,
    selectedAudioOutputDevice,
    selectedVideoDevice,
    isRecording,
    requestPermissions,
    selectAudioDevice,
    selectAudioOutputDevice,
    selectVideoDevice,
  }), [deviceInfo, selectedAudioDevice, selectedAudioOutputDevice, selectedVideoDevice, isRecording, requestPermissions, selectAudioDevice, selectAudioOutputDevice, selectVideoDevice]);

  return (
    <DeviceContext.Provider value={contextValue}>
      {children}
    </DeviceContext.Provider>
  );
}

export function useDevice() {
  const ctx = useContext(DeviceContext);
  if (!ctx) throw new Error("useDevice must be used within DeviceProvider");
  return ctx;
}
