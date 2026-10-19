import React from "react";
import { ProviderIcon, type ProviderId } from "@/components/ProviderIcon";
import { Badge, Box, Select } from "@/components/ui";
import type { BadgeVariant } from "@/components/ui/Badge";
import { Check, Loader2 } from "lucide-react";
import type { Theme } from "@/lib/theme";
import type { ModelOption } from "./SearchableModelSelect";
import { SearchableModelSelect } from "./SearchableModelSelect";
import type { ModelProvider } from "./constants";

interface ProviderSelectProps {
  providers: ModelProvider[];
  selectedProvider: ProviderId;
  selectedModel: string;
  savedApiStatus: Record<string, boolean>;
  theme: Theme;
  accentColor: string;
  badgeVariant: BadgeVariant;
  icon: React.ElementType;
  title: string;
  onSelectProvider: (provider: ModelProvider) => void;
  onSelectModel: (model: string) => void;
  children?: React.ReactNode;
  useSearchable?: boolean;
  searchableOptions?: ModelOption[];
  modelLoading?: boolean;
  loadingText?: string;
  onModelInputChange?: (value: string) => void;
  /** 模型输入行右侧的动作区（如"获取模型"按钮） */
  modelAction?: React.ReactNode;
}

export function ProviderSelect({
  providers,
  selectedProvider,
  selectedModel,
  savedApiStatus,
  theme,
  accentColor,
  badgeVariant,
  icon: Icon,
  title,
  onSelectProvider,
  onSelectModel,
  children,
  useSearchable = false,
  searchableOptions = [],
  modelLoading = false,
  loadingText,
  onModelInputChange,
  modelAction,
}: ProviderSelectProps) {
  const currentProviderData = providers.find((p) => p.id === selectedProvider);
  const isLight = theme.mode === "light";

  const modelOptions = (currentProviderData?.models || []).map((m) => ({
    value: m,
    label: m,
  }));

  return (
    <Box
      surface="tertiary"
      radius="lg"
      padding="lg"
      border={theme.borderLight}
      theme={theme}
      style={{
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        boxShadow: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          fontSize: "13px",
          fontWeight: 600,
          color: theme.text,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        <Icon size={16} color={accentColor} />
        <span>{title}</span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "10px",
        }}
      >
        {providers.map((provider) => {
          const selected = selectedProvider === provider.id;
          return (
            <button
              key={provider.id}
              onClick={() => onSelectProvider(provider)}
              style={{
                padding: "12px 14px",
                borderRadius: "12px",
                border: `1px solid ${selected ? accentColor : theme.borderLight}`,
                background: selected
                  ? (isLight ? `${accentColor}12` : `${accentColor}20`)
                  : isLight
                    ? "rgba(255,255,255,0.6)"
                    : theme.bgTertiary,
                color: selected ? theme.text : theme.textSecondary,
                fontSize: "13px",
                fontWeight: selected ? 600 : 500,
                cursor: "pointer",
                transition: "all 150ms ease",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "10px",
                minHeight: "44px",
                position: "relative",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
                <ProviderIcon provider={provider.id} size={18} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {provider.name}
                </span>
              </div>
              {selected ? (
                <Check size={16} color={accentColor} />
              ) : savedApiStatus[provider.id] ? (
                <Badge variant={badgeVariant} size="sm" theme={theme}>已保存</Badge>
              ) : null}
            </button>
          );
        })}
      </div>

      {useSearchable ? (
        modelAction ? (
          <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <SearchableModelSelect
                value={selectedModel}
                onChange={(val) => onSelectModel(val)}
                options={searchableOptions}
                placeholder="搜索或选择模型"
                theme={theme}
                width="100%"
                loading={modelLoading}
                onInputChange={onModelInputChange}
              />
            </div>
            {modelAction}
          </div>
        ) : (
          <SearchableModelSelect
            value={selectedModel}
            onChange={(val) => onSelectModel(val)}
            options={searchableOptions}
            placeholder="搜索或选择模型"
            theme={theme}
            width="100%"
            loading={modelLoading}
            onInputChange={onModelInputChange}
          />
        )
      ) : (
        <Select
          value={selectedModel}
          onChange={onSelectModel}
          options={modelOptions}
          placeholder="-- 选择预设模型 --"
          theme={theme}
          width="100%"
        />
      )}

      {modelLoading && (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", color: theme.textMuted }}>
            <Loader2 size={14} className="spin" />
            <span>{loadingText || "正在加载模型列表..."}</span>
        </div>
      )}

      {children}
    </Box>
  );
}
