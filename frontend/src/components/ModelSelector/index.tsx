import { useMemo, useState } from "react";
import type { ProviderId } from "@/components/ProviderIcon";
import { Badge, Box, Button, Modal, Select, Toggle } from "@/components/ui";
import { radius, spacing } from "@/styles";
import { STORAGE_KEYS, loadFromStorage, saveToStorage } from "@/lib/storage";
import { useModelList } from "@/hooks/useModelList";
import { useProviderCatalog } from "@/hooks/useProviderCatalog";
import {
  Brain,
  Eye,
  Mic,
  ShieldAlert,
  Video,
  Volume2,
} from "lucide-react";
import {
  getDefaultTtsVoice,
  isValidTtsVoice,
  getTtsVoiceOptions,
  type CustomProviderConfig,
  type ModelProvider,
  type ModelSelectorProps,
} from "./constants";
import { ProviderSelect } from "./ProviderSelect";
import { ApiKeyInput } from "./ApiKeyInput";
import { GenerationParams } from "./GenerationParams";
import { AlertSettings } from "./AlertSettings";
export type { ModelSelectorProps } from "./constants";

type TabKey = "vlm" | "stt" | "tts" | "generation" | "alerts" | "camera";

function loadModelMap(key: string): Record<string, string> {
  try {
    const stored = localStorage.getItem(key);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function loadCustomProviders(): CustomProviderConfig[] {
  return loadFromStorage<CustomProviderConfig[]>(STORAGE_KEYS.customVlmProviders, []);
}

function saveCustomProviders(providers: CustomProviderConfig[]): void {
  saveToStorage(STORAGE_KEYS.customVlmProviders, providers);
}

function isCustomProviderId(id: string): boolean {
  return id === "custom" || id.startsWith("custom:");
}

const tabs: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: "vlm", label: "视觉模型", icon: Eye },
  { key: "stt", label: "语音识别", icon: Mic },
  { key: "tts", label: "语音合成", icon: Volume2 },
  { key: "alerts", label: "检测开关", icon: ShieldAlert },
  { key: "camera", label: "摄像头", icon: Video },
];

