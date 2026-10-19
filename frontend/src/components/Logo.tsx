import React from "react";
import type { Theme } from "@/lib/theme";

interface LogoProps {
  theme: Theme;
  size?: number;
}

export const Logo: React.FC<LogoProps> = ({ theme, size = 40 }) => {
  const gradientId = `logo-gradient-${theme.mode}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ display: "block" }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2={size} y2={size} gradientUnits="userSpaceOnUse">
          <stop stopColor={theme.primary} />
          <stop offset="1" stopColor={theme.secondary} />
        </linearGradient>
      </defs>
      <rect width={size} height={size} rx={size * 0.2} fill={`url(#${gradientId})`} />
      {/* 锅铲：弧形铲面 + 颈部 + 导流槽 + 锥形手柄 + 挂孔 */}
      {/* 铲面与颈部（弧形轮廓，底部收窄连接手柄） */}
      <path
        d={`M${size * 0.25} ${size * 0.1} Q${size * 0.18} ${size * 0.1} ${size * 0.18} ${size * 0.17} L${size * 0.18} ${size * 0.32} Q${size * 0.18} ${size * 0.38} ${size * 0.24} ${size * 0.38} L${size * 0.38} ${size * 0.38} L${size * 0.42} ${size * 0.44} L${size * 0.58} ${size * 0.44} L${size * 0.62} ${size * 0.38} L${size * 0.76} ${size * 0.38} Q${size * 0.82} ${size * 0.38} ${size * 0.82} ${size * 0.32} L${size * 0.82} ${size * 0.17} Q${size * 0.82} ${size * 0.1} ${size * 0.75} ${size * 0.1} Z`}
        fill="white"
        fillOpacity={0.95}
      />
      {/* 铲面导流槽（4 条竖线，模拟硅胶铲纹路） */}
      <line x1={size * 0.3} y1={size * 0.15} x2={size * 0.3} y2={size * 0.34} stroke={theme.primary} strokeWidth={size * 0.018} strokeOpacity={0.3} strokeLinecap="round" />
      <line x1={size * 0.4} y1={size * 0.15} x2={size * 0.4} y2={size * 0.34} stroke={theme.primary} strokeWidth={size * 0.018} strokeOpacity={0.3} strokeLinecap="round" />
      <line x1={size * 0.6} y1={size * 0.15} x2={size * 0.6} y2={size * 0.34} stroke={theme.primary} strokeWidth={size * 0.018} strokeOpacity={0.3} strokeLinecap="round" />
      <line x1={size * 0.7} y1={size * 0.15} x2={size * 0.7} y2={size * 0.34} stroke={theme.primary} strokeWidth={size * 0.018} strokeOpacity={0.3} strokeLinecap="round" />
      {/* 手柄（锥形，上宽下窄，末端圆角） */}
      <path
        d={`M${size * 0.42} ${size * 0.44} L${size * 0.58} ${size * 0.44} L${size * 0.55} ${size * 0.8} Q${size * 0.55} ${size * 0.85} ${size * 0.5} ${size * 0.85} Q${size * 0.45} ${size * 0.85} ${size * 0.45} ${size * 0.8} Z`}
        fill="white"
        fillOpacity={0.9}
      />
      {/* 手柄末端挂孔（用背景渐变填充模拟镂空） */}
      <circle cx={size * 0.5} cy={size * 0.76} r={size * 0.028} fill={`url(#${gradientId})`} />
    </svg>
  );
};
