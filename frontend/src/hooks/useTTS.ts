import { useRef, useState, useCallback, useEffect } from "react";
import { createTTSController, TTSController } from "../lib/tts";

export interface UseTTSReturn {
  isSpeaking: boolean;
  speak: (text: string) => void;
  stop: () => void;
  ttsController: React.MutableRefObject<TTSController | null>;
}

export interface UseTTSOptions {
  onSpeakEnd?: () => void;
  onSpeakStart?: () => void;
}

export function useTTS(options: UseTTSOptions = {}): UseTTSReturn {
  const { onSpeakEnd, onSpeakStart } = options;
  const [isSpeaking, setIsSpeaking] = useState(false);
  const ttsController = useRef<TTSController | null>(null);

  useEffect(() => {
    ttsController.current = createTTSController(
      { rate: 1, pitch: 1, volume: 1, lang: 'zh-CN' },
      () => {
        setIsSpeaking(false);
        onSpeakEnd?.();
      }
    );

    return () => {
      ttsController.current?.stop();
    };
  }, [onSpeakEnd]);

  const speak = useCallback((text: string) => {
    if (ttsController.current) {
      setIsSpeaking(true);
      onSpeakStart?.();
      ttsController.current.speak(text);
    }
  }, [onSpeakStart]);

  const stop = useCallback(() => {
    if (ttsController.current) {
      ttsController.current.stop();
      setIsSpeaking(false);
    }
  }, []);

  return {
    isSpeaking,
    speak,
    stop,
    ttsController
  };
}

export function stripMarkdown(text: string): string {
  if (!text) return "";
  return text
    .replace(/#{1,6}\s*/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/!\[.*?\]\(.+?\)/g, '[图片]')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*>\s+/gm, '')
    .replace(/^\s*[=-]{3,}\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