export function ModelSelector({
  vlmProvider, vlmModel, visionFps, sttProvider, sttModel, ttsProvider, ttsModel, ttsVoice,
  llmTemperature, llmMaxTokens, llmTopP, llmSampleCount, llmFrequencyPenalty, llmResponseFormat, llmEnableThinking,
  dangerAlertEnabled, oilSmokeAlertEnabled,
  cameraSourceMode, onCameraSourceModeChange,
  isOpen, onClose, theme, onTestTts, isTestingTts,
  onVlmProviderChange, onVlmModelChange, onVisionFpsChange,
  edgeKwsUrl, edgeAsrUrl, onEdgeUrlsChange,
  sttPreferCloud = true, onSttPreferCloudChange,
  onSttProviderChange, onSttModelChange,
  onTtsProviderChange, onTtsModelChange, onTtsVoiceChange,
  onApiKeyChange, onLlmGenerationChange,
  onDangerAlertChange, onOilSmokeAlertChange,
  apiKeys,
}: ModelSelectorProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("vlm");
  const [selectedVlmProvider, setSelectedVlmProvider] = useState<string>(vlmProvider);
  const [selectedVlmModel, setSelectedVlmModel] = useState(vlmModel);
  // 端侧语音服务（香橙派）：唤醒模型 KWS 与流式 ASR 服务地址
  const [selectedEdgeKwsUrl, setSelectedEdgeKwsUrl] = useState(edgeKwsUrl ?? "ws://192.168.1.127:8767/kws");
  const [selectedEdgeAsrUrl, setSelectedEdgeAsrUrl] = useState(edgeAsrUrl ?? "ws://192.168.1.127:8768/asr");
  const [selectedVisionFps, setSelectedVisionFps] = useState(visionFps);
  const [selectedSttProvider, setSelectedSttProvider] = useState(sttProvider);
  const [selectedSttModel, setSelectedSttModel] = useState(sttModel);
  const [selectedTtsProvider, setSelectedTtsProvider] = useState(ttsProvider);
  const [selectedTtsModel, setSelectedTtsModel] = useState(ttsModel);
  const [selectedTtsVoice, setSelectedTtsVoice] = useState(ttsVoice);
  const [showApiKey, setShowApiKey] = useState<Record<string, boolean>>({});
  const [vlmModelMap] = useState(() => loadModelMap("multimodal_vlm_model_map"));
  const [sttModelMap] = useState(() => loadModelMap("multimodal_stt_model_map"));
  const [ttsModelMap] = useState(() => loadModelMap("multimodal_tts_model_map"));

  const [customProviders, setCustomProviders] = useState<CustomProviderConfig[]>(() => loadCustomProviders());
  const [customForm, setCustomForm] = useState<CustomProviderConfig>({
    id: "",
    name: "",
    apiFormat: "openai",
    baseUrl: "",
    apiKey: "",
    model: "",
  });
  const [isEditingCustom, setIsEditingCustom] = useState(false);

  // 供应商/模型清单从后端 /providers/catalog 自动读取（registry 单一数据源），拉取失败回退静态清单
  const catalog = useProviderCatalog();

  const vlmProvidersWithCustom: ModelProvider[] = useMemo(() => {
    const custom: ModelProvider[] = customProviders.map((c) => ({
      id: c.id as ProviderId,
      name: c.name,
      models: c.model ? [c.model] : [],
      hasApiKey: true,
      supportsFetch: true,
      isPreset: false,
    }));
    return [...catalog.vlmProviders, ...custom];
  }, [catalog.vlmProviders, customProviders]);

  const currentVlmProviderData = vlmProvidersWithCustom.find((p) => p.id === selectedVlmProvider);
  const currentCustomProvider = useMemo(() => {
    if (!selectedVlmProvider || !isCustomProviderId(selectedVlmProvider)) return null;
    return customProviders.find((c) => c.id === selectedVlmProvider) || null;
  }, [customProviders, selectedVlmProvider]);

  const vlmListParams = useMemo(() => {
    const isCustom = isCustomProviderId(selectedVlmProvider);
    if (!isCustom) {
      return {
        provider: selectedVlmProvider,
        apiKey: apiKeys?.[selectedVlmProvider] || "",
        baseUrl: undefined,
        apiFormat: "openai" as const,
        presetModels: currentVlmProviderData?.models || [],
        supportsFetch: currentVlmProviderData?.supportsFetch ?? false,
      };
    }
    const cfg = (isEditingCustom || selectedVlmProvider === "custom")
      ? customForm
      : (currentCustomProvider || customForm);
    return {
      provider: selectedVlmProvider,
      apiKey: cfg.apiKey,
      baseUrl: cfg.baseUrl,
      apiFormat: cfg.apiFormat,
      presetModels: cfg.model ? [cfg.model] : [],
      supportsFetch: true,
    };
  }, [selectedVlmProvider, currentCustomProvider, customForm, apiKeys, currentVlmProviderData, isEditingCustom]);

  const { options: vlmModelOptions, loading: vlmModelLoading, refresh: refreshVlmModels, validating: vlmValidating, error: vlmModelError } = useModelList(vlmListParams);

  // 各供应商是否已有 key（输入框值 / localStorage）：目录与 apiKeys 到位后自动重算，
  // 保证徽标在目录异步加载完成后依然覆盖全部供应商
  const savedApiStatus = useMemo<Record<string, boolean>>(() => {
    const status: Record<string, boolean> = {};
    [...vlmProvidersWithCustom, ...catalog.sttProviders, ...catalog.ttsProviders].forEach((p) => {
      status[p.id] = !!apiKeys?.[p.id];
    });
    return status;
  }, [vlmProvidersWithCustom, catalog.sttProviders, catalog.ttsProviders, apiKeys]);

  // key 显示只认后端下发的 apiKeys（.env.local 权威），不再读前端 localStorage 持久化
  const [vlmApiKey, setVlmApiKey] = useState(() => apiKeys?.[vlmProvider] || "");
  const [sttApiKey, setSttApiKey] = useState(() => apiKeys?.[sttProvider] || "");
  const [ttsApiKey, setTtsApiKey] = useState(() => apiKeys?.[ttsProvider] || "");

  const [selectedTemperature, setSelectedTemperature] = useState(llmTemperature);
  const [selectedMaxTokens, setSelectedMaxTokens] = useState(llmMaxTokens);
  const [selectedTopP, setSelectedTopP] = useState(llmTopP);
  const [selectedSampleCount, setSelectedSampleCount] = useState(llmSampleCount);
  const [selectedFrequencyPenalty, setSelectedFrequencyPenalty] = useState(llmFrequencyPenalty);
  const [selectedResponseFormat, setSelectedResponseFormat] = useState(llmResponseFormat);
  const [selectedEnableThinking, setSelectedEnableThinking] = useState(llmEnableThinking);
  // 智能检测开关（危险报警/油烟检测）不走确认门控：Toggle 点击即通过
  // onDangerAlertChange/onOilSmokeAlertChange 立即下发 set_alert_settings 并持久化，
  // 保证"开关=行为开关"——关闭即刻停止对应检测（2026-09-06 用户要求）。
  const [saving, setSaving] = useState(false);

  const currentSttProviderData = catalog.sttProviders.find((p) => p.id === selectedSttProvider);
  const currentTtsProviderData = catalog.ttsProviders.find((p) => p.id === selectedTtsProvider);

  const selectProvider = (
    provider: ModelProvider,
    modelMap: Record<string, string>,
    setSelected: (id: string) => void,
    setModel: (m: string) => void,
    setApiKey: (k: string) => void,
  ) => {
    setSelected(provider.id);
    const savedModel = modelMap[provider.id];
    if (savedModel) setModel(savedModel);
    else if (provider.models.length > 0) setModel(provider.models[0]);
    else setModel("");
    setApiKey(apiKeys?.[provider.id] || "");
  };

  const handleSelectTtsProvider = (provider: ModelProvider) => {
    selectProvider(provider, ttsModelMap, setSelectedTtsProvider as (id: string) => void, setSelectedTtsModel, setTtsApiKey);
    if (!isValidTtsVoice(provider.id as ProviderId, selectedTtsVoice)) {
      setSelectedTtsVoice(getDefaultTtsVoice(provider.id as ProviderId));
    }
  };

  const handleSelectVlmProvider = (provider: ModelProvider) => {
    selectProvider(provider, vlmModelMap, setSelectedVlmProvider, setSelectedVlmModel, setVlmApiKey);
    if (provider.id === "custom") {
      setCustomForm({ id: "", name: "", apiFormat: "openai", baseUrl: "", apiKey: "", model: "" });
      setIsEditingCustom(true);
    } else if (isCustomProviderId(provider.id)) {
      const cfg = customProviders.find((c) => c.id === provider.id);
      if (cfg) {
        setCustomForm({ ...cfg });
        setIsEditingCustom(false);
      }
    } else {
      setIsEditingCustom(false);
    }
  };

  const handleSaveCustomProvider = () => {
    const { name, baseUrl, apiKey, model } = customForm;
    if (!name.trim() || !baseUrl.trim() || !apiKey.trim()) return;
    const id = customForm.id || `custom:${name.trim().toLowerCase().replace(/\s+/g, "-")}`;
    const next = { ...customForm, id, name: name.trim(), baseUrl: baseUrl.trim(), apiKey: apiKey.trim(), model: model.trim() };
    setCustomProviders((prev) => {
      const idx = prev.findIndex((c) => c.id === id);
      const list = idx >= 0 ? prev.map((c) => (c.id === id ? next : c)) : [...prev, next];
      saveCustomProviders(list);
      return list;
    });
    setSelectedVlmProvider(id);
    setIsEditingCustom(false);
    if (model.trim() && !selectedVlmModel) {
      setSelectedVlmModel(model.trim());
    }
  };

  const handleDeleteCustomProvider = (id: string) => {
    setCustomProviders((prev) => {
      const list = prev.filter((c) => c.id !== id);
      saveCustomProviders(list);
      return list;
    });
    if (selectedVlmProvider === id) {
      setSelectedVlmProvider("modelscope");
      const modelscopeDefault = catalog.vlmProviders.find((p) => p.id === "modelscope")?.models[0];
      setSelectedVlmModel(modelscopeDefault || "Qwen/Qwen3.8-27B");
      setIsEditingCustom(false);
    }
  };

  const handleClearApiKey = (providerId: string, type: "vlm" | "stt" | "tts") => {
    localStorage.removeItem(`multimodal_${providerId}_api_key`);
    if (type === "vlm") setVlmApiKey("");
    else if (type === "stt") setSttApiKey("");
    else setTtsApiKey("");
    onApiKeyChange(providerId as ProviderId, "");
  };

  const handleConfirm = () => {
    setSaving(true);
    setTimeout(() => {
      if (selectedVlmProvider && selectedVlmModel) {
        const isCustom = isCustomProviderId(selectedVlmProvider);
        const cfg = isCustom
          ? ((isEditingCustom || selectedVlmProvider === "custom") ? customForm : (currentCustomProvider || customForm))
          : null;
        // 非 custom 也显式带上输入框的 key 与选中的模型：经 set_model_provider 原子切换
        // （provider+model 同帧，避免"新 provider + 旧模型"错位被常驻视觉分析调用），
        // key 经后端 persist 落盘（唯一合法写入通道），不再依赖 hello 回写。
        onVlmProviderChange(selectedVlmProvider, cfg ? {
          base_url: cfg.baseUrl,
          api_format: cfg.apiFormat,
          api_key: cfg.apiKey,
        } : {
          api_key: vlmApiKey || undefined,
          vlm_model: selectedVlmModel,
        });
        onVlmModelChange(selectedVlmModel);
        onEdgeUrlsChange?.(selectedEdgeKwsUrl.trim(), selectedEdgeAsrUrl.trim());
        localStorage.setItem("multimodal_vlm_model_map", JSON.stringify({ ...vlmModelMap, [selectedVlmProvider]: selectedVlmModel }));
        if (vlmApiKey && currentVlmProviderData?.hasApiKey && !isCustom) {
          onApiKeyChange(selectedVlmProvider as ProviderId, vlmApiKey);
        }
        if (isCustom && cfg) {
          const next = { ...cfg, model: selectedVlmModel };
          setCustomProviders((prev) => {
            const idx = prev.findIndex((c) => c.id === next.id);
            const list = idx >= 0 ? prev.map((c) => (c.id === next.id ? next : c)) : [...prev, next];
            saveCustomProviders(list);
            return list;
          });
        }
      }
      onVisionFpsChange(selectedVisionFps);
      if (selectedSttProvider && selectedSttModel) {
        // key 与 provider 同帧配对发给后端立即落盘，避免"保存后等 hello 回写"的异步脱节
        onSttProviderChange(selectedSttProvider, sttApiKey || undefined);
        onSttModelChange(selectedSttModel);
        localStorage.setItem("multimodal_stt_model_map", JSON.stringify({ ...sttModelMap, [selectedSttProvider]: selectedSttModel }));
        if (sttApiKey && currentSttProviderData?.hasApiKey) {
          onApiKeyChange(selectedSttProvider, sttApiKey);
        }
      }
      if (selectedTtsProvider && selectedTtsModel) {
        // key 与 provider 同帧配对发给后端立即落盘（原先 onTtsProviderChange 先发 WS、
        // 带的是旧 apiKeys 快照值，用户刚输入的新 key 要等下次 hello 才到后端）
        onTtsProviderChange(selectedTtsProvider, ttsApiKey || undefined);
        onTtsModelChange(selectedTtsModel);
        onTtsVoiceChange(selectedTtsVoice);
        localStorage.setItem("multimodal_tts_model_map", JSON.stringify({ ...ttsModelMap, [selectedTtsProvider]: selectedTtsModel }));
        if (ttsApiKey && currentTtsProviderData?.hasApiKey) {
          onApiKeyChange(selectedTtsProvider, ttsApiKey);
        }
      }
      onLlmGenerationChange({
        temperature: selectedTemperature, maxTokens: selectedMaxTokens, topP: selectedTopP,
        sampleCount: selectedSampleCount, frequencyPenalty: selectedFrequencyPenalty,
        responseFormat: selectedResponseFormat, enableThinking: selectedEnableThinking,
      });
      setSaving(false);
      onClose();
    }, 80);
  };

  const handleCancel = () => {
    onClose();
    setSelectedVlmProvider(vlmProvider); setSelectedVlmModel(vlmModel); setSelectedVisionFps(visionFps);
    setSelectedSttProvider(sttProvider); setSelectedSttModel(sttModel);
    setSelectedTtsProvider(ttsProvider); setSelectedTtsModel(ttsModel); setSelectedTtsVoice(ttsVoice);
    setSelectedTemperature(llmTemperature); setSelectedMaxTokens(llmMaxTokens); setSelectedTopP(llmTopP);
    setSelectedSampleCount(llmSampleCount); setSelectedFrequencyPenalty(llmFrequencyPenalty);
    setSelectedResponseFormat(llmResponseFormat); setSelectedEnableThinking(llmEnableThinking);
    setCustomForm({ id: "", name: "", apiFormat: "openai", baseUrl: "", apiKey: "", model: "" });
    setIsEditingCustom(false);
    setSaving(false);
  };

  const ttsVoiceOptions = getTtsVoiceOptions(selectedTtsProvider, selectedTtsModel);

  const isLight = theme.mode === "light";
  const textMuted = theme.textMuted;

  const sidebarStyle: React.CSSProperties = {
    width: 200,
    flexShrink: 0,
    borderRight: `1px solid ${theme.borderLight}`,
    background: isLight ? "rgba(250,250,252,0.6)" : theme.bgSecondary,
    padding: `${spacing.md}px`,
    display: "flex",
    flexDirection: "column",
    gap: `${spacing.xs}px`,
    position: "sticky",
    top: 0,
    alignSelf: "flex-start",
  };

  const tabBtnStyle = (active: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: `${spacing.md}px`,
    width: "100%",
    padding: `${spacing.md}px ${spacing.lg}px`,
    borderRadius: radius.md,
    border: "none",
    background: active ? (isLight ? "#fff" : theme.bgTertiary) : "transparent",
    color: active ? theme.text : textMuted,
    fontSize: 14,
    fontWeight: active ? 600 : 500,
    cursor: "pointer",
    transition: "all 150ms ease",
    boxShadow: active ? (isLight ? "0 1px 3px rgba(0,0,0,0.06)" : "0 1px 3px rgba(0,0,0,0.25)") : "none",
  });

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: 22,
    fontWeight: 600,
    color: theme.text,
    margin: `0 0 ${spacing.sm}px`,
    letterSpacing: "-0.02em",
  };

  const sectionDescStyle: React.CSSProperties = {
    fontSize: 14,
    color: textMuted,
    marginBottom: `${spacing.xl}px`,
    lineHeight: 1.5,
  };

  const panelStyle: React.CSSProperties = {
    flex: 1,
    padding: `${spacing.xl}px`,
    overflowY: "auto",
    minWidth: 0,
  };

  const renderVlmPanel = () => (
    <>
      <h2 style={sectionTitleStyle}>视觉模型</h2>
      <p style={sectionDescStyle}>选择处理摄像头画面的视觉语言模型，或接入自定义 OpenAI / Anthropic 兼容服务。</p>
      <ProviderSelect
        providers={vlmProvidersWithCustom}
        selectedProvider={selectedVlmProvider as ProviderId}
        selectedModel={selectedVlmModel}
        savedApiStatus={savedApiStatus}
        theme={theme} accentColor={theme.primary} badgeVariant="success" icon={Eye} title="Provider"
        onSelectProvider={handleSelectVlmProvider}
        onSelectModel={setSelectedVlmModel}
        useSearchable
        searchableOptions={vlmModelOptions}
        modelLoading={vlmModelLoading || !!vlmValidating}
        loadingText={vlmValidating ? `端点校验中，正在筛选可用模型…（${vlmValidating.done}/${vlmValidating.total}）` : undefined}
        modelAction={
          vlmListParams.supportsFetch ? (
            <Button
              variant="secondary"
              size="sm"
              theme={theme}
              onClick={refreshVlmModels}
              loading={vlmModelLoading}
              disabled={vlmModelLoading}
            >
              {vlmModelLoading ? "获取中..." : "获取模型"}
            </Button>
          ) : undefined
        }
      >
        {vlmModelError && (
          <div style={{ fontSize: 12, color: theme.error }}>
            获取模型列表失败：{vlmModelError}（请检查 API Key / Base URL 后重试）
          </div>
        )}
        {isCustomProviderId(selectedVlmProvider) && (
          <div style={{ marginTop: spacing.lg, padding: spacing.lg, borderRadius: radius.lg, background: isLight ? "rgba(248,250,252,0.8)" : theme.bgTertiary, border: `1px solid ${theme.borderLight}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.md }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>
                {selectedVlmProvider === "custom" || isEditingCustom ? "新增自定义 Provider" : "自定义 Provider 配置"}
              </span>
              {selectedVlmProvider !== "custom" && !isEditingCustom && (
                <div style={{ display: "flex", gap: spacing.sm }}>
                  <Button variant="ghost" size="sm" theme={theme} onClick={() => setIsEditingCustom(true)}>编辑</Button>
                  <Button variant="ghost" size="sm" theme={theme} style={{ color: theme.error }} onClick={() => handleDeleteCustomProvider(selectedVlmProvider)}>删除</Button>
                </div>
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.md }}>
              <input
                value={customForm.name}
                onChange={(e) => setCustomForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="显示名称"
                className="model-input"
                style={inputStyle(theme)}
              />
              <input
                value={customForm.baseUrl}
                onChange={(e) => setCustomForm((f) => ({ ...f, baseUrl: e.target.value }))}
                placeholder="Base URL（如 https://api.example.com/v1）"
                className="model-input"
                style={inputStyle(theme)}
              />
              <Select
                value={customForm.apiFormat}
                onChange={(v) => setCustomForm((f) => ({ ...f, apiFormat: v as "openai" | "anthropic" }))}
                options={[
                  { value: "openai", label: "OpenAI 兼容格式" },
                  { value: "anthropic", label: "Anthropic 兼容格式" },
                ]}
                theme={theme}
                width="100%"
              />
              <input
                type="password"
                value={customForm.apiKey}
                onChange={(e) => setCustomForm((f) => ({ ...f, apiKey: e.target.value }))}
                placeholder="API Key"
                className="model-input"
                style={inputStyle(theme)}
              />
              <input
                value={customForm.model}
                onChange={(e) => setCustomForm((f) => ({ ...f, model: e.target.value }))}
                placeholder="默认模型名（可选，留空则从下拉选择）"
                className="model-input"
                style={inputStyle(theme)}
              />
              <div style={{ display: "flex", gap: spacing.md, marginTop: spacing.xs }}>
                <Button variant="primary" size="sm" theme={theme} onClick={handleSaveCustomProvider}>
                  {customForm.id ? "保存" : "添加"}
                </Button>
                {isEditingCustom && selectedVlmProvider !== "custom" && (
                  <Button variant="secondary" size="sm" theme={theme} onClick={() => {
                    const cfg = customProviders.find((c) => c.id === selectedVlmProvider);
                    setCustomForm(cfg ? { ...cfg } : { id: "", name: "", apiFormat: "openai", baseUrl: "", apiKey: "", model: "" });
                    setIsEditingCustom(false);
                  }}>取消</Button>
                )}
                <Button variant="ghost" size="sm" theme={theme} onClick={refreshVlmModels} disabled={vlmModelLoading}>
                  {vlmModelLoading ? "刷新中..." : "刷新模型列表"}
                </Button>
              </div>
            </div>
          </div>
        )}
        {currentVlmProviderData?.hasApiKey && !isCustomProviderId(selectedVlmProvider) && (
          <ApiKeyInput
            value={vlmApiKey} onChange={setVlmApiKey}
            onClear={() => handleClearApiKey(selectedVlmProvider, "vlm")}
            providerName={currentVlmProviderData.name} theme={theme}
            showKey={!!showApiKey.vlm}
            onToggleShow={() => setShowApiKey((v) => ({ ...v, vlm: !v.vlm }))}
          />
        )}
        <div style={{ marginTop: spacing.lg, paddingTop: spacing.lg, borderTop: `1px solid ${theme.borderLight}` }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, marginBottom: spacing.md, display: "block" }}>视觉采集帧率</label>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.lg }}>
            <input type="range" min="1" max="30" step="1" value={selectedVisionFps}
              onChange={(e) => setSelectedVisionFps(parseInt(e.target.value, 10))}
              style={{ flex: 1, accentColor: theme.primary, cursor: "pointer" }} />
            <input type="number" min="1" max="30" step="1" value={selectedVisionFps}
              onChange={(e) => { const val = parseInt(e.target.value, 10); if (!isNaN(val) && val >= 1 && val <= 30) setSelectedVisionFps(val); }}
              style={{ width: 64, padding: "8px 10px", borderRadius: radius.md, border: `1px solid ${theme.border}`, background: theme.bgTertiary, color: theme.text, fontSize: 14, fontWeight: 600, textAlign: "center", outline: "none" }} />
            <span style={{ fontSize: 13, color: textMuted, whiteSpace: "nowrap" }}>帧/秒</span>
          </div>
          <div style={{ fontSize: 12, color: textMuted, marginTop: spacing.md }}>更高的帧率会消耗更多带宽和 API 调用次数</div>
        </div>
      </ProviderSelect>

      {/* 生成参数：置于视觉模型配置下方 */}
      <div style={{ marginTop: spacing.xl, paddingTop: spacing.lg, borderTop: `1px solid ${theme.borderLight}` }}>
        <h2 style={sectionTitleStyle}>生成参数</h2>
        <p style={sectionDescStyle}>调整模型输出的采样与格式参数。</p>
        <GenerationParams
          temperature={selectedTemperature} maxTokens={selectedMaxTokens} topP={selectedTopP}
          sampleCount={selectedSampleCount} frequencyPenalty={selectedFrequencyPenalty}
          responseFormat={selectedResponseFormat} enableThinking={selectedEnableThinking}
          onTemperatureChange={setSelectedTemperature} onMaxTokensChange={setSelectedMaxTokens}
          onTopPChange={setSelectedTopP} onSampleCountChange={setSelectedSampleCount}
          onFrequencyPenaltyChange={setSelectedFrequencyPenalty}
          onResponseFormatChange={setSelectedResponseFormat}
          onEnableThinkingChange={setSelectedEnableThinking} theme={theme}
        />
      </div>
    </>
  );

  const renderSttPanel = () => (
    <>
      <h2 style={sectionTitleStyle}>语音识别</h2>
      <p style={sectionDescStyle}>选择将语音转换为文本的服务。</p>
      <ProviderSelect
        providers={catalog.sttProviders}
        selectedProvider={selectedSttProvider}
        selectedModel={selectedSttModel}
        savedApiStatus={savedApiStatus}
        theme={theme} accentColor={theme.secondary} badgeVariant="info" icon={Mic} title="Provider"
        onSelectProvider={(p) => selectProvider(p, sttModelMap, setSelectedSttProvider as (id: string) => void, setSelectedSttModel, setSttApiKey)}
        onSelectModel={setSelectedSttModel}
      >
        {currentSttProviderData?.hasApiKey && (
          <ApiKeyInput
            value={sttApiKey} onChange={setSttApiKey}
            onClear={() => handleClearApiKey(selectedSttProvider, "stt")}
            providerName={currentSttProviderData.name} theme={theme}
            showKey={!!showApiKey.stt}
            onToggleShow={() => setShowApiKey((v) => ({ ...v, stt: !v.stt }))}
          />
        )}
      </ProviderSelect>

      {/* 端侧语音服务（香橙派）：唤醒模型与流式 ASR 服务地址。
          选中「端侧语音（香橙派）」时整块高亮并标注"当前生效"，提示这两个地址即实际链路 */}
      {(() => {
        const edgeActive = selectedSttProvider === "edge";
        return (
          <div
            style={{
              marginTop: spacing.md,
              ...(edgeActive
                ? {
                    padding: spacing.md,
                    borderRadius: radius.lg,
                    border: `1px solid ${theme.secondary}`,
                    background: isLight ? `${theme.secondary}0D` : `${theme.secondary}1A`,
                    boxShadow: `0 0 0 1px ${theme.secondary}22`,
                  }
                : { paddingTop: spacing.md, borderTop: `1px solid ${theme.borderLight}` }),
              transition: "border-color 150ms ease, background 150ms ease",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, marginBottom: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: edgeActive ? theme.secondary : theme.text }}>端侧语音服务</span>
              {edgeActive && <Badge variant="info" size="sm" theme={theme}>当前生效</Badge>}
            </div>
            <p style={sectionDescStyle}>
              唤醒模型（KWS）与流式 ASR 运行在香橙派端侧（模型本身在设备上），此处配置服务地址。
              {edgeActive
                ? "当前语音链路已切换到端侧：唤醒与识别全部离线，使用以下地址。"
                : "选择上方「端侧语音（香橙派）」后，语音链路将使用以下地址。"}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
              <label style={{ fontSize: 12, color: theme.textMuted }}>唤醒模型（KWS）服务地址</label>
              <input
                value={selectedEdgeKwsUrl}
                onChange={(e) => setSelectedEdgeKwsUrl(e.target.value)}
                placeholder="ws://192.168.1.127:8767/kws"
                className="model-input"
                style={inputStyle(theme)}
              />
              <label style={{ fontSize: 12, color: theme.textMuted }}>ASR 流式识别服务地址</label>
              <input
                value={selectedEdgeAsrUrl}
                onChange={(e) => setSelectedEdgeAsrUrl(e.target.value)}
                placeholder="ws://192.168.1.248:8768/asr"
                className="model-input"
                style={inputStyle(theme)}
              />
            </div>

            {/* 识别策略开关：唤醒词模式下"云端优先（端侧兜底）"与"纯端侧低延迟"二选一 */}
            <div
              style={{
                marginTop: spacing.md,
                paddingTop: spacing.md,
                borderTop: `1px solid ${theme.borderLight}`,
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                gap: spacing.md,
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>云端优先（端侧兜底）</div>
                <p style={{ ...sectionDescStyle, margin: `${spacing.xs}px 0 0` }}>
                  {selectedSttProvider === "edge"
                    ? "当前选的是「端侧语音（香橙派）」，云端不参与转写，本开关不生效。"
                    : selectedSttProvider === "browser"
                      ? "当前走浏览器内置识别（Web Speech），不经端侧/云端链路，本开关不生效。"
                      : sttPreferCloud
                        ? "开启：唤醒后音频同时送端侧 ASR 与云端 STT，云端先回就用云端（更准），2.5s 超时或失败自动回退端侧；持续识别/通话直接走云端 STT（实时逐字显示），云端失败自动回退端侧。"
                        : "关闭：只用端侧 ASR（延迟最低），云端不参与；端侧设备不在场时自动回退云端。"}
                  {" 唤醒词与持续识别/通话模式共用本开关；持续识别在重开后生效。"}
                </p>
              </div>
              <Toggle
                checked={sttPreferCloud}
                onChange={(next) => onSttPreferCloudChange?.(next)}
                disabled={selectedSttProvider === "edge" || selectedSttProvider === "browser"}
                theme={theme}
              />
            </div>
          </div>
        );
      })()}
    </>
  );

  const renderTtsPanel = () => (
    <>
      <h2 style={sectionTitleStyle}>语音合成</h2>
      <p style={sectionDescStyle}>选择将 AI 回复转换为语音的服务与音色。</p>
      <ProviderSelect
        providers={catalog.ttsProviders}
        selectedProvider={selectedTtsProvider}
        selectedModel={selectedTtsModel}
        savedApiStatus={savedApiStatus}
        theme={theme} accentColor={theme.accent} badgeVariant="warning" icon={Volume2} title="Provider"
        onSelectProvider={handleSelectTtsProvider}
        onSelectModel={setSelectedTtsModel}
      >
        <div style={{ marginTop: spacing.md }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: theme.textSecondary, marginBottom: spacing.sm, display: "block" }}>音色</label>
          <Select value={selectedTtsVoice} onChange={setSelectedTtsVoice}
            options={ttsVoiceOptions} placeholder="选择音色" theme={theme} width="100%" />
        </div>
        {currentTtsProviderData?.hasApiKey && (
          <ApiKeyInput
            value={ttsApiKey} onChange={setTtsApiKey}
            onClear={() => handleClearApiKey(selectedTtsProvider, "tts")}
            providerName={currentTtsProviderData.name} theme={theme}
            showKey={!!showApiKey.tts}
            onToggleShow={() => setShowApiKey((v) => ({ ...v, tts: !v.tts }))}
          />
        )}
        {onTestTts && (
          <div style={{ marginTop: spacing.lg, paddingTop: spacing.md, borderTop: `1px solid ${theme.borderLight}` }}>
            <Button
              variant="secondary"
              theme={theme}
              onClick={onTestTts}
              loading={!!isTestingTts}
              disabled={!!isTestingTts}
              icon={<Volume2 size={16} />}
            >
              {isTestingTts ? "测试中..." : "测试语音"}
            </Button>
          </div>
        )}
      </ProviderSelect>
    </>
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleCancel}
      width={960}
      theme={theme}
      footer={
        <>
          <Button variant="secondary" onClick={handleCancel} theme={theme}>取消</Button>
          <Button variant="primary" onClick={handleConfirm} loading={saving} disabled={!selectedVlmProvider || !selectedVlmModel} theme={theme}>
            确认
          </Button>
        </>
      }
    >
      <div style={{ display: "flex", height: "100%", minHeight: 520 }}>
        <aside style={sidebarStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, padding: `${spacing.sm}px ${spacing.md}px`, marginBottom: spacing.md }}>
            <Brain size={20} color={theme.primary} />
            <span style={{ fontSize: 15, fontWeight: 700, color: theme.text }}>模型配置</span>
          </div>
          {tabs.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.key} style={tabBtnStyle(activeTab === t.key)} onClick={() => setActiveTab(t.key)}>
                <Icon size={18} />
                <span>{t.label}</span>
              </button>
            );
          })}
        </aside>
        <div style={panelStyle}>
          {activeTab === "vlm" && renderVlmPanel()}
          {activeTab === "stt" && renderSttPanel()}
          {activeTab === "tts" && renderTtsPanel()}
          {activeTab === "alerts" && (
            <>
              <h2 style={sectionTitleStyle}>检测开关</h2>
              <p style={sectionDescStyle}>控制视觉分析中的安全检测项。</p>
              <AlertSettings
          dangerAlert={dangerAlertEnabled} oilSmokeAlert={oilSmokeAlertEnabled}
          onDangerAlertChange={onDangerAlertChange}
          onOilSmokeAlertChange={onOilSmokeAlertChange} theme={theme}
              />
            </>
          )}
          {activeTab === "camera" && (
            <>
              <h2 style={sectionTitleStyle}>摄像头</h2>
              <p style={sectionDescStyle}>选择画面来源：香橙派网络摄像头（摆位自由）或插在后端主机上的 USB 摄像头。</p>
              <Box
                surface="tertiary"
                radius="lg"
                padding="lg"
                border={theme.borderLight}
                theme={theme}
              >
                <div style={{ display: "flex", alignItems: "center", gap: spacing.md }}>
                  <Video size={18} color={theme.primary} />
                  <Toggle
                    checked={cameraSourceMode === "network"}
                    onChange={(v) => onCameraSourceModeChange(v ? "network" : "local")}
                    theme={theme}
                    label="使用网络摄像头（香橙派）"
                  />
                </div>
                <div style={{ marginTop: spacing.md, fontSize: "12px", color: theme.textMuted, lineHeight: 1.5 }}>
                  开启：后端优先使用香橙派推流的画面（断流自动回退本机 USB 摄像头）；
                  关闭：使用插在后端主机上的 USB 摄像头（忽略网络摄像头帧）。
                  画面来源同时用于前端预览、AI 识图与危险/油烟检测。切换即时生效并持久化保存。
                </div>
              </Box>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}

function inputStyle(theme: ModelSelectorProps["theme"]): React.CSSProperties {
  return {
    width: "100%",
    padding: "10px 14px",
    borderRadius: radius.md,
    border: `1px solid ${theme.border}`,
    background: theme.bgTertiary,
    color: theme.text,
    fontSize: 14,
    outline: "none",
    boxSizing: "border-box",
    transition: "border-color 150ms ease, box-shadow 150ms ease",
  };
}
