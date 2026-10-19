export type ThemeMode = "light" | "dark";

export interface Theme {
  mode: ThemeMode;

  bg: string;
  bgSecondary: string;
  bgTertiary: string;

  text: string;
  textSecondary: string;
  textMuted: string;

  border: string;
  borderLight: string;

  primary: string;
  primaryHover: string;
  primaryLight: string;
  primaryGlow: string;

  secondary: string;
  secondaryHover: string;
  secondaryLight: string;

  accent: string;
  accentHover: string;
  accentLight: string;

  success: string;
  error: string;
  errorLight: string;
  warning: string;

  userBubble: string;
  userBubbleText: string;

  assistantBubble: string;
  assistantBubbleText: string;

  visionBubble: string;
  visionBubbleText: string;

  inputBg: string;
  inputBorder: string;
  inputText: string;

  buttonPrimary: string;
  buttonPrimaryText: string;

  buttonSecondary: string;
}

// 莫兰迪配色方案 - 深色主题
export const darkTheme: Theme = {
  mode: "dark",

  bg: "#111111",
  bgSecondary: "#1b1b1b",
  bgTertiary: "#242424",

  text: "#f2f2f0",
  textSecondary: "#c7c7c4",
  textMuted: "#8f8f8c",

  border: "rgba(255, 255, 255, 0.12)",
  borderLight: "rgba(255, 255, 255, 0.08)",

  primary: "#6a9fb5",
  primaryHover: "#7aafcc",
  primaryLight: "rgba(106, 159, 181, 0.15)",
  primaryGlow: "rgba(106, 159, 181, 0.25)",

  secondary: "#c4a484",
  secondaryHover: "#d4b494",
  secondaryLight: "rgba(196, 164, 132, 0.15)",

  accent: "#9b8aa6",
  accentHover: "#ab9ab6",
  accentLight: "rgba(155, 138, 166, 0.15)",

  success: "#87a987",
  error: "#c17e7e",
  errorLight: "rgba(193, 126, 126, 0.15)",
  warning: "#f59e0b",

  userBubble: "linear-gradient(135deg, #6a9fb5 0%, #5a8f9e 100%)",
  userBubbleText: "#fff",

  assistantBubble: "#1f1f1f",
  assistantBubbleText: "#f2f2f0",

  visionBubble: "linear-gradient(135deg, #9b8aa6 0%, #8b7a96 100%)",
  visionBubbleText: "#e8e6e3",

  inputBg: "#1f1f1f",
  inputBorder: "rgba(255, 255, 255, 0.12)",
  inputText: "#f2f2f0",

  buttonPrimary: "linear-gradient(135deg, #6a9fb5 0%, #5a8f9e 100%)",
  buttonPrimaryText: "#fff",

  buttonSecondary: "#1f1f1f",
};

// 现代中性配色方案 - 浅色主题 (去除紫色，使用灰蓝中性色)
export const lightTheme: Theme = {
  mode: "light",

  bg: "#f8fafc",                    // Slate 50 - 极浅灰蓝背景
  bgSecondary: "#ffffff",           // 纯白卡片
  bgTertiary: "#f1f5f9",            // Slate 100 - 第三层背景

  text: "#0f172a",                  // Slate 900 - 主文本
  textSecondary: "#334155",         // Slate 700 - 次要文本
  textMuted: "#64748b",             // Slate 500 - 辅助文本

  border: "rgba(148, 163, 184, 0.3)",      // Slate 400 边框
  borderLight: "rgba(148, 163, 184, 0.15)", // Slate 400 浅边框

  primary: "#3b82f6",               // Blue 500 (主色)
  primaryHover: "#2563eb",          // Blue 600
  primaryLight: "rgba(59, 130, 246, 0.12)",
  primaryGlow: "rgba(59, 130, 246, 0.25)",

  secondary: "#10b981",             // Emerald 500 (强调色)
  secondaryHover: "#059669",        // Emerald 600
  secondaryLight: "rgba(16, 185, 129, 0.12)",

  accent: "#6366f1",                // Indigo 500 (辅助强调)
  accentHover: "#4f46e5",
  accentLight: "rgba(99, 102, 241, 0.12)",

  success: "#10b981",               // Emerald
  error: "#ef4444",                 // Red
  errorLight: "rgba(239, 68, 68, 0.12)",
  warning: "#d97706",               // Amber 600

  userBubble: "#ffffff",
  userBubbleText: "#0f172a",

  assistantBubble: "#ffffff",
  assistantBubbleText: "#0f172a",

  visionBubble: "linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%)",
  visionBubbleText: "#0369a1",

  inputBg: "#ffffff",
  inputBorder: "rgba(148, 163, 184, 0.4)",
  inputText: "#0f172a",

  buttonPrimary: "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
  buttonPrimaryText: "#fff",

  buttonSecondary: "#ffffff",
};

export const getTheme = (mode: ThemeMode): Theme => {
  return mode === "light" ? lightTheme : darkTheme;
};
