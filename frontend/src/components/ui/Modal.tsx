/**
 * Modal - 模态框组件
 *
 * 使用 createPortal 渲染到 document.body。
 * 支持背景遮罩点击关闭、ESC 关闭。
 */
import React, { useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { radius, spacing, transition, zIndex, getShadow, getSurface } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  /** 底部操作区 */
  footer?: React.ReactNode;
  /** 模态框宽度，默认 480 */
  width?: number;
  /** 点击遮罩是否关闭，默认 true */
  closeOnBackdrop?: boolean;
  theme?: Theme;
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  footer,
  width = 480,
  closeOnBackdrop = true,
  theme: themeProp,
}) => {
  const ctx = useTheme();
  const theme = themeProp ?? ctx.theme;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
      return () => {
        document.removeEventListener("keydown", handleKeyDown);
        document.body.style.overflow = "";
      };
    }
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const backdropStyle: React.CSSProperties = {
    position: "fixed",
    inset: 0,
    background: "rgba(0, 0, 0, 0.5)",
    backdropFilter: "blur(4px)",
    zIndex: zIndex.modal,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    animation: `fadeIn ${transition.base}`,
  };

  const modalStyle: React.CSSProperties = {
    width: Math.min(width, window.innerWidth - 32),
    maxHeight: "85vh",
    background: getSurface("primary", theme.mode),
    borderRadius: radius.xl,
    boxShadow: getShadow("hover", theme.mode),
    border: `1px solid ${theme.borderLight}`,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  };

  const headerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: `${spacing.lg}px ${spacing.xl}px`,
    borderBottom: `1px solid ${theme.borderLight}`,
    flexShrink: 0,
  };

  const bodyStyle: React.CSSProperties = {
    padding: `${spacing.xl}px`,
    overflowY: "auto",
    flex: 1,
  };

  const footerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: `${spacing.md}px`,
    padding: `${spacing.lg}px ${spacing.xl}px`,
    borderTop: `1px solid ${theme.borderLight}`,
    flexShrink: 0,
  };

  return createPortal(
    <div
      style={backdropStyle}
      onClick={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <div style={modalStyle}>
        {title && (
          <div style={headerStyle}>
            <h2
              style={{
                margin: 0,
                fontSize: 18,
                fontWeight: 600,
                color: theme.text,
              }}
            >
              {title}
            </h2>
            <button
              onClick={onClose}
              style={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                border: "none",
                background: "transparent",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: theme.textMuted,
                transition: `background ${transition.fast}`,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = theme.borderLight;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div style={bodyStyle}>{children}</div>
        {footer && <div style={footerStyle}>{footer}</div>}
      </div>
    </div>,
    document.body
  );
};
