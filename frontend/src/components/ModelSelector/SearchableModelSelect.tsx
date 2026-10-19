import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Search, Sparkles } from "lucide-react";
import { radius, spacing, transition, zIndex, getShadow, getSurface } from "@/styles";
import type { Theme } from "@/lib/theme";

export interface ModelOption {
  value: string;
  label: string;
  kind: "preset" | "fetched" | "custom";
}

export interface SearchableModelSelectProps {
  value: string;
  onChange: (value: string, option?: ModelOption) => void;
  options: ModelOption[];
  placeholder?: string;
  theme: Theme;
  width?: number | string;
  loading?: boolean;
  disabled?: boolean;
  allowCustom?: boolean;
  onInputChange?: (value: string) => void;
}

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const prev: number[] = Array(n + 1).fill(0).map((_, i) => i);
  for (let i = 1; i <= m; i++) {
    const curr: number[] = [i];
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(
        curr[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + cost
      );
    }
    for (let j = 0; j <= n; j++) prev[j] = curr[j];
  }
  return prev[n];
}

function fuzzyScore(query: string, text: string): number {
  if (!query) return 1;
  const q = query.toLowerCase().trim();
  const t = text.toLowerCase().trim();
  if (t === q) return 10000;
  if (t.startsWith(q)) return 1000 - q.length;
  if (t.includes(q)) return 500;

  // 按字符顺序匹配（容忍中间字符）
  let idx = 0;
  let matched = true;
  for (const ch of q) {
    idx = t.indexOf(ch, idx);
    if (idx === -1) {
      matched = false;
      break;
    }
    idx++;
  }
  if (matched) return 200;

  // 拼写容错：与目标前段比较
  const window = t.slice(0, Math.min(q.length + 3, t.length));
  const dist = levenshtein(q, window);
  if (dist <= 2) return 80 - dist * 25;
  return 0;
}

function highlightText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
  const parts = text.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark
        key={i}
        style={{
          background: "transparent",
          color: "inherit",
          fontWeight: 700,
          textDecoration: "underline",
        }}
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

