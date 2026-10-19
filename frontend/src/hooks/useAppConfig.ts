import { useState, useEffect, useCallback, useMemo } from "react";
import { getTheme, ThemeMode, Theme } from "../lib/theme";
import { detectDevices } from "../lib/device";
import { loadFromStorage, saveToStorage, loadDeviceFromStorage } from "../lib/storage";
import { STORAGE_KEYS, DEFAULTS } from "../lib/constants";
import type { DeviceInfo, ApiKeys } from "../types";

export interface AppConfig {
  theme: Theme;
  themeMode: ThemeMode;
  visionEnabled: boolean;
  visionFps: number;
  ttsEnabled: boolean;
  vlmProvider: string;
  vlmModel: string;
  sttProvider: string;
  sttModel: string;
  ttsProvider: string;
  ttsModel: string;
  ttsVoice: string;
  apiKeys: ApiKeys;
  deviceInfo: DeviceInfo | null;
  selectedAudioDevice: string;
  selectedAudioOutputDevice: string;
  selectedVideoDevice: string;
  chatPanelWidth: number;
  viewportWidth: number;
  wakeWordEnabled: boolean;
  continuousMode: boolean;
}

export interface AppConfigActions {
  setVisionEnabled: (enabled: boolean) => void;
  setVisionFps: (fps: number) => void;
  setTtsEnabled: (enabled: boolean) => void;
  setVlmProvider: (provider: string) => void;
  setVlmModel: (model: string) => void;
  setSttProvider: (provider: string) => void;
  setSttModel: (model: string) => void;
  setTtsProvider: (provider: string) => void;
  setTtsModel: (model: string) => void;
  setTtsVoice: (voice: string) => void;
  setApiKeys: (keys: ApiKeys | ((prev: ApiKeys) => ApiKeys)) => void;
  setSelectedAudioDevice: (deviceId: string) => void;
  setSelectedAudioOutputDevice: (deviceId: string) => void;
  setSelectedVideoDevice: (deviceId: string) => void;
  setChatPanelWidth: (width: number) => void;
  setWakeWordEnabled: (enabled: boolean) => void;
  setContinuousMode: (mode: boolean) => void;
  refreshDevices: () => Promise<void>;
  saveAudioDevice: (deviceId: string) => void;
  saveAudioOutputDevice: (deviceId: string) => void;
  saveVideoDevice: (deviceId: string) => void;
}

export type AppConfigReturn = AppConfig & AppConfigActions;

