import { useState } from "react";
import { ChefHat, Search, Utensils, CheckCircle, Loader2, X } from "lucide-react";
import { Button, Badge } from "@/components/ui";
import { CookingHeader } from "./CookingHeader";
import { RecipeCard } from "./RecipeCard";
import { CookingState, type Recipe, type RecipeSearchResult } from "./types";
import type { Theme } from "@/lib/theme";
import { radius, spacing } from "@/styles";

// ─── IdleView ─────────────────────────────────────────────
interface IdleViewProps {
  theme: Theme;
  onStart: () => void;
}

export function IdleView({ theme, onStart }: IdleViewProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", padding: 40, textAlign: "center" }}>
      <div style={{ width: 80, height: 80, borderRadius: "50%", background: theme.primaryLight, display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 24 }}>
        <ChefHat size={40} color={theme.primary} strokeWidth={1.5} />
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 600, color: theme.text, marginBottom: 12, margin: 0 }}>做饭引导</h2>
      <p style={{ fontSize: 14, color: theme.textMuted, marginBottom: 32, maxWidth: 280, margin: 0 }}>
        告诉我你有的食材，我来为你推荐美味菜谱
      </p>
      <Button variant="primary" size="lg" icon={<Utensils size={18} strokeWidth={1.5} />} onClick={onStart} theme={theme}>
        开始做饭
      </Button>
    </div>
  );
}

// ─── CollectingIngredientsView ────────────────────────────
interface CollectingIngredientsViewProps {
  theme: Theme;
  userIngredients: string[];
  onSubmit: (ingredients: string) => void;
  onExit: () => void;
}

export function CollectingIngredientsView({ theme, userIngredients, onSubmit, onExit }: CollectingIngredientsViewProps) {
  const [input, setInput] = useState("");
  const handleSubmit = () => { if (input.trim()) onSubmit(input.trim()); };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: spacing.xl }}>
      <CookingHeader
        icon={<Search size={20} color={theme.primary} strokeWidth={1.5} />}
        title="收集食材"
        subtitle="输入你现有的食材，用逗号分隔"
        onBack={onExit}
        theme={theme}
      />
      {userIngredients.length > 0 && (
        <div style={{ marginBottom: spacing.lg }}>
          <p style={{ fontSize: 12, color: theme.textMuted, marginBottom: spacing.sm, margin: 0 }}>已添加的食材：</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {userIngredients.map((ing, i) => (
              <Badge key={i} variant="info" size="sm" theme={theme}>
                {ing}
                <X size={12} strokeWidth={2} style={{ cursor: "pointer" }} />
              </Badge>
            ))}
          </div>
        </div>
      )}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <div style={{ background: theme.inputBg, border: `1px solid ${theme.inputBorder}`, borderRadius: radius.md, padding: "14px 16px", marginBottom: spacing.md, display: "flex", alignItems: "center", gap: spacing.md }}>
          <Utensils size={18} color={theme.textMuted} strokeWidth={1.5} />
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
            placeholder="例如：鸡蛋、番茄、葱花"
            style={{ flex: 1, border: "none", outline: "none", background: "transparent", fontSize: 14, color: theme.inputText }}
          />
        </div>
        <div style={{ marginBottom: spacing.lg }}>
          <p style={{ fontSize: 11, color: theme.textMuted, marginBottom: spacing.sm, margin: 0 }}>快捷添加：</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {["鸡蛋", "番茄", "葱", "蒜", "盐", "油"].map((item) => (
              <button
                key={item}
                onClick={() => setInput(prev => prev ? `${prev}, ${item}` : item)}
                style={{ fontSize: 11, color: theme.textSecondary, background: theme.bgTertiary, border: `1px solid ${theme.borderLight}`, padding: "4px 10px", borderRadius: radius.sm, cursor: "pointer" }}
              >
                + {item}
              </button>
            ))}
          </div>
        </div>
        <Button variant="primary" fullWidth icon={<Search size={18} strokeWidth={1.5} />} disabled={!input.trim()} onClick={handleSubmit} theme={theme}>
          搜索菜谱
        </Button>
      </div>
    </div>
  );
}

// ─── RecommendingView ─────────────────────────────────────
interface RecommendingViewProps {
  theme: Theme;
  cookingState: CookingState;
  userIngredients: string[];
  recipes: RecipeSearchResult[];
  onRecipeSelect: (name: string) => void;
  onBack: () => void;
}

