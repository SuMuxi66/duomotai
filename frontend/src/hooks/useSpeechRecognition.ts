import { useRef, useState, useCallback, useEffect } from "react";
import {
  createBrowserSpeechRecognition,
  BrowserSpeechRecognition,
  isSpeechRecognitionSupported,
} from "../lib/browser-speech-recognition";

export interface UseSpeechRecognitionOptions {
  sttProvider: string;
  audioDeviceId?: string;
  onResult: (result: { transcript: string; isFinal: boolean }) => void;
  onError: (error: string) => void;
  onStart?: () => void;
  onEnd?: () => void;
}

export interface UseSpeechRecognitionReturn {
  isListening: boolean;
  speechRecognitionState: 'idle' | 'waiting' | 'listening' | 'analyzing';
  transcript: string;
  interimTranscript: string;
  start: (continuous?: boolean) => void;
  stop: () => void;
  reset: () => void;
  setVoiceCallMode: (enabled: boolean) => void;
}

export function useSpeechRecognition(options: UseSpeechRecognitionOptions): UseSpeechRecognitionReturn {
  const { sttProvider, audioDeviceId: _audioDeviceId, onResult, onError, onStart, onEnd } = options;

  const [isListening, setIsListening] = useState(false);
  const [speechRecognitionState, setSpeechRecognitionState] = useState<'idle' | 'waiting' | 'listening' | 'analyzing'>('idle');
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");

  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const finalTranscriptRef = useRef('');
  const interimTranscriptRef = useRef('');
  const silenceTimerRef = useRef<number | null>(null);
  const isVoiceCallModeRef = useRef(false);
  const speechRecognitionStateRef = useRef<'idle' | 'waiting' | 'listening' | 'analyzing'>('idle');
  const voiceCallRestartTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (speechRecognitionRef.current && sttProvider !== 'browser') {
      speechRecognitionRef.current.stop();
      speechRecognitionRef.current = null;
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
    }

    if (sttProvider === 'browser') {
      if (!isSpeechRecognitionSupported()) {
        console.warn('[Browser STT] Browser does not support speech recognition');
        return;
      }

      speechRecognitionRef.current = createBrowserSpeechRecognition(
        {
          lang: 'zh-CN',
          continuous: true,
          interimResults: true,
          debug: true,
        },
        {
          onResult: (result) => {
            const text = (result.transcript || '').trim();
            if (!text) return;

            if (result.isFinal) {
              finalTranscriptRef.current = `${finalTranscriptRef.current}${text}`;
              interimTranscriptRef.current = '';
            } else {
              interimTranscriptRef.current = text;
            }

            const combinedText = `${finalTranscriptRef.current}${interimTranscriptRef.current}`.trim();
            setTranscript(combinedText);

            if (speechRecognitionStateRef.current !== 'listening') {
              setSpeechRecognitionState('listening');
              speechRecognitionStateRef.current = 'listening';
            }

            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }

            if (result.isFinal) {
              setSpeechRecognitionState('idle');
              speechRecognitionStateRef.current = 'idle';

              if (isVoiceCallModeRef.current) {
                const textToSend = `${finalTranscriptRef.current}${interimTranscriptRef.current}`.trim();
                if (textToSend) {
                  onResult({ transcript: textToSend, isFinal: true });
                  setTranscript('');
                  finalTranscriptRef.current = '';
                  interimTranscriptRef.current = '';

                  voiceCallRestartTimerRef.current = window.setTimeout(() => {
                    if (speechRecognitionRef.current && isVoiceCallModeRef.current) {
                      try {
                        speechRecognitionRef.current.start();
                      } catch (error) {
                        console.error('[语音通话] 重启识别失败:', error);
                      }
                    }
                  }, 500);
                }
              }
            } else {
              silenceTimerRef.current = window.setTimeout(() => {
                const textToSend = `${finalTranscriptRef.current}${interimTranscriptRef.current}`.trim();
                if (textToSend) {
                  setSpeechRecognitionState('analyzing');
                  speechRecognitionStateRef.current = 'analyzing';

                  if (isVoiceCallModeRef.current) {
                    onResult({ transcript: textToSend, isFinal: true });
                    setTranscript('');
                    finalTranscriptRef.current = '';
                    interimTranscriptRef.current = '';

                    voiceCallRestartTimerRef.current = window.setTimeout(() => {
                      if (speechRecognitionRef.current && isVoiceCallModeRef.current) {
                        try {
                          speechRecognitionRef.current.start();
                        } catch (error) {
                          console.error('[语音通话] 重启识别失败:', error);
                        }
                      }
                    }, 500);
                  } else {
                    setSpeechRecognitionState('idle');
                    speechRecognitionStateRef.current = 'idle';
                  }
                }
              }, 3000);
            }
          },
          onError: (error) => {
            console.error('[SpeechRecognition] Error:', error);
            setSpeechRecognitionState('idle');
            speechRecognitionStateRef.current = 'idle';
            setIsListening(false);
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
            onError(error);
          },
          onEnd: () => {
            console.log('[SpeechRecognition] Ended');
            setIsListening(false);
            setSpeechRecognitionState('idle');
            speechRecognitionStateRef.current = 'idle';
            setInterimTranscript('');
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
            onEnd?.();
          },
          onStart: () => {
            console.log('[SpeechRecognition] Started');
            setIsListening(true);
            setSpeechRecognitionState('waiting');
            speechRecognitionStateRef.current = 'waiting';
            setInterimTranscript('');
            finalTranscriptRef.current = '';
            interimTranscriptRef.current = '';
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
            onStart?.();
          },
        }
      );

      return () => {
        speechRecognitionRef.current?.stop();
        speechRecognitionRef.current = null;
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
        }
        if (voiceCallRestartTimerRef.current) {
          clearTimeout(voiceCallRestartTimerRef.current);
        }
      };
    }

    return () => {
      if (speechRecognitionRef.current) {
        speechRecognitionRef.current.stop();
        speechRecognitionRef.current = null;
      }
    };
  }, [sttProvider]);

  const start = useCallback((continuous: boolean = false) => {
    if (speechRecognitionRef.current) {
      isVoiceCallModeRef.current = continuous;
      try {
        speechRecognitionRef.current.start();
        setIsListening(true);
        setSpeechRecognitionState('waiting');
        speechRecognitionStateRef.current = 'waiting';
      } catch (error) {
        console.error('[SpeechRecognition] Start failed:', error);
        onError?.(String(error));
      }
    }
  }, []);

  const stop = useCallback(() => {
    if (speechRecognitionRef.current) {
      speechRecognitionRef.current.stop();
      setIsListening(false);
      setSpeechRecognitionState('idle');
      speechRecognitionStateRef.current = 'idle';
    }
  }, []);

  const reset = useCallback(() => {
    finalTranscriptRef.current = '';
    interimTranscriptRef.current = '';
    setTranscript('');
    setSpeechRecognitionState('idle');
    speechRecognitionStateRef.current = 'idle';
  }, []);

  const setVoiceCallMode = useCallback((enabled: boolean) => {
    isVoiceCallModeRef.current = enabled;
  }, []);

  return {
    isListening,
    speechRecognitionState,
    transcript,
    interimTranscript,
    start,
    stop,
    reset,
    setVoiceCallMode
  };
}
