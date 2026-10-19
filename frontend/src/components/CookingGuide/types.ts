import { Theme } from "../../lib/theme";

// 做饭状态枚举
export enum CookingState {
  IDLE = "IDLE",
  COLLECTING_INGREDIENTS = "COLLECTING_INGREDIENTS",
  RECOMMENDING = "RECOMMENDING",
  AWAITING_SERVINGS = "AWAITING_SERVINGS",
  COOKING = "COOKING",
  STEP_WAITING = "STEP_WAITING",
  FINISHED = "FINISHED"
}

// 步骤数据
export interface RecipeStep {
  order: number;
  title?: string;
  content: string;
  tip?: string | null;
}

// 菜谱数据
export interface Recipe {
  name: string;
  category?: string;
  difficulty?: string;
  description?: string;
  ingredients?: string[];
  prep_items?: string[];
  servings_note?: string | null;
  per_serving_lines?: string[];
  steps?: RecipeStep[];
  matched_ingredients?: string[];
}

// 搜索返回的菜谱结果
export interface RecipeSearchResult {
  name: string;
  category?: string;
  difficulty?: string;
  matched_ingredients?: string[];
}

// 食材输入组件 Props
export interface IngredientInputProps {
  theme: Theme;
  onSubmit: (ingredients: string) => void;
  isLoading?: boolean;
}

// 菜谱卡片组件 Props
export interface RecipeCardProps {
  recipe: RecipeSearchResult;
  onSelect: () => void;
  theme: Theme;
}

// 步骤引导组件 Props
export interface StepGuideProps {
  step: RecipeStep;
  totalSteps: number;
  onConfirm: () => void;
  onSkip: () => void;
  theme: Theme;
}

// 做饭状态栏组件 Props
export interface CookingStatusProps {
  recipeName: string;
  currentStep: number;
  totalSteps: number;
  timers: CookingTimer[];
  onCancelTimer: () => void;
  onExit: () => void;
  theme: Theme;
}

// 倒计时数据
export interface CookingTimer {
  id: string;
  description: string;
  total_seconds: number;
  end_time: number;  // Unix timestamp (seconds)
}

// 做饭引导主组件 Props
export interface CookingGuideProps {
  isActive: boolean;
  cookingState: CookingState;
  currentRecipe: Recipe | null;
  currentStep: number;
  currentServings: number | null;
  userIngredients: string[];
  recipes: RecipeSearchResult[];
  timers: CookingTimer[];
  onIngredientsSubmit: (ingredients: string) => void;
  onRecipeSelect: (recipeName: string) => void;
  onStepConfirm: () => void;
  onSkipStep: () => void;
  onCancelTimer: () => void;
  onExit: () => void;
  theme: Theme;
}
