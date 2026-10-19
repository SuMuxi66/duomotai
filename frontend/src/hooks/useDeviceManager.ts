import { useState, useEffect, useCallback, useRef } from "react";
import { requestDevicePermissions, detectDevices, DeviceInfo } from "../lib/device";
import { saveRawToStorage } from "../lib/storage";
import { GLASSES_CAMERA_ID, GLASSES_AUDIO_ID } from "../lib/constants";

const STORAGE_KEY_AUDIO = "multimodal_selected_audio_device";
const STORAGE_KEY_AUDIO_OUTPUT = "multimodal_selected_audio_output_device";
const STORAGE_KEY_VIDEO = "multimodal_selected_video_device";

export interface UseDeviceManagerOptions {
  showToast: (text: string, type?: "info" | "warning" | "error" | "success") => void;
  sendWsEventRef: React.MutableRefObject<((type: string, payload: any) => void) | null>;
}

export interface UseDeviceManagerReturn {
  deviceInfo: DeviceInfo | null;
  setDeviceInfo: React.Dispatch<React.SetStateAction<DeviceInfo | null>>;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  useGlassesCamera: boolean;
  setUseGlassesCamera: React.Dispatch<React.SetStateAction<boolean>>;
  glassesCameraConnected: boolean;
  setGlassesCameraConnected: React.Dispatch<React.SetStateAction<boolean>>;
  useGlassesAudio: boolean;
  setUseGlassesAudio: React.Dispatch<React.SetStateAction<boolean>>;
  glassesAudioConnected: boolean;
  glassesImgRef: React.MutableRefObject<HTMLImageElement | null>;
  saveAudioDevice: (deviceId: string) => void;
  saveAudioOutputDevice: (deviceId: string) => void;
  saveVideoDevice: (deviceId: string) => void;
  refreshDevices: () => Promise<void>;
}

/**
 * 设备管理 hook
 * - 麦克风/扬声器/摄像头设备选择
 * - 设备热插拔检测
 * - 眼镜摄像头/音频连接状态轮询
 */
