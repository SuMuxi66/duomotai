import { MutableRefObject, useCallback, useEffect, useRef, useState } from "react";
import { loadTimersFromStorage, saveTimersToStorage } from "../components/CountdownManager";

export type CookingState =
  | "IDLE"
  | "COLLECTING_INGREDIENTS"
  | "RECOMMENDING"
  | "AWAITING_SERVINGS"
  | "COOKING"
  | "STEP_WAITING"
  | "FINISHED";

export interface RecipeStep {
  order: number;
  title?: string | null;
  content: string;
  tip?: string | null;
}

export interface Recipe {
  name: string;
  category?: string;
  difficulty?: string;
  time?: string | null;
  ingredients: string[];
  prep_items?: string[];
  servings_note?: string | null;
  per_serving_lines?: string[];
  steps: RecipeStep[];
  tips?: string | null;
}

export interface RecipeSearchResult {
  name: string;
  category?: string;
  difficulty?: string;
  match_count?: number;
  matched_ingredients?: string[];
}

export interface CookingStatusPayload {
  enabled: boolean;
  state: string;
  recipe: Recipe | null;
  step: number;
  servings?: number | null;
  awaiting_servings?: boolean;
  ingredients: string[];
  recipes?: RecipeSearchResult[];
  completed_steps?: number[];
  timers?: CookingTimer[];
}

export interface CookingTimer {
  id: string;
  description: string;
  total_seconds: number;
  end_time: number;
}

type SendCookingAction = (action: string, payload?: Record<string, unknown>) => void;

export interface UseCookingModeReturn {
  isActive: boolean;
  cookingState: CookingState;
  currentRecipe: Recipe | null;
  currentStep: number;
  currentServings: number | null;
  completedSteps: number[];
  userIngredients: string[];
  recipes: RecipeSearchResult[];
  timers: CookingTimer[];
  isLoading: boolean;
  error: string | null;
  startCookingMode: () => void;
  exitCookingMode: () => void;
  setIngredients: (ingredients: string) => Promise<void>;
  selectRecipe: (recipeName: string) => Promise<void>;
  confirmStep: () => void;
  skipStep: () => void;
  finishCooking: () => void;
  cancelTimer: () => void;
  updateFromBackend: (payload: Partial<CookingStatusPayload>) => void;
  stopCookingMode: () => void;
}

