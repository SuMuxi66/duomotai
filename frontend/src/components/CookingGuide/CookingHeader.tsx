import React from "react";
import { ArrowLeft } from "lucide-react";
import type { Theme } from "@/lib/theme";
import { radius, spacing } from "@/styles";

interface CookingHeaderProps {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  onBack?: () => void;
  theme: Theme;
  iconBg?: string;
  marginBottom?: number;
}

export function CookingHeader({
  icon,
  title,
  subtitle,
  onBack,
  theme,
  iconBg,
  marginBottom = 24,
}: CookingHeaderProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: `${spacing.md}px`,
        marginBottom: `${marginBottom}px`,
      }}
    >
      {onBack && (
        <ArrowLeft
          size={20}
          color={theme.textMuted}
          strokeWidth={1.5}
          style={{ cursor: "pointer", flexShrink: 0 }}
          onClick={onBack}
        />
      )}
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: radius.sm + 2,
          background: iconBg ?? theme.primaryLight,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {icon}
      </div>
      <div style={{ minWidth: 0 }}>
        <h2
          style={{
            fontSize: 16,
            fontWeight: 600,
            color: theme.text,
            margin: 0,
          }}
        >
          {title}
        </h2>
        {subtitle && (
          <p
            style={{
              fontSize: 12,
              color: theme.textMuted,
              margin: 0,
            }}
          >
            {subtitle}
          </p>
        )}
      </div>
    </div>
  );
}