export function useDeviceManager(options: UseDeviceManagerOptions): UseDeviceManagerReturn {
  const { showToast, sendWsEventRef } = options;

  const [deviceInfo, setDeviceInfo] = useState<DeviceInfo | null>(null);
  const [selectedAudioDevice, setSelectedAudioDevice] = useState<string>(() =>
    localStorage.getItem(STORAGE_KEY_AUDIO) || ""
  );
  const [selectedAudioOutputDevice, setSelectedAudioOutputDevice] = useState<string>(() =>
    localStorage.getItem(STORAGE_KEY_AUDIO_OUTPUT) || ""
  );
  const [selectedVideoDevice, setSelectedVideoDevice] = useState<string>(() =>
    localStorage.getItem(STORAGE_KEY_VIDEO) || ""
  );

  const [useGlassesCamera, setUseGlassesCamera] = useState(false);
  const [glassesCameraConnected, setGlassesCameraConnected] = useState(false);
  const [useGlassesAudio, setUseGlassesAudio] = useState(false);
  const [glassesAudioConnected, setGlassesAudioConnected] = useState(false);
  const glassesImgRef = useRef<HTMLImageElement | null>(null);

  // 应用启动时自动请求设备权限
  useEffect(() => {
    console.log('[设备] 正在初始化设备信息...');
    requestDevicePermissions()
      .then(info => {
        console.log('[设备] 设备信息获取成功:', info);
        setDeviceInfo(info);
      })
      .catch(err => {
        console.error('[设备] 初始化失败:', err);
      });
  }, []);

  const saveDeviceToBackend = useCallback(async (type: 'audio' | 'audiooutput' | 'video', deviceId: string) => {
    try {
      const envKey = type === 'audio' ? 'AUDIO_DEVICE_ID' : type === 'audiooutput' ? 'AUDIO_OUTPUT_DEVICE_ID' : 'CAMERA_DEVICE_ID';
      const response = await fetch('/env/update', {
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
  }, []);

  const saveAudioDevice = useCallback((deviceId: string) => {
    if (deviceId === GLASSES_AUDIO_ID) {
      setUseGlassesAudio(true);
      sendWsEventRef.current?.("control", { action: "set_audio_source", source: "glasses" });
      return;
    }
    setSelectedAudioDevice(deviceId);
    saveRawToStorage(STORAGE_KEY_AUDIO, deviceId);
    saveDeviceToBackend('audio', deviceId);
    if (useGlassesAudio) {
      setUseGlassesAudio(false);
      sendWsEventRef.current?.("control", { action: "set_audio_source", source: "local" });
    }
  }, [saveDeviceToBackend, sendWsEventRef, useGlassesAudio]);

  const saveAudioOutputDevice = useCallback((deviceId: string) => {
    if (deviceId === GLASSES_AUDIO_ID) {
      setSelectedAudioOutputDevice(deviceId);
      saveRawToStorage(STORAGE_KEY_AUDIO_OUTPUT, deviceId);
      setUseGlassesAudio(true);
      sendWsEventRef.current?.("control", { action: "set_audio_source", source: "glasses" });
      return;
    }

    // 检查浏览器是否支持 setSinkId（扬声器切换）
    if (deviceId) {
      const audioEl = new Audio();
      const isSupported = typeof (audioEl as any).setSinkId === 'function';
      if (!isSupported) {
        showToast('当前浏览器不支持扬声器切换，请使用 Chrome/Edge', 'warning');
        return;
      }
    }
    setSelectedAudioOutputDevice(deviceId);
    saveRawToStorage(STORAGE_KEY_AUDIO_OUTPUT, deviceId);
    saveDeviceToBackend('audiooutput', deviceId);

    if (useGlassesAudio) {
      setUseGlassesAudio(false);
      sendWsEventRef.current?.("control", { action: "set_audio_source", source: "local" });
    }
  }, [saveDeviceToBackend, showToast, sendWsEventRef, useGlassesAudio]);

  const saveVideoDevice = useCallback((deviceId: string) => {
    if (deviceId === GLASSES_CAMERA_ID) {
      setUseGlassesCamera(true);
      sendWsEventRef.current?.("control", { action: "set_camera_source", source: "glasses" });
      return;
    }
    setUseGlassesCamera(false);
    setSelectedVideoDevice(deviceId);
    saveRawToStorage(STORAGE_KEY_VIDEO, deviceId);
    saveDeviceToBackend('video', deviceId);
    sendWsEventRef.current?.("control", { action: "set_camera_source", source: "usb" });
  }, [saveDeviceToBackend, sendWsEventRef]);

  const refreshDevices = useCallback(async () => {
    const info = await requestDevicePermissions();
    setDeviceInfo(info);
  }, []);

  // 设备热插拔检测
  const selectedAudioDeviceRef = useRef(selectedAudioDevice);
  const selectedAudioOutputDeviceRef = useRef(selectedAudioOutputDevice);
  const selectedVideoDeviceRef = useRef(selectedVideoDevice);
  useEffect(() => { selectedAudioDeviceRef.current = selectedAudioDevice; }, [selectedAudioDevice]);
  useEffect(() => { selectedAudioOutputDeviceRef.current = selectedAudioOutputDevice; }, [selectedAudioOutputDevice]);
  useEffect(() => { selectedVideoDeviceRef.current = selectedVideoDevice; }, [selectedVideoDevice]);

  useEffect(() => {
    const handleDeviceChange = () => {
      console.log('[设备检测] 检测到设备列表变化');
      detectDevices().then(info => {
        console.log('[设备检测] 设备列表已更新:', info);
        setDeviceInfo(info);

        const currentAudioDevice = selectedAudioDeviceRef.current;
        const currentAudioOutputDevice = selectedAudioOutputDeviceRef.current;
        const currentVideoDevice = selectedVideoDeviceRef.current;

        if (currentAudioDevice) {
          const micExists = info.audioDevices.some(d => d.deviceId === currentAudioDevice);
          if (!micExists) {
            console.warn('[设备检测] 当前选择的麦克风已断开，回退到默认设备');
            setSelectedAudioDevice('');
            saveRawToStorage(STORAGE_KEY_AUDIO, '');
            saveDeviceToBackend('audio', '');
            showToast('麦克风已断开，已切换到默认设备', 'warning');
          }
        }
        if (currentAudioOutputDevice) {
          const speakerExists = info.audioOutputDevices.some(d => d.deviceId === currentAudioOutputDevice);
          if (!speakerExists) {
            console.warn('[设备检测] 当前选择的扬声器已断开，回退到默认设备');
            setSelectedAudioOutputDevice('');
            saveRawToStorage(STORAGE_KEY_AUDIO_OUTPUT, '');
            saveDeviceToBackend('audiooutput', '');
            showToast('扬声器已断开，已切换到默认设备', 'warning');
          }
        }
        if (currentVideoDevice) {
          const camExists = info.videoDevices.some(d => d.deviceId === currentVideoDevice);
          if (!camExists) {
            console.warn('[设备检测] 当前选择的摄像头已断开，回退到默认设备');
            setSelectedVideoDevice('');
            saveRawToStorage(STORAGE_KEY_VIDEO, '');
            saveDeviceToBackend('video', '');
            showToast('摄像头已断开，已切换到默认设备', 'warning');
          }
        }
      });
    };

    if (navigator.mediaDevices && typeof navigator.mediaDevices.addEventListener === 'function') {
      navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange);
      console.log('[设备检测] 已注册设备热插拔监听');
    } else {
      console.warn('[设备检测] 浏览器不支持 devicechange 事件，使用轮询方案');
      const intervalId = setInterval(() => {
        detectDevices().then(info => {
          setDeviceInfo(info);
        });
      }, 5000);
      return () => clearInterval(intervalId);
    }

    return () => {
      if (navigator.mediaDevices && typeof navigator.mediaDevices.removeEventListener === 'function') {
        navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange);
      }
    };
  }, [saveDeviceToBackend, showToast]);

  // 轮询眼镜摄像头连接状态
  useEffect(() => {
    const check = () => {
      fetch('/glasses-frame/latest').then(r => r.json()).then(d => {
        setGlassesCameraConnected(d.connected);
      }).catch(() => {});
    };
    check();
    const interval = setInterval(check, 3000);
    return () => clearInterval(interval);
  }, [useGlassesCamera]);

  // 轮询眼镜音频连接状态
  useEffect(() => {
    const check = () => {
      fetch('/glasses-audio/status').then(r => r.json()).then(d => {
        setGlassesAudioConnected(!!d.connected);
      }).catch(() => {});
    };
    check();
    const interval = setInterval(check, 3000);
    return () => clearInterval(interval);
  }, [useGlassesAudio]);

  return {
    deviceInfo,
    setDeviceInfo,
    selectedAudioDevice,
    selectedAudioOutputDevice,
    selectedVideoDevice,
    useGlassesCamera,
    setUseGlassesCamera,
    glassesCameraConnected,
    setGlassesCameraConnected,
    useGlassesAudio,
    setUseGlassesAudio,
    glassesAudioConnected,
    glassesImgRef,
    saveAudioDevice,
    saveAudioOutputDevice,
    saveVideoDevice,
    refreshDevices,
  };
}