function parseIngredients(input: string): string[] {
  const cookingTriggers = [
    "我要做饭",
    "我要做菜",
    "开始做饭",
    "开始做菜",
    "做饭",
    "做菜",
    "下厨",
    "炒菜",
    "烹饪",
    "料理",
    "我想做饭",
    "我想做菜",
  ];
  let normalized = input;
  for (const phrase of cookingTriggers) {
    const escapedPhrase = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    normalized = normalized.replace(new RegExp(escapedPhrase, 'g'), " ");
  }
  return normalized
    .split(/[,??\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

// ===== 倒计时"叮"提示音（Web Audio GainNode 放大版）=====
// HTMLAudioElement.volume 上限 1.0，文件本身录音增益低听感偏小；
// 走 AudioContext + GainNode 可做 >1 倍增益。解码结果缓存，触发时零延迟。
const DING_VOLUME_BOOST = 16; // 音量放大倍数（原始录音增益偏低），嫌小可调大
let dingAudioCtx: AudioContext | null = null;
let dingLimiter: DynamicsCompressorNode | null = null;
let dingAudioBuffer: AudioBuffer | null = null;
let dingBufferPromise: Promise<AudioBuffer | null> | null = null;

function loadDingBuffer(): Promise<AudioBuffer | null> {
  if (!dingBufferPromise) {
    dingBufferPromise = (async () => {
      try {
        if (!dingAudioCtx) dingAudioCtx = new AudioContext();
        const res = await fetch("/ding.mp3");
        const arrayBuffer = await res.arrayBuffer();
        dingAudioBuffer = await dingAudioCtx.decodeAudioData(arrayBuffer);
        return dingAudioBuffer;
      } catch (e) {
        console.warn("[倒计时提示音] 音频加载失败:", e);
        return null;
      }
    })();
  }
  return dingBufferPromise;
}

// 播一声"叮"，Promise 在声音自然播完（或失败）时 resolve——循环播报靠它串接
function playDingOnce(): Promise<void> {
  return new Promise<void>((resolve) => {
    void (async () => {
      try {
        if (!dingAudioCtx) dingAudioCtx = new AudioContext();
        if (dingAudioCtx.state === "suspended") void dingAudioCtx.resume();
        const buffer = dingAudioBuffer ?? (await loadDingBuffer());
        if (!buffer || !dingAudioCtx) {
          resolve();
          return;
        }
        const ctx = dingAudioCtx;
        // 高倍增益下必须挂限幅器，否则峰值直接削波成爆音
        if (!dingLimiter) {
          const limiter = ctx.createDynamicsCompressor();
          limiter.threshold.value = -3;
          limiter.knee.value = 0;
          limiter.ratio.value = 20;
          limiter.attack.value = 0.003;
          limiter.release.value = 0.1;
          limiter.connect(ctx.destination);
          dingLimiter = limiter;
        }
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        const gain = ctx.createGain();
        gain.gain.value = DING_VOLUME_BOOST;
        source.connect(gain);
        gain.connect(dingLimiter);
        source.onended = () => {
          source.disconnect();
          gain.disconnect();
          resolve();
        };
        source.start();
      } catch (e) {
        console.warn("[倒计时提示音] 播放失败:", e);
        resolve();
      }
    })();
  });
}

// 循环"叮"直到 alive() 返回 false（倒计时结束/定时器被移除时停）
async function playDingLoopUntil(alive: () => boolean): Promise<void> {
  while (alive()) {
    await playDingOnce();
  }
}

export function useCookingMode(
  sendCookingActionRef: MutableRefObject<SendCookingAction | null>
): UseCookingModeReturn {
  const [isActive, setIsActive] = useState(false);
  const [cookingState, setCookingState] = useState<CookingState>("IDLE");
  const [currentRecipe, setCurrentRecipe] = useState<Recipe | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [currentServings, setCurrentServings] = useState<number | null>(null);
  const [completedSteps, setCompletedSteps] = useState<number[]>([]);
  const [userIngredients, setUserIngredients] = useState<string[]>([]);
  const [recipes, setRecipes] = useState<RecipeSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timers, setTimers] = useState<CookingTimer[]>([]);
  // timers 的镜像 ref：循环播报的存活判断读它拿最新列表，避免闭包捕获旧数组
  const timersRef = useRef<CookingTimer[]>([]);
  useEffect(() => {
    timersRef.current = timers;
  }, [timers]);
  // 已进入"最后3秒循环叮"的定时器 id（每个定时器只启动一个循环）
  const dingLoopIdsRef = useRef<Set<string>>(new Set());

  // 倒计时结束前 3 秒循环播"叮"，直到倒计时结束（250ms 轮询保证 3 秒窗口内必然命中）。
  // 存活判断：定时器仍在列表且未到期——到期后 cooking_status/timer_done 会把它移出
  // timers，即使状态推送延迟，end_time 已过也会让循环在当前一声播完后停止；取消同理。
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now() / 1000;
      const activeIds = new Set(timersRef.current.map((t) => t.id));
      for (const id of Array.from(dingLoopIdsRef.current)) {
        if (!activeIds.has(id)) dingLoopIdsRef.current.delete(id);
      }
      for (const t of timersRef.current) {
        const remaining = t.end_time - now;
        if (remaining > 0 && remaining <= 3 && !dingLoopIdsRef.current.has(t.id)) {
          dingLoopIdsRef.current.add(t.id);
          const timerId = t.id;
          void playDingLoopUntil(() => {
            const current = timersRef.current.find((x) => x.id === timerId);
            return !!current && current.end_time - Date.now() / 1000 > 0;
          });
        }
      }
    }, 250);
    return () => clearInterval(interval);
  }, []);

  // 页面加载时从本地存储恢复倒计时
  useEffect(() => {
    const storedTimers = loadTimersFromStorage();
    if (storedTimers.length > 0) {
      // 转换格式
      const convertedTimers: CookingTimer[] = storedTimers.map(t => ({
        id: t.id,
        description: t.description,
        total_seconds: t.total_seconds,
        end_time: t.end_time,
      }));
      setTimers(convertedTimers);
      console.log(`[CookingMode] 从本地存储恢复 ${convertedTimers.length} 个倒计时`);
    }
  }, []);

  // 倒计时变化时保存到本地存储
  useEffect(() => {
    if (timers.length > 0) {
      // 转换格式
      const storageTimers = timers.map(t => ({
        id: t.id,
        description: t.description,
        total_seconds: t.total_seconds,
        end_time: t.end_time,
        created_at: Date.now() / 1000,
        category: 'cooking' as const,
      }));
      saveTimersToStorage(storageTimers);
    } else {
      // 倒计时为空时清除本地存储
      saveTimersToStorage([]);
    }
  }, [timers]);

  const sendAction = useCallback((action: string, payload: Record<string, unknown> = {}) => {
    if (!sendCookingActionRef.current) {
      setError("后端连接尚未建立，暂时无法使用做饭引导。");
      return false;
    }
    sendCookingActionRef.current(action, payload);
    return true;
  }, [sendCookingActionRef]);

  const startCookingMode = useCallback(() => {
    setIsActive(true);
    setCookingState("COLLECTING_INGREDIENTS");
    setCurrentRecipe(null);
    setCurrentStep(0);
    setCurrentServings(null);
    setCompletedSteps([]);
    setUserIngredients([]);
    setRecipes([]);
    setIsLoading(false);
    setError(null);
    sendAction("start");
  }, [sendAction]);

  const exitCookingMode = useCallback(() => {
    sendAction("stop");
    setIsActive(false);
    setCookingState("IDLE");
    setCurrentRecipe(null);
    setCurrentStep(0);
    setCurrentServings(null);
    setCompletedSteps([]);
    setUserIngredients([]);
    setRecipes([]);
    setTimers([]);
    setIsLoading(false);
    setError(null);
    saveTimersToStorage([]); // 清理本地存储
  }, [sendAction]);

  const setIngredients = useCallback(async (ingredients: string) => {
    const ingredientList = parseIngredients(ingredients);
    if (ingredientList.length === 0) {
      setError("请至少输入一个食材。");
      return;
    }
    setError(null);
    setIsLoading(true);
    setCookingState("RECOMMENDING");
    setUserIngredients((prev) => Array.from(new Set([...prev, ...ingredientList])));
    sendAction("add_ingredients", { ingredients: ingredientList });
  }, [sendAction]);

  const selectRecipe = useCallback(async (recipeName: string) => {
    if (!recipeName) {
      return;
    }
    setError(null);
    setIsLoading(true);
    sendAction("select_recipe", { recipe_name: recipeName });
  }, [sendAction]);

  const confirmStep = useCallback(() => {
    setIsLoading(true);
    sendAction("confirm_step");
  }, [sendAction]);

  const skipStep = useCallback(() => {
    setIsLoading(true);
    sendAction("skip_step");
  }, [sendAction]);

  const finishCooking = useCallback(() => {
    exitCookingMode();
  }, [exitCookingMode]);

  const cancelTimer = useCallback(() => {
    sendAction("cancel_timer");
    setTimers([]);
    saveTimersToStorage([]); // 清除本地存储
  }, [sendAction]);

  // 部分更新语义：仅覆盖 payload 中显式提供的字段，未提供的字段保留当前值。
  // 背景：后端全量推送（cooking_status）携带全部字段，行为不变；
  // 而倒计时到期（timer_done / timer_completed）等局部事件只关心 timers，
  // 旧的全量覆盖逻辑会把未携带的 completed_steps / recipes 等重置为空，
  // 导致长流程中任一倒计时到期后，面板已打勾步骤和当前菜谱全部归零。
  const updateFromBackend = useCallback((payload: Partial<CookingStatusPayload>) => {
    if (payload.enabled !== undefined) {
      setIsActive(payload.enabled);
    }
    if (payload.state !== undefined) {
      setCookingState((payload.state || "IDLE") as CookingState);
    }
    if (payload.recipe !== undefined) {
      setCurrentRecipe(payload.recipe ?? null);
    }
    if (payload.step !== undefined) {
      setCurrentStep(typeof payload.step === "number" ? payload.step : 0);
    }
    if (payload.servings !== undefined) {
      setCurrentServings(typeof payload.servings === "number" ? payload.servings : null);
    }
    if (Array.isArray(payload.ingredients)) {
      setUserIngredients(payload.ingredients);
    }
    if (Array.isArray(payload.recipes)) {
      setRecipes(payload.recipes);
    }
    if (Array.isArray(payload.completed_steps)) {
      setCompletedSteps(payload.completed_steps);
    }

    // 更新倒计时，如果后端返回空数组则清理本地存储
    if (payload.timers !== undefined) {
      const backendTimers = Array.isArray(payload.timers) ? payload.timers : [];
      setTimers(backendTimers);
      if (backendTimers.length === 0) {
        saveTimersToStorage([]);
      }
    }

    setIsLoading(false);
    setError(null);
  }, []);

  const stopCookingMode = useCallback(() => {
    setIsActive(false);
    setCookingState("IDLE");
    setCurrentRecipe(null);
    setCurrentStep(0);
    setCurrentServings(null);
    setCompletedSteps([]);
    setUserIngredients([]);
    setRecipes([]);
    setIsLoading(false);
    setError(null);
  }, []);

  return {
    isActive,
    cookingState,
    currentRecipe,
    currentStep,
    currentServings,
    completedSteps,
    userIngredients,
    recipes,
    timers,
    isLoading,
    error,
    startCookingMode,
    exitCookingMode,
    setIngredients,
    selectRecipe,
    confirmStep,
    skipStep,
    finishCooking,
    cancelTimer,
    updateFromBackend,
    stopCookingMode,
  };
}
