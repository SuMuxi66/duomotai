import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { STORAGE_KEYS, loadFromStorage, saveToStorage } from "@/lib/storage";
import type { ModelOption } from "@/components/ModelSelector/SearchableModelSelect";

export interface UseModelListOptions {
  provider: string;
  apiKey?: string;
  baseUrl?: string;
  apiFormat?: "openai" | "anthropic";
  presetModels?: string[];
  supportsFetch?: boolean;
  enabled?: boolean;
}

export interface UseModelListReturn {
  options: ModelOption[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  lastSource: "preset" | "fetched" | null;
  validating: { done: number; total: number } | null;
}

interface CacheEntry {
  provider: string;
  baseUrl?: string;
  models: string[];
  fetchedAt: number;
}

function getCacheKey(provider: string, baseUrl?: string): string {
  return `${provider}:${baseUrl || ""}`;
}

function loadCachedModels(provider: string, baseUrl?: string): string[] | null {
  const cache = loadFromStorage<Record<string, CacheEntry>>(STORAGE_KEYS.vlmModelListCache, {});
  const entry = cache[getCacheKey(provider, baseUrl)];
  if (!entry) return null;
  // Cache valid for 24 hours
  if (Date.now() - entry.fetchedAt > 24 * 60 * 60 * 1000) return null;
  return entry.models;
}

interface ValidationEntry {
  ok: boolean;
  at: number;
}

type ValidationStore = Record<string, Record<string, ValidationEntry>>;

// 校验结果缓存：provider + key 指纹为桶，模型为键。
// ok=true 长期有效；ok=false 24 小时后允许重试（应对瞬时报错/限流）
const VALIDATION_STORAGE_KEY = "multimodal_vlm_model_validation";
const VALIDATION_FAIL_TTL = 24 * 60 * 60 * 1000;

function loadValidationStore(): ValidationStore {
  try {
    return JSON.parse(localStorage.getItem(VALIDATION_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveValidationEntry(store: ValidationStore, provider: string, keyFp: string, model: string, ok: boolean): void {
  const bucket = store[provider + ":" + keyFp] || {};
  bucket[model] = { ok, at: Date.now() };
  store[provider + ":" + keyFp] = bucket;
  try {
    localStorage.setItem(VALIDATION_STORAGE_KEY, JSON.stringify(store));
  } catch { /* 存储满时忽略 */ }
}

function validationState(store: ValidationStore, provider: string, keyFp: string, model: string): boolean | null {
  const entry = store[provider + ":" + keyFp]?.[model];
  if (!entry) return null; // 从未校验
  if (entry.ok) return true;
  if (Date.now() - entry.at > VALIDATION_FAIL_TTL) return null; // 失败已过期，允许重试
  return false;
}

function keyFingerprint(apiKey?: string): string {
  return (apiKey || "").slice(-8);
}

function saveCachedModels(provider: string, baseUrl: string | undefined, models: string[]): void {
  const cache = loadFromStorage<Record<string, CacheEntry>>(STORAGE_KEYS.vlmModelListCache, {});
  cache[getCacheKey(provider, baseUrl)] = {
    provider,
    baseUrl,
    models,
    fetchedAt: Date.now(),
  };
  saveToStorage(STORAGE_KEYS.vlmModelListCache, cache);
}

/**
 * 模型列表管理 hook
 *
 * - 将 presetModels 作为系统预设选项（kind=preset）
 * - 优先从后端 /models 拉取 provider 的模型列表（kind=fetched）
 * - 支持自定义输入模型名（kind=custom），由 SearchableModelSelect 动态生成
 * - 拉取结果缓存到 localStorage，24 小时内复用
 */
export function useModelList(options: UseModelListOptions): UseModelListReturn {
  const { provider, apiKey, baseUrl, apiFormat = "openai", presetModels = [], supportsFetch = true, enabled = true } = options;


  const [fetchedModels, setFetchedModels] = useState<string[]>(() => {
    if (!supportsFetch) return [];
    return loadCachedModels(provider, baseUrl) || [];
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSource, setLastSource] = useState<"preset" | "fetched" | null>(null);
  // 端点校验：每个模型需通过"带图小请求"实测（可对话且可识图）才进入列表
  const [validatedModels, setValidatedModels] = useState<string[]>([]);
  const [validating, setValidating] = useState<{ done: number; total: number } | null>(null);
  const validationRunIdRef = useRef(0);
  const validationStoreRef = useRef<ValidationStore>(loadValidationStore());

  const abortRef = useRef<AbortController | null>(null);
  const lastFetchKeyRef = useRef<string>("");
  const fetchedModelsRef = useRef<string[]>(fetchedModels);
  useEffect(() => {
    fetchedModelsRef.current = fetchedModels;
  }, [fetchedModels]);

  const fetchModels = useCallback(async (force = false) => {
    if (!enabled || !supportsFetch || !provider) return;

    // 对于内置 provider，baseUrl 和 apiKey 非必需；自定义 provider 需要 baseUrl
    const isCustom = provider === "custom" || provider.startsWith("custom:");
    if (isCustom && !baseUrl) {
      setFetchedModels([]);
      setLastSource("preset");
      return;
    }

    const fetchKey = `${provider}:${baseUrl || ""}:${apiKey || ""}:${apiFormat}`;
    if (!force && fetchKey === lastFetchKeyRef.current && fetchedModelsRef.current.length > 0) return;

    // 缓存命中：24h 内已拉取过的列表直接复用，跳过 API 请求
    if (!force) {
      const cached = loadCachedModels(provider, baseUrl);
      if (cached && cached.length > 0) {
        setFetchedModels(cached);
        setLastSource("fetched");
        lastFetchKeyRef.current = fetchKey;
        setLoading(false);
        return;
      }
    }

    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setLoading(true);
    setError(null);

    const params = new URLSearchParams({ provider, api_format: apiFormat });
    if (apiKey) params.set("api_key", apiKey);
    if (baseUrl) params.set("base_url", baseUrl);

    try {
      const res = await fetch(`/models?${params.toString()}`, { signal: abortRef.current.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const models: string[] = Array.isArray(data.models) ? data.models : [];
      setFetchedModels(models);
      setLastSource(data.source === "fetched" ? "fetched" : "preset");
      if (data.source === "fetched") {
        saveCachedModels(provider, baseUrl, models);
      }
      lastFetchKeyRef.current = fetchKey;
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message);
        setLastSource("preset");
      }
    } finally {
      setLoading(false);
    }
  }, [enabled, supportsFetch, provider, baseUrl, apiKey, apiFormat]);

  useEffect(() => {
    // Provider/baseUrl/apiKey 变化时，先尝试读缓存
    const cached = supportsFetch ? loadCachedModels(provider, baseUrl) : [];
    if (cached) {
      setFetchedModels(cached);
      setLastSource("fetched");
    } else {
      setFetchedModels([]);
      setLastSource("preset");
    }
    lastFetchKeyRef.current = "";
    fetchModels();
  }, [provider, baseUrl, apiKey, supportsFetch]);

  // 校验调度：未通过/未校验的模型进队列，4 路并发实测；列表随校验结果渐进生长
  useEffect(() => {
    if (!supportsFetch || fetchedModels.length === 0) {
      setValidatedModels([]);
      setValidating(null);
      return;
    }
    const keyFp = keyFingerprint(apiKey);
    const store = validationStoreRef.current;
    const okNow = fetchedModels.filter((m) => validationState(store, provider, keyFp, m) === true);
    setValidatedModels(okNow);
    const pending = fetchedModels.filter((m) => validationState(store, provider, keyFp, m) === null);
    if (pending.length === 0 || !apiKey) {
      setValidating(null);
      return;
    }

    const runId = ++validationRunIdRef.current;
    setValidating({ done: 0, total: pending.length });
    let done = 0;
    const queue = [...pending];

    const worker = async () => {
      while (queue.length > 0) {
        if (validationRunIdRef.current !== runId) return;
        const model = queue.shift();
        if (!model) break;
        let ok = false;
        try {
          const res = await fetch("/models/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider, api_key: apiKey, model, base_url: baseUrl }),
          });
          const data = await res.json();
          ok = !!data.ok;
        } catch {
          ok = false;
        }
        if (validationRunIdRef.current !== runId) return;
        saveValidationEntry(validationStoreRef.current, provider, keyFp, model, ok);
        done += 1;
        setValidating({ done, total: pending.length });
        if (ok) {
          setValidatedModels((prev) => (prev.includes(model) ? prev : [...prev, model]));
        }
      }
    };

    void Promise.allSettled([worker(), worker(), worker(), worker()]);
    return () => {
      validationRunIdRef.current += 1; // 卸载/重跑时废弃旧 worker 的产出
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchedModels, provider, apiKey, baseUrl, supportsFetch]);

  const modelOptions = useMemo<ModelOption[]>(() => {
    const presetSet = new Set(presetModels);
    const presetOptions: ModelOption[] = presetModels.map((m) => ({
      value: m,
      label: m,
      kind: "preset" as const,
    }));

    // 去重：已出现在 preset 中的模型不再重复列为 fetched
    const fetchedOptions: ModelOption[] = validatedModels
      .filter((m) => !presetSet.has(m))
      .map((m) => ({
        value: m,
        label: m,
        kind: "fetched" as const,
      }));

    return [...presetOptions, ...fetchedOptions];
  }, [presetModels, validatedModels]);

  const refresh = useCallback(() => {
    lastFetchKeyRef.current = "";
    fetchModels(true);
  }, [fetchModels]);

  return { options: modelOptions, loading, error, refresh, lastSource, validating };
}