export function useAppConfig(): AppConfigReturn {
  const [themeMode] = useState<ThemeMode>(() => loadFromStorage(STORAGE_KEYS.themeMode, "light"));
  const theme: Theme = useMemo(() => getTheme(themeMode), [themeMode]);

  const [visionEnabled, setVisionEnabled] = useState(() =>
    loadFromStorage('multimodal_vision_enabled', DEFAULTS.visionEnabled)
  );
  const [visionFps, setVisionFps] = useState(() => loadFromStorage(STORAGE_KEYS.visionFps, DEFAULTS.visionFps));
  const [ttsEnabled, setTtsEnabled] = useState(() => loadFromStorage(STORAGE_KEYS.ttsEnabled, DEFAULTS.ttsEnabled));

  const [vlmProvider, setVlmProvider] = useState(() => loadFromStorage(STORAGE_KEYS.vlmProvider, DEFAULTS.vlmProvider));
  const [vlmModel, setVlmModel] = useState(() => loadFromStorage(STORAGE_KEYS.vlmModel, DEFAULTS.vlmModel));

  const [sttProvider, setSttProvider] = useState(() => loadFromStorage(STORAGE_KEYS.sttProvider, "browser"));
  const [sttModel, setSttModel] = useState(() => loadFromStorage(STORAGE_KEYS.sttModel, "Web Speech API"));

  const [ttsProvider, setTtsProvider] = useState(() => loadFromStorage(STORAGE_KEYS.ttsProvider, DEFAULTS.ttsProvider));
  const [ttsModel, setTtsModel] = useState(() => loadFromStorage(STORAGE_KEYS.ttsModel, DEFAULTS.ttsModel));
  const [ttsVoice, setTtsVoice] = useState(() => loadFromStorage(STORAGE_KEYS.ttsVoice, DEFAULTS.ttsVoice));

  // key 权威在后端（.env.local 经 GET /config 下发），前端不持久化（本 hook 当前无调用方，仅为兼容保留）
  const [apiKeys, setApiKeys] = useState<ApiKeys>(() => ({
    modelscope: "",
    siliconflow: "",
    dashscope: "",
    xiaomi: "",
    deepseek: "",
    zhipu: "",
    sensenova: ""
  }));

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

  const [chatPanelWidth, setChatPanelWidth] = useState(() => loadFromStorage('multimodal_chat_panel_width', 420));
  const [viewportWidth, setViewportWidth] = useState<number>(() => window.innerWidth);

  const [wakeWordEnabled, setWakeWordEnabled] = useState(() => {
    const saved = localStorage.getItem('multimodal_wake_word_enabled');
    return saved !== null ? saved === 'true' : false;
  });
  const [continuousMode, setContinuousMode] = useState(false);

  useEffect(() => {
    detectDevices().then(setDeviceInfo);
  }, []);

  // 首次加载时从后端同步 STT/TTS 配置
  // 使用相对路径，让 Vite 代理处理请求
  useEffect(() => {
    fetch(`/config`)
      .then(r => r.json())
      .then((cfg: any) => {
        if (cfg.stt_provider && cfg.stt_provider !== sttProvider) {
          setSttProvider(cfg.stt_provider);
        }
        if (cfg.stt_model && cfg.stt_provider !== "browser") {
          setSttModel(cfg.stt_model);
        }
        if (cfg.tts_provider) {
          setTtsProvider(cfg.tts_provider);
        }
        if (cfg.tts_model) {
          setTtsModel(cfg.tts_model);
        }
        if (cfg.tts_voice) {
          setTtsVoice(cfg.tts_voice);
        }
      })
      .catch(() => {});
    // 仅首次加载执行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    saveToStorage('multimodal_vision_enabled', visionEnabled);
  }, [visionEnabled]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.visionFps, visionFps);
  }, [visionFps]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.ttsEnabled, ttsEnabled);
  }, [ttsEnabled]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.vlmProvider, vlmProvider);
  }, [vlmProvider]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.vlmModel, vlmModel);
  }, [vlmModel]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.sttProvider, sttProvider);
    saveToStorage(STORAGE_KEYS.sttModel, sttModel);
  }, [sttProvider, sttModel]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.ttsProvider, ttsProvider);
    saveToStorage(STORAGE_KEYS.ttsModel, ttsModel);
  }, [ttsProvider, ttsModel]);

  useEffect(() => {
    saveToStorage(STORAGE_KEYS.ttsVoice, ttsVoice);
  }, [ttsVoice]);

  useEffect(() => {
    localStorage.setItem('multimodal_wake_word_enabled', String(wakeWordEnabled));
  }, [wakeWordEnabled]);

  useEffect(() => {
    saveToStorage('multimodal_chat_panel_width', chatPanelWidth);
  }, [chatPanelWidth]);

  const refreshDevices = useCallback(async () => {
    const { requestDevicePermissions } = await import("../lib/device");
    const info = await requestDevicePermissions();
    setDeviceInfo(info);
  }, []);

  const saveAudioDevice = useCallback((deviceId: string) => {
    setSelectedAudioDevice(deviceId);
    localStorage.setItem('multimodal_selected_audio_device', deviceId);
  }, []);

  const saveAudioOutputDevice = useCallback((deviceId: string) => {
    setSelectedAudioOutputDevice(deviceId);
    localStorage.setItem('multimodal_selected_audio_output_device', deviceId);
  }, []);

  const saveVideoDevice = useCallback((deviceId: string) => {
    setSelectedVideoDevice(deviceId);
    localStorage.setItem('multimodal_selected_video_device', deviceId);
  }, []);

  return {
    theme,
    themeMode,
    visionEnabled,
    visionFps,
    ttsEnabled,
    vlmProvider,
    vlmModel,
    sttProvider,
    sttModel,
    ttsProvider,
    ttsModel,
    ttsVoice,
    apiKeys,
    deviceInfo,
    selectedAudioDevice,
    selectedAudioOutputDevice,
    selectedVideoDevice,
    chatPanelWidth,
    viewportWidth,
    wakeWordEnabled,
    continuousMode,
    setVisionEnabled,
    setVisionFps,
    setTtsEnabled,
    setVlmProvider,
    setVlmModel,
    setSttProvider,
    setSttModel,
    setTtsProvider,
    setTtsModel,
    setTtsVoice,
    setApiKeys,
    setSelectedAudioDevice,
    setSelectedAudioOutputDevice,
    setSelectedVideoDevice,
    setChatPanelWidth,
    setWakeWordEnabled,
    setContinuousMode,
    refreshDevices,
    saveAudioDevice,
    saveAudioOutputDevice,
    saveVideoDevice,
  };
}
