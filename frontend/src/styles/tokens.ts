/**
 * 设计令牌系统 (Design Tokens)
 *
 * 统一管理所有重复的样式常量，替代散落在各组件中的 inline style 值。
 * 所有组件应优先使用这些令牌，而非硬编码值。
 */

// ─── BorderRadius ──────────────────────────────────────────────
// 4 级标准化圆角，替代原有的 8 种不同取值 (6/8/10/12/14/16/20/24px)
export const radius = {
  sm: 8,   // 小元素：标签、Badge
  md: 12,  // 中等元素：按钮、输入框
  lg: 16,  // 大容器：卡片、面板
  xl: 24,  // 超大容器：模态框、全屏面板
} as const;

export type RadiusKey = keyof typeof radius;

// ─── BoxShadow ─────────────────────────────────────────────────
// 3 级阴影，替代 controlShadow / controlHoverShadow / controlPressedShadow
export const shadow = {
  // 基础阴影：卡片、面板默认状态
  base: {
    dark: "0 4px 14px rgba(0, 0, 0, 0.28)",
    light: "0 2px 8px rgba(0, 0, 0, 0.08)",
  },
  // 悬停阴影：按钮、可交互元素 hover 状态
  hover: {
    dark: "0 6px 20px rgba(0, 0, 0, 0.35)",
    light: "0 4px 12px rgba(0, 0, 0, 0.12)",
  },
  // 按压阴影：按钮 active 状态
  pressed: {
    dark: "0 2px 8px rgba(0, 0, 0, 0.20)",
    light: "0 1px 4px rgba(0, 0, 0, 0.06)",
  },
} as const;

// ─── Surface ───────────────────────────────────────────────────
// 3 级表面色，替代 controlSurface 的重复定义
// 注意：这些是半透明覆盖色，用于在视频流等背景上叠加
export const surface = {
  // 一级表面：主要控件背景
  primary: {
    dark: "rgba(36, 36, 36, 0.95)",
    light: "rgba(255, 255, 255, 0.95)",
  },
  // 二级表面：次要控件背景
  secondary: {
    dark: "rgba(31, 31, 31, 0.90)",
    light: "rgba(248, 250, 252, 0.90)",
  },
  // 三级表面：悬浮层、下拉菜单
  tertiary: {
    dark: "rgba(27, 27, 27, 0.88)",
    light: "rgba(241, 245, 249, 0.88)",
  },
} as const;

// ─── Spacing ───────────────────────────────────────────────────
// 标准化间距，替代散落的 padding/margin 值
export const spacing = {
  xs: 4,    // 极小间距：图标与文字间距
  sm: 8,    // 小间距：组件内部间距
  md: 12,   // 中间距：组件间间距
  lg: 16,   // 大间距：区块间距
  xl: 24,   // 超大间距：面板间距
  xxl: 32,  // 最大间距：页面级间距
} as const;

export type SpacingKey = keyof typeof spacing;

// ─── Transition ────────────────────────────────────────────────
// 标准化过渡动画
export const transition = {
  fast: "150ms ease",
  base: "200ms ease",
  slow: "300ms ease",
} as const;

// ─── ZIndex ────────────────────────────────────────────────────
// 层级管理
export const zIndex = {
  base: 0,
  sticky: 200,
  overlay: 500,
  modal: 1000,
  toast: 1100,
  dropdown: 1200,
} as const;

// ─── 辅助函数 ──────────────────────────────────────────────────

/** 根据主题模式获取阴影值 */
export function getShadow(level: keyof typeof shadow, mode: "light" | "dark"): string {
  return shadow[level][mode];
}

/** 根据主题模式获取表面色 */
export function getSurface(level: keyof typeof surface, mode: "light" | "dark"): string {
  return surface[level][mode];
}

/** 根据半径 key 获取像素值 */
export function getRadius(key: RadiusKey): number {
  return radius[key];
}
