import { useRef, useState, useCallback, useEffect } from "react";
import { createSimpleWakeWordListener, createContinuousSpeechListener } from "../lib/wakeword";
import { WAKE_WORDS } from "../lib/constants";

export interface UseWakeWordOptions {
  enabled: boolean;
  audioDeviceId?: string;
  wakeWords?: string[];
  onWakeWordDetected: (recognizedText: string) => void;
  onContinuousSpeech?: (text: string) => void;
  continuousMode?: boolean;
}

export interface UseWakeWordReturn {
  wakeWordDetected: boolean;
  wakeWordListenerActive: boolean;
  continuousListenerActive: boolean;
  startWakeWordListener: () => Promise<void>;
  stopWakeWordListener: () => void;
  startContinuousListener: () => Promise<void>;
  stopContinuousListener: () => void;
}

export function useWakeWord(options: UseWakeWordOptions): UseWakeWordReturn {
  const {
    enabled,
    audioDeviceId,
    wakeWords = WAKE_WORDS,
    onWakeWordDetected,
    onContinuousSpeech,
    continuousMode = false
  } = options;

  const [wakeWordDetected, setWakeWordDetected] = useState(false);
  const [wakeWordListenerActive, setWakeWordListenerActive] = useState(false);
  const [continuousListenerActive, setContinuousListenerActive] = useState(false);

  const wakeWordListenerRef = useRef<{ start: () => Promise<void>; stop: () => void } | null>(null);
  const continuousSpeechListenerRef = useRef<{ start: () => Promise<void>; stop: () => void; pause: () => void; resume: () => Promise<void> } | null>(null);

  const startWakeWordListener = useCallback(async () => {
    if (wakeWordListenerRef.current) {
      console.log('[唤醒词] 监听器已存在，跳过创建');
      return;
    }

    try {
      console.log('[唤醒词] 正在创建监听器...');
      wakeWordListenerRef.current = await createSimpleWakeWordListener({
        wakeWords,
        onWakeWordDetected: (recognizedText: string) => {
          console.log('[唤醒词] 检测到唤醒词:', recognizedText);
          setWakeWordDetected(true);
          wakeWordListenerRef.current?.stop();
          wakeWordListenerRef.current = null;
          setWakeWordListenerActive(false);
          onWakeWordDetected(recognizedText);
        },
        audioDeviceId,
      });
      await wakeWordListenerRef.current.start();
      setWakeWordListenerActive(true);
      console.log('[唤醒词] 监听器已启动');
    } catch (error) {
      console.error('[唤醒词] 监听器启动失败:', error);
    }
  }, [wakeWords, audioDeviceId, onWakeWordDetected]);

  const stopWakeWordListener = useCallback(() => {
    if (wakeWordListenerRef.current) {
      wakeWordListenerRef.current.stop();
      wakeWordListenerRef.current = null;
      setWakeWordListenerActive(false);
      console.log('[唤醒词] 监听器已停止');
    }
  }, []);

  const startContinuousListener = useCallback(async () => {
    if (continuousSpeechListenerRef.current) {
      console.log('[持续识别] 监听器已存在，跳过创建');
      return;
    }

    try {
      console.log('[持续识别] 正在创建监听器...');
      continuousSpeechListenerRef.current = createContinuousSpeechListener({
        silenceSeconds: 3,
        debug: true,
        onResult: (text: string) => {
          console.log('[持续识别] 检测到语音:', text);
          if (onContinuousSpeech) {
            onContinuousSpeech(text);
          }
        },
        audioDeviceId,
      });
      await continuousSpeechListenerRef.current.start();
      setContinuousListenerActive(true);
      console.log('[持续识别] 监听器已启动');
    } catch (error) {
      console.error('[持续识别] 监听器启动失败:', error);
    }
  }, [audioDeviceId, onContinuousSpeech]);

  const stopContinuousListener = useCallback(() => {
    if (continuousSpeechListenerRef.current) {
      continuousSpeechListenerRef.current.stop();
      continuousSpeechListenerRef.current = null;
      setContinuousListenerActive(false);
      console.log('[持续识别] 监听器已停止');
    }
  }, []);

  useEffect(() => {
    if (enabled && !continuousMode) {
      startWakeWordListener();
    } else if (!enabled) {
      stopWakeWordListener();
    }

    return () => {
      stopWakeWordListener();
    };
  }, [enabled, continuousMode, startWakeWordListener, stopWakeWordListener]);

  useEffect(() => {
    if (continuousMode) {
      stopWakeWordListener();
      startContinuousListener();
    } else {
      stopContinuousListener();
    }

    return () => {
      stopContinuousListener();
    };
  }, [continuousMode, startContinuousListener, stopWakeWordListener, stopContinuousListener]);

  return {
    wakeWordDetected,
    wakeWordListenerActive,
    continuousListenerActive,
    startWakeWordListener,
    stopWakeWordListener,
    startContinuousListener,
    stopContinuousListener
  };
}