export function SearchableModelSelect({
  value,
  onChange,
  options,
  placeholder = "搜索或选择模型",
  theme,
  width = "100%",
  loading = false,
  disabled = false,
  allowCustom = true,
  onInputChange,
}: SearchableModelSelectProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState(value || "");
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  useEffect(() => {
    if (!isOpen) setQuery(value || "");
  }, [value, isOpen]);

  const normalizedQuery = query.trim();

  const filtered = useMemo(() => {
    // 当前仅打开下拉且未输入新搜索词时，展示全部选项
    const showAll = !normalizedQuery || normalizedQuery === value;
    const base = options
      .map((opt) => ({ opt, score: fuzzyScore(normalizedQuery, opt.label) }))
      .filter(({ score }) => showAll || score > 0)
      .sort((a, b) => b.score - a.score)
      .map(({ opt }) => opt);

    if (allowCustom && normalizedQuery && normalizedQuery !== value && !options.some((o) => o.value === normalizedQuery)) {
      const custom: ModelOption = {
        value: normalizedQuery,
        label: `使用自定义模型：${normalizedQuery}`,
        kind: "custom",
      };
      return [custom, ...base];
    }
    return base;
  }, [options, normalizedQuery, allowCustom, value]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [filtered.length, normalizedQuery]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      // 下拉面板经 createPortal 挂在 document.body 上，不在 containerRef 内，
      // 必须一并视为"内部"，否则 mousedown 会先关掉下拉，选项的 click 永远收不到
      const inDropdown = dropdownRef.current?.contains(target) ?? false;
      if (containerRef.current && !containerRef.current.contains(target) && !inDropdown) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  useEffect(() => {
    if (!isOpen || !containerRef.current) return;

    const updatePosition = () => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const maxHeight = 320;
      const spaceBelow = window.innerHeight - rect.bottom;
      const top = spaceBelow < maxHeight && rect.top > maxHeight
        ? rect.top - maxHeight - 4
        : rect.bottom + 4;
      setDropdownPos({ top, left: rect.left, width: rect.width });
    };

    updatePosition();

    // 滚动时重新计算固定定位，避免菜单被页面滚动带跑
    const scrollableAncestors: HTMLElement[] = [];
    let curr = containerRef.current.parentElement;
    while (curr) {
      const style = window.getComputedStyle(curr);
      if (style.overflowY === "auto" || style.overflowY === "scroll" || style.overflowX === "auto" || style.overflowX === "scroll") {
        scrollableAncestors.push(curr);
      }
      curr = curr.parentElement;
    }

    const scrollTargets: EventTarget[] = [window, ...scrollableAncestors];
    scrollTargets.forEach((target) => target.addEventListener("scroll", updatePosition, { passive: true } as AddEventListenerOptions));
    window.addEventListener("resize", updatePosition);

    return () => {
      scrollTargets.forEach((target) => target.removeEventListener("scroll", updatePosition));
      window.removeEventListener("resize", updatePosition);
    };
  }, [isOpen, filtered.length]);

  const handleSelect = (opt: ModelOption) => {
    onChange(opt.value, opt);
    setQuery(opt.value);
    setIsOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter")) {
      setIsOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const opt = filtered[highlightedIndex];
      if (opt) handleSelect(opt);
    } else if (e.key === "Escape") {
      setIsOpen(false);
    }
  };

  const selectedOption = options.find((o) => o.value === value);
  const bestSuggestion = normalizedQuery && filtered.length > 0 && filtered[0].kind !== "custom"
    ? filtered[0]
    : null;

  const baseBorder = theme.borderLight;

  const triggerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: `${spacing.sm}px`,
    padding: `${spacing.sm}px ${spacing.md}px`,
    height: 44,
    background: getSurface("secondary", theme.mode),
    border: `1px solid ${baseBorder}`,
    borderRadius: radius.md,
    cursor: disabled ? "not-allowed" : "text",
    opacity: disabled ? 0.5 : 1,
    boxShadow: getShadow("base", theme.mode),
    transition: `border-color ${transition.base}, box-shadow ${transition.base}`,
    color: theme.text,
    fontSize: 13,
    width,
    boxSizing: "border-box",
  };

  const dropdownStyle: React.CSSProperties = {
    position: "fixed",
    top: dropdownPos.top,
    left: dropdownPos.left,
    width: dropdownPos.width,
    maxHeight: 320,
    overflowY: "auto",
    background: getSurface("tertiary", theme.mode),
    border: `1px solid ${theme.borderLight}`,
    borderRadius: radius.md,
    boxShadow: getShadow("hover", theme.mode),
    zIndex: zIndex.dropdown,
    padding: `${spacing.xs}px`,
  };

  const optionStyle = (selected: boolean, highlighted: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: "8px",
    padding: `${spacing.sm}px ${spacing.md}px`,
    cursor: "pointer",
    borderRadius: radius.sm,
    transition: `background ${transition.fast}`,
    fontSize: 13,
    color: theme.text,
    background: selected ? theme.primaryLight : highlighted ? (theme.mode === "dark" ? theme.bgTertiary : theme.borderLight) : "transparent",
    fontWeight: selected ? 600 : 400,
  });

  const badgeStyle = (kind: ModelOption["kind"]): React.CSSProperties => ({
    fontSize: 10,
    padding: "2px 6px",
    borderRadius: radius.sm,
    fontWeight: 600,
    whiteSpace: "nowrap",
    color: kind === "preset" ? theme.primary : kind === "fetched" ? theme.secondary : theme.accent,
    background: kind === "preset" ? `${theme.primary}18` : kind === "fetched" ? `${theme.secondary}18` : `${theme.accent}18`,
  });

  const kindLabel: Record<ModelOption["kind"], string> = {
    preset: "系统预设",
    fetched: "已获取",
    custom: "自定义",
  };

  return (
    <div ref={containerRef} style={{ position: "relative", width }}>
      <div
        style={triggerStyle}
        onClick={() => !disabled && inputRef.current?.focus()}
      >
        <Search size={14} color={theme.textMuted} />
        <input
          ref={inputRef}
          type="text"
          value={query}
          disabled={disabled}
          placeholder={placeholder}
          onFocus={() => setIsOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            onInputChange?.(e.target.value);
            setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          style={{
            flex: 1,
            border: "none",
            outline: "none",
            background: "transparent",
            color: theme.text,
            fontSize: 13,
            minWidth: 0,
          }}
        />
        {loading && (
          <span
            style={{
              width: 14,
              height: 14,
              border: `2px solid ${theme.border}`,
              borderTopColor: theme.primary,
              borderRadius: "50%",
              animation: "spin 0.8s linear infinite",
            }}
          />
        )}
        {selectedOption && !isOpen && (
          <span style={badgeStyle(selectedOption.kind)}>{kindLabel[selectedOption.kind]}</span>
        )}
      </div>

      {bestSuggestion && bestSuggestion.value.toLowerCase() !== normalizedQuery.toLowerCase() && (
        <div
          style={{
            marginTop: 6,
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12,
            color: theme.textSecondary,
          }}
        >
          <Sparkles size={12} color={theme.accent} />
          <span>
            是否指：
            <button
              type="button"
              onClick={() => handleSelect(bestSuggestion)}
              style={{
                background: "none",
                border: "none",
                color: theme.primary,
                cursor: "pointer",
                fontWeight: 600,
                padding: 0,
                textDecoration: "underline",
              }}
            >
              {bestSuggestion.label}
            </button>
          </span>
        </div>
      )}

      {isOpen &&
        createPortal(
          <div ref={dropdownRef} style={dropdownStyle}>
            {filtered.length === 0 ? (
              <div
                style={{
                  padding: `${spacing.md}px`,
                  color: theme.textMuted,
                  fontSize: 13,
                  textAlign: "center",
                }}
              >
                无匹配模型
              </div>
            ) : (
              filtered.map((opt, index) => {
                const selected = value === opt.value;
                const highlighted = index === highlightedIndex;
                return (
                  <div
                    key={`${opt.kind}-${opt.value}`}
                    style={optionStyle(selected, highlighted)}
                    onMouseEnter={(e) => {
                      setHighlightedIndex(index);
                      if (!selected) {
                        e.currentTarget.style.background = theme.mode === "dark" ? theme.bgTertiary : theme.borderLight;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!selected) {
                        e.currentTarget.style.background = "transparent";
                      }
                    }}
                    onClick={() => handleSelect(opt)}
                  >
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {highlightText(opt.label, normalizedQuery)}
                    </span>
                    <span style={badgeStyle(opt.kind)}>{kindLabel[opt.kind]}</span>
                    {selected && <Check size={14} color={theme.primary} />}
                  </div>
                );
              })
            )}
          </div>,
          document.body
        )}
    </div>
  );
}
