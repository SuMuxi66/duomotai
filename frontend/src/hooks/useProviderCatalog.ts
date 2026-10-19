/**
 * Provider 目录 hook：从后端 GET /providers/catalog 拉取供应商/STT/TTS 清单
 *
 * 后端 providers/registry.py 是唯一数据源（display_name / preset_models / has_api_key），
 * 前端不再维护独立清单；拉取失败时回退到 constants.ts 静态数据，保证设置面板离线可用。
 *
 * 模块级缓存：一次页面加载只发一次请求，ModelSelector 与 useModelConfig 等多个
 * 消费者共享同一份数据。
 */
import { useEffect, useMemo, useState } from "react";
import {
  VLM_PROVIDERS,
  STT_PROVIDERS,
  TTS_PROVIDERS,
  type ModelProvider,
} from "@/components/ModelSelector/constants";
import type { ProviderId } from "@/components/ProviderIcon";

interface CatalogEntry {
  name: string;
  display_name: string;
  model: string;
  has_api_key: boolean;
}

interface CatalogVlmEntry {
  name: string;
  display_name: string;
  supports_vlm: boolean;
  default_model: string;
  preset_models: string[];
}

interface ProviderCatalog {
  providers: CatalogVlmEntry[];
  stt: CatalogEntry[];
  tts: CatalogEntry[];
}

export interface ProviderCatalogResult {
  /** VLM 供应商列表（含"自定义"占位项，ModelSelector 可直接使用） */
  vlmProviders: ModelProvider[];
  sttProviders: ModelProvider[];
  ttsProviders: ModelProvider[];
  /** 是否已成功从后端拉取目录（false 时上面三份是静态回退值） */
  loaded: boolean;
}

// 拉取失败时的静态回退（模块常量保证引用稳定，避免 effect 依赖抖动）
const FALLBACK: ProviderCatalogResult = {
  vlmProviders: VLM_PROVIDERS,
  sttProviders: STT_PROVIDERS,
  ttsProviders: TTS_PROVIDERS,
  loaded: false,
};

// "自定义 OpenAI/Anthropic"是纯前端能力，后端目录里没有，始终追加在 VLM 列表末尾
const CUSTOM_VLM_ENTRY: ModelProvider =
  VLM_PROVIDERS.find((p) => p.id === "custom") ?? {
    id: "custom" as ProviderId,
    name: "自定义 OpenAI/Anthropic",
    models: [],
    hasApiKey: true,
    supportsFetch: true,
    isPreset: true,
  };

// supportsFetch（能否从 /v1/models 拉模型列表）取决于各家端点能力，不在 catalog 里下发，
// 以静态清单为基准继承；清单里没有的新供应商保守取 false
function legacyEntry(list: ModelProvider[], id: string): ModelProvider | undefined {
  return list.find((p) => p.id === id);
}

let cache: ProviderCatalog | null = null;
let pending: Promise<ProviderCatalog | null> | null = null;

function fetchCatalog(): Promise<ProviderCatalog | null> {
  if (cache) return Promise.resolve(cache);
  if (!pending) {
    pending = fetch("/providers/catalog")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ProviderCatalog | null) => {
        if (data && Array.isArray(data.providers) && Array.isArray(data.stt) && Array.isArray(data.tts)) {
          cache = data;
          return data;
        }
        return null;
      })
      .catch(() => null);
  }
  return pending;
}

function buildResult(catalog: ProviderCatalog): ProviderCatalogResult {
  const vlmPresets = catalog.providers
    .filter((p) => p.supports_vlm)
    .map((e): ModelProvider => {
      const models = e.preset_models?.length
        ? e.preset_models
        : e.default_model
          ? [e.default_model]
          : [];
      return {
        id: e.name as ProviderId,
        name: e.display_name || e.name,
        models,
        hasApiKey: true,
        supportsFetch: legacyEntry(VLM_PROVIDERS, e.name)?.supportsFetch ?? false,
        isPreset: true,
      };
    });
  const toEntry = (list: ModelProvider[]) => (e: CatalogEntry): ModelProvider => ({
    id: e.name as ProviderId,
    name: e.display_name || e.name,
    models: e.model ? [e.model] : [],
    hasApiKey: !!e.has_api_key,
    supportsFetch: legacyEntry(list, e.name)?.supportsFetch ?? false,
  });
  return {
    vlmProviders: [...vlmPresets, CUSTOM_VLM_ENTRY],
    sttProviders: catalog.stt.map(toEntry(STT_PROVIDERS)),
    ttsProviders: catalog.tts.map(toEntry(TTS_PROVIDERS)),
    loaded: true,
  };
}

export function useProviderCatalog(): ProviderCatalogResult {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(cache);

  useEffect(() => {
    if (cache) return;
    let alive = true;
    fetchCatalog().then((data) => {
      if (alive && data) setCatalog(data);
    });
    return () => {
      alive = false;
    };
  }, []);

  // useMemo 保证目录数据引用稳定，下游 effect/useMemo 依赖不抖动
  return useMemo(
    () => (catalog ? buildResult(catalog) : FALLBACK),
    [catalog]
  );
}