export function RecommendingView({ theme, cookingState, userIngredients, recipes, onRecipeSelect, onBack }: RecommendingViewProps) {
  const isLoading = cookingState === CookingState.RECOMMENDING;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: spacing.xl }}>
      <CookingHeader
        icon={<ChefHat size={20} color={theme.secondary} strokeWidth={1.5} />}
        title="推荐菜谱"
        subtitle="根据你的食材推荐的菜谱"
        onBack={onBack}
        theme={theme}
        iconBg={theme.secondaryLight}
        marginBottom={20}
      />
      {userIngredients.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 20 }}>
          {userIngredients.map((ing, i) => (
            <span key={i} style={{ fontSize: 11, color: theme.textSecondary, background: theme.bgTertiary, padding: "3px 8px", borderRadius: 4 }}>
              {ing}
            </span>
          ))}
        </div>
      )}
      {isLoading && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
          <Loader2 size={40} color={theme.primary} strokeWidth={1.5} style={{ animation: "spin 1s linear infinite" }} />
          <p style={{ fontSize: 14, color: theme.textMuted, margin: 0 }}>正在搜索菜谱...</p>
          <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
        </div>
      )}
      {!isLoading && recipes.length > 0 && (
        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: spacing.md }}>
          {recipes.map((recipe, i) => (
            <RecipeCard key={i} recipe={recipe} onSelect={() => onRecipeSelect(recipe.name)} theme={theme} />
          ))}
        </div>
      )}
      {!isLoading && recipes.length === 0 && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: spacing.md }}>
          <Utensils size={40} color={theme.textMuted} strokeWidth={1} />
          <p style={{ fontSize: 14, color: theme.textMuted, margin: 0 }}>暂无匹配的菜谱，请尝试添加其他食材</p>
        </div>
      )}
    </div>
  );
}

// ─── PreparationView ──────────────────────────────────────
interface PreparationViewProps {
  theme: Theme;
  currentRecipe: Recipe | null;
  currentServings: number | null;
  onExit: () => void;
}

export function PreparationView({ theme, currentRecipe, currentServings, onExit }: PreparationViewProps) {
  const prepItems = currentRecipe?.prep_items ?? currentRecipe?.ingredients ?? [];
  const perServingLines = currentRecipe?.per_serving_lines ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: spacing.xl, gap: spacing.lg }}>
      <CookingHeader
        icon={<ChefHat size={20} color={theme.primary} strokeWidth={1.5} />}
        title="准备原料和工具"
        subtitle="先把文档里的准备项备齐，再告诉我几人份"
        onBack={onExit}
        theme={theme}
        marginBottom={0}
      />
      <div style={{ background: theme.bgSecondary, border: `1px solid ${theme.borderLight}`, borderRadius: 18, padding: spacing.lg, display: "flex", flexDirection: "column", gap: spacing.md, overflow: "auto" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>需要准备：</div>
          {prepItems.map((item, i) => (
            <div key={i} style={{ fontSize: 13, color: theme.text, background: theme.bgTertiary, borderRadius: 10, padding: "8px 10px" }}>
              {item}
            </div>
          ))}
        </div>
        {currentRecipe?.servings_note && (
          <div style={{ fontSize: 13, color: theme.textSecondary, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
            {currentRecipe.servings_note}
          </div>
        )}
        {perServingLines.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>每份用量参考：</div>
            {perServingLines.slice(0, 6).map((line, i) => (
              <div key={i} style={{ fontSize: 13, color: theme.textSecondary, lineHeight: 1.6, background: theme.bgTertiary, borderRadius: 10, padding: "8px 10px" }}>
                {line}
              </div>
            ))}
          </div>
        )}
        <div style={{ fontSize: 13, color: theme.primary, padding: "10px 12px", borderRadius: radius.md, background: theme.primaryLight }}>
          请在聊天里告诉我你准备做几人份，例如 2 人份或三个人。
          {currentServings ? ` 当前已确认：${currentServings} 人份。` : ""}
        </div>
      </div>
    </div>
  );
}

// ─── FinishedView ─────────────────────────────────────────
interface FinishedViewProps {
  theme: Theme;
  currentRecipe: Recipe | null;
  onExit: () => void;
}

export function FinishedView({ theme, currentRecipe, onExit }: FinishedViewProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", padding: 40, textAlign: "center" }}>
      <div style={{ width: 80, height: 80, borderRadius: "50%", background: theme.success + "20", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 24 }}>
        <CheckCircle size={40} color={theme.success} strokeWidth={1.5} />
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 600, color: theme.text, marginBottom: 12, margin: 0 }}>恭喜完成！</h2>
      <p style={{ fontSize: 14, color: theme.textMuted, marginBottom: 12, maxWidth: 280, margin: 0 }}>
        {currentRecipe?.name} 已完成，快尝尝你的手艺吧！
      </p>
      <p style={{ fontSize: 13, color: theme.textSecondary, marginBottom: 32, margin: 0 }}>
        共 {currentRecipe?.steps?.length || 0} 个步骤
      </p>
      <Button variant="primary" size="lg" icon={<ChefHat size={18} strokeWidth={1.5} />} onClick={onExit} theme={theme}>
        开始新菜谱
      </Button>
    </div>
  );
}
