import React from "react";
import { providerIconColors, providerSvgs } from "./ProviderIconMap";

export type ProviderId =
  | "modelscope"
  | "dashscope"
  | "siliconflow"
  | "xiaomi"
  | "deepseek"
  | "zhipu"
  | "sensenova"
  | "browser"
  | "local_sensevoice"
  | "qwen3_asr_realtime"
  | "local_cosyvoice"
  | "xinference"
  | "edge"
  | "custom";

interface ProviderIconProps {
  provider: ProviderId;
  size?: number;
  className?: string;
}

export function ProviderIcon({ provider, size = 24, className = "" }: ProviderIconProps) {
  const svgHtml = providerSvgs[provider];
  if (svgHtml) {
    return (
      <span
        className={className}
        style={{
          width: size,
          height: size,
          fontSize: size,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          lineHeight: 1,
          color: providerIconColors[provider],
        }}
        dangerouslySetInnerHTML={{ __html: svgHtml }}
      />
    );
  }

  const fallbackMap: Partial<Record<ProviderId, React.ReactNode>> = {
    browser: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#607D8B" />
        <rect x="4" y="6" width="16" height="12" rx="2" stroke="#FFF" strokeWidth="2" />
        <circle cx="7" cy="9" r="1" fill="#FFF" />
        <circle cx="10" cy="9" r="1" fill="#FFF" />
        <circle cx="13" cy="9" r="1" fill="#FFF" />
        <path d="M6 12H18" stroke="#FFF" strokeWidth="1" />
      </svg>
    ),
    local_sensevoice: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#4CAF50" />
        <path d="M12 4V14" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M8 8C8 8 8 12 12 12C16 12 16 8 16 8" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M7 16H17" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
    // 端侧语音（香橙派）：橙色底 + 声波条，呼应端侧离线链路（KWS/ASR 运行在设备上）
    edge: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#FF9800" />
        <path d="M5 10v4" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M8.5 7.5v9" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M12 5v14" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M15.5 8v8" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M19 10v4" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
    local_cosyvoice: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#E91E63" />
        <path d="M12 4C9.23858 4 7 6.23858 7 9V15C7 17.7614 9.23858 20 12 20C14.7614 20 17 17.7614 17 15V9C17 6.23858 14.7614 4 12 4Z" fill="#FFF" />
        <circle cx="12" cy="12" r="2" fill="#E91E63" />
      </svg>
    ),
    custom: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#6B7280" />
        <circle cx="12" cy="12" r="3" stroke="#FFF" strokeWidth="2" />
        <path d="M12 5V9" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M12 15V19" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M5 12H9" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
        <path d="M15 12H19" stroke="#FFF" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
    deepseek: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect width="24" height="24" rx="4" fill="#4D6BFE" />
        <path d="M4 15c1.5-4.5 5-7.5 9-7.5s6.5 3 8 7.5c-2.5 1.5-5 2.5-8 2.5a17 17 0 01-9-2.5Z" stroke="#FFF" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M6.5 12a1.2 1.2 0 100 2.4 1.2 1.2 0 000-2.4Z" fill="#FFF" />
      </svg>
    ),
    sensenova: (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" className={className}>
        <rect x="3" y="4" width="18" height="8" rx="2.5" fill="#00E5C9" />
        <rect x="3" y="14" width="18" height="6" rx="2.5" fill="#5B21F3" />
        <rect x="6.5" y="6.5" width="4" height="3" rx="0.5" fill="#FFF" />
        <rect x="14" y="16.5" width="4" height="2" rx="0.5" fill="#FFF" />
      </svg>
    ),
  };

  return (
    fallbackMap[provider] || (
      <svg viewBox="0 0 24 24" width={size} height={size} className={className}>
        <rect width="24" height="24" rx="4" fill="#9E9E9E" />
        <text x="12" y="16" textAnchor="middle" fill="#FFF" fontSize="12" fontWeight="bold">?</text>
      </svg>
    )
  );
}
