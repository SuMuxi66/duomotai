import { createContext, useContext, useState, useRef, useEffect, useMemo } from "react";
import { SpeechRecognitionState, AIResponseState } from "../types";
import { usePersistedState } from "../hooks/usePersistedState";

interface VoiceContextValue {
  speechRecognitionState: SpeechRecognitionState;
  aiResponseState: AIResponseState;
  isListening: boolean;
  isAiSpeaking: boolean;
  wakeWordEnabled: boolean;
  wakeWordDetected: boolean;
  wakeWordStatusText: string;
  wakeWordLiveTranscript: string;
  continuousMode: boolean;
  continuousSpeechPaused: boolean;
  interruptMessage: string | null;
  setSpeechRecognitionState: React.Dispatch<React.SetStateAction<SpeechRecognitionState>>;
  setAiResponseState: React.Dispatch<React.SetStateAction<AIResponseState>>;
  setIsListening: React.Dispatch<React.SetStateAction<boolean>>;
  setIsAiSpeaking: React.Dispatch<React.SetStateAction<boolean>>;
  setWakeWordEnabled: (v: boolean) => void;
  setWakeWordDetected: React.Dispatch<React.SetStateAction<boolean>>;
  setWakeWordStatusText: React.Dispatch<React.SetStateAction<string>>;
  setWakeWordLiveTranscript: React.Dispatch<React.SetStateAction<string>>;
  setContinuousMode: React.Dispatch<React.SetStateAction<boolean>>;
  setContinuousSpeechPaused: React.Dispatch<React.SetStateAction<boolean>>;
  setInterruptMessage: React.Dispatch<React.SetStateAction<string | null>>;
  isAiSpeakingRef: React.MutableRefObject<boolean>;
  continuousModeRef: React.MutableRefObject<boolean>;
}

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceProvider({ children }: { children: React.ReactNode }) {
  const [speechRecognitionState, setSpeechRecognitionState] = useState<SpeechRecognitionState>('idle');
  const [aiResponseState, setAiResponseState] = useState<AIResponseState>('idle');
  const [isListening, setIsListening] = useState(false);
  const [isAiSpeaking, setIsAiSpeaking] = useState(false);
  const [wakeWordEnabled, setWakeWordEnabled] = usePersistedState('multimodal_wake_word_enabled', true);
  const [wakeWordDetected, setWakeWordDetected] = useState(false);
  const [wakeWordStatusText, setWakeWordStatusText] = useState("唤醒词未开启");
  const [wakeWordLiveTranscript, setWakeWordLiveTranscript] = useState("");
  const [continuousMode, setContinuousMode] = useState(false);
  const [continuousSpeechPaused, setContinuousSpeechPaused] = useState(false);
  const [interruptMessage, setInterruptMessage] = useState<string | null>(null);
  const isAiSpeakingRef = useRef(false);
  const continuousModeRef = useRef(continuousMode);

  useEffect(() => {
    continuousModeRef.current = continuousMode;
  }, [continuousMode]);

  useEffect(() => {
    isAiSpeakingRef.current = isAiSpeaking;
  }, [isAiSpeaking]);

  const contextValue = useMemo(() => ({
    speechRecognitionState, aiResponseState, isListening, isAiSpeaking,
    wakeWordEnabled, wakeWordDetected, wakeWordStatusText, wakeWordLiveTranscript,
    continuousMode, continuousSpeechPaused, interruptMessage,
    setSpeechRecognitionState, setAiResponseState, setIsListening, setIsAiSpeaking,
    setWakeWordEnabled, setWakeWordDetected, setWakeWordStatusText, setWakeWordLiveTranscript,
    setContinuousMode, setContinuousSpeechPaused, setInterruptMessage,
    isAiSpeakingRef, continuousModeRef,
  }), [
    speechRecognitionState, aiResponseState, isListening, isAiSpeaking,
    wakeWordEnabled, wakeWordDetected, wakeWordStatusText, wakeWordLiveTranscript,
    continuousMode, continuousSpeechPaused, interruptMessage,
  ]);

  return (
    <VoiceContext.Provider value={contextValue}>
      {children}
    </VoiceContext.Provider>
  );
}

export function useVoice() {
  const ctx = useContext(VoiceContext);
  if (!ctx) throw new Error("useVoice must be used within VoiceProvider");
  return ctx;
}
