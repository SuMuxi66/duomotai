/**
 * MonitorCard — shared frame for monitor panels.
 *
 * Gives each tile a consistent sci-fi-ish frame: thin glowing accent bar
 * on top, labelled header with an icon, and a content slot. Uses the
 * project's design tokens (radius / spacing) and theme.
 */
import React from "react";
import { useTheme } from "@/hooks/useTheme";
import { radius, spacing } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface MonitorCardProps {
  title: string;
  icon?: React.ReactNode;
  accent?: string; // override accent color (defaults to theme.primary)
  right?: React.ReactNode; // header right slot (e.g. live badge)
  children: React.ReactNode;
  theme?: Theme;
  style?: React.CSSProperties;
}

export const MonitorCard: React.FC<MonitorCardProps> = ({
  title,
  icon,
  accent,
  right,
  children,
  theme: themeProp,
  style,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;
  const accentColor = accent ?? theme.primary;

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        background: theme.bgSecondary,
        border: `1px solid ${theme.border}`,
        borderRadius: radius.lg,
        overflow: "hidden",
        boxShadow: `0 4px 18px rgba(0,0,0,${theme.mode === "dark" ? 0.35 : 0.06})`,
        ...style,
      }}
    >
      {/* top accent bar */}
      <div
        style={{
          height: 2,
          background: `linear-gradient(90deg, ${accentColor}, transparent 90%)`,
          boxShadow: `0 0 12px ${accentColor}`,
          flexShrink: 0,
        }}
      />
      {/* subtle grid overlay */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: "2px 0 0 0",
          backgroundImage: `linear-gradient(${theme.borderLight} 1px, transparent 1px), linear-gradient(90deg, ${theme.borderLight} 1px, transparent 1px)`,
          backgroundSize: "28px 28px",
          opacity: theme.mode === "dark" ? 0.5 : 0.35,
          pointerEvents: "none",
        }}
      />
      {/* header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${spacing.md}px ${spacing.lg}px ${spacing.sm}px`,
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {icon && (
            <span style={{ color: accentColor, display: "inline-flex" }}>{icon}</span>
          )}
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              color: theme.textSecondary,
            }}
          >
            {title}
          </span>
        </div>
        {right}
      </div>
      {/* content */}
      <div
        style={{
          padding: `${spacing.sm}px ${spacing.lg}px ${spacing.lg}px`,
          flex: 1,
          minHeight: 0,
          position: "relative",
          zIndex: 1,
        }}
      >
        {children}
      </div>
    </div>
  );
};
