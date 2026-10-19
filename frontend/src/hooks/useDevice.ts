import { useState, useEffect, useCallback } from "react";
import { detectDevices, requestDevicePermissions, DeviceInfo } from "../lib/device";

export interface UseDeviceReturn {
  deviceInfo: DeviceInfo | null;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  refreshDevices: () => Promise<void>;
  saveAudioDevice: (deviceId: string) => void;
  saveAudioOutputDevice: (deviceId: string) => void;
  saveVideoDevice: (deviceId: string) => void;
}

const STORAGE_KEYS = {
  selectedAudioDevice: "multimodal_selected_audio_device",
  selectedAudioOutputDevice: "multimodal_selected_audio_output_device",
  selectedVideoDevice: "multimodal_selected_video_device"
};

export function useDevice(apiBase: string): UseDeviceReturn {
  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [selectedAudioDevice, setSelectedAudioDevice] = useState<string>(() => 
    localStorage.getItem(STORAGE_KEYS.selectedAudioDevice) || ""
  );
  const [selectedAudioOutputDevice, setSelectedAudioOutputDevice] = useState<string>(() => 
    localStorage.getItem(STORAGE_KEYS.selectedAudioOutputDevice) || ""
  );
  const [selectedVideoDevice, setSelectedVideoDevice] = useState<string>(() => 
    localStorage.getItem(STORAGE_KEYS.selectedVideoDevice) || ""
  );

  const refreshDevices = useCallback(async () => {
    const info = await requestDevicePermissions();
    setDeviceInfo(info);
  }, []);

  const saveDeviceToBackend = useCallback(async (type: 'audio' | 'audiooutput' | 'video', deviceId: string) => {
    try {
      const envKey = type === 'audio' ? 'AUDIO_DEVICE_ID' : type === 'audiooutput' ? 'AUDIO_OUTPUT_DEVICE_ID' : 'CAMERA_DEVICE_ID';
      // 使用相对路径，让 Vite 代理处理请求
      const response = await fetch(`/env/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: envKey,
          value: deviceId || 'default'
        })
      });
      const result = await response.json();
      if (result.success) {
        const label = type === 'audio' ? '麦克风' : type === 'audiooutput' ? '扬声器' : '摄像头';
        console.log(`[设备同步] 已保存${label}设备到后端：${deviceId || '默认'}`);
      } else {
        console.warn(`[设备同步] 保存到后端失败：`, result.error);
      }
    } catch (error) {
      console.warn(`[设备同步] 网络错误:`, error);
    }
  }, [apiBase]);

  const saveAudioDevice = useCallback((deviceId: string) => {
    setSelectedAudioDevice(deviceId);
    localStorage.setItem(STORAGE_KEYS.selectedAudioDevice, deviceId);
    saveDeviceToBackend('audio', deviceId);
  }, [saveDeviceToBackend]);

  const saveAudioOutputDevice = useCallback((deviceId: string) => {
    setSelectedAudioOutputDevice(deviceId);
    localStorage.setItem(STORAGE_KEYS.selectedAudioOutputDevice, deviceId);
    saveDeviceToBackend('audiooutput', deviceId);
  }, [saveDeviceToBackend]);

  const saveVideoDevice = useCallback((deviceId: string) => {
    setSelectedVideoDevice(deviceId);
    localStorage.setItem(STORAGE_KEYS.selectedVideoDevice, deviceId);
    saveDeviceToBackend('video', deviceId);
  }, [saveDeviceToBackend]);

  useEffect(() => {
    detectDevices().then(setDeviceInfo);
  }, []);

  return {
    deviceInfo,
    selectedAudioDevice,
    selectedAudioOutputDevice,
    selectedVideoDevice,
    refreshDevices,
    saveAudioDevice,
    saveAudioOutputDevice,
    saveVideoDevice
  };
}
