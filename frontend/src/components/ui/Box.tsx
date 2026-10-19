/**
 * Box - 通用容器组件
 *
 * 使用设计令牌统一 surface / shadow / radius / padding。
 * 替代散落在各组件中的 inline style 容器样式。
 */
import React from "react";
import { useTheme } from "@/hooks/useTheme";
import {
  radius,
  spacing,
  getShadow,
  getSurface,
  type RadiusKey,
  type SpacingKey,
} from "@/styles";
import type { Theme } from "@/lib/theme";

type SurfaceLevel = "primary" | "secondary" | "tertiary";
type ShadowLevel = "base" | "hover" | "pressed";

export interface BoxProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 表面色级别 */
  surface?: SurfaceLevel;
  /** 阴影级别 */
  shadow?: ShadowLevel;
  /** 圆角级别 */
  radius?: RadiusKey;
  /** 内边距（标准化间距 key 或自定义数值） */
  padding?: SpacingKey | number;
  /** 自定义背景色（覆盖 surface） */
  background?: string;
  /** 自定义边框色 */
  border?: string;
  /** 主题对象（不传则使用 useTheme） */
  theme?: Theme;
  /** 子元素 */
  children?: React.ReactNode;
}

export const Box: React.FC<BoxProps> = ({
  surface,
  shadow,
  radius: radiusKey,
  padding,
  background,
  border,
  theme: themeProp,
  style,
  children,
  ...rest
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;

  const composedStyle: React.CSSProperties = {
    background:
      background ??
      (surface ? getSurface(surface, theme.mode) : undefined),
    boxShadow: shadow ? getShadow(shadow, theme.mode) : undefined,
    borderRadius: radiusKey ? radius[radiusKey] : undefined,
    padding:
      padding !== undefined
        ? typeof padding === "number"
          ? padding
          : spacing[padding]
        : undefined,
    border: border ? `1px solid ${border}` : undefined,
    ...style,
  };

  return (
    <div style={composedStyle} {...rest}>
      {children}
    </div>
  );
};
