import { RecipeCardProps } from "./types";
import { ChefHat, Clock, Flame } from "lucide-react";

/**
 * 菜谱卡片组件
 * 显示菜谱的基本信息，包括名称、分类、难度和匹配的食材
 */
export function RecipeCard({ recipe, onSelect, theme }: RecipeCardProps) {
  // 根据难度返回对应的颜色
  const getDifficultyColor = (difficulty: string): string => {
    switch (difficulty.toLowerCase()) {
      case "简单":
        return theme.success;
      case "中等":
        return theme.secondary;
      case "困难":
        return theme.error;
      default:
        return theme.textSecondary;
    }
  };

  // 获取难度标签背景色
  const getDifficultyBg = (difficulty: string): string => {
    switch (difficulty.toLowerCase()) {
      case "简单":
        return theme.success + "20";
      case "中等":
        return theme.secondary + "20";
      case "困难":
        return theme.error + "20";
      default:
        return theme.bgTertiary;
    }
  };

  return (
    <div
      onClick={onSelect}
      style={{
        background: theme.bgSecondary,
        borderRadius: "16px",
        border: `1px solid ${theme.borderLight}`,
        padding: "16px",
        cursor: "pointer",
        transition: "all 0.2s ease",
        boxShadow: `0 4px 12px rgba(0, 0, 0, 0.08)`,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.boxShadow = `0 8px 24px rgba(0, 0, 0, 0.12)`;
        e.currentTarget.style.borderColor = theme.primary;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
        e.currentTarget.style.boxShadow = `0 4px 12px rgba(0, 0, 0, 0.08)`;
        e.currentTarget.style.borderColor = theme.borderLight;
      }}
    >
      {/* 菜谱名称和分类 */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          marginBottom: "6px"
        }}>
          <ChefHat size={18} color={theme.primary} strokeWidth={1.5} />
          <span style={{
            fontSize: "16px",
            fontWeight: 600,
            color: theme.text,
          }}>
            {recipe.name}
          </span>
        </div>
        <span style={{
          fontSize: "12px",
          color: theme.textMuted,
          background: theme.bgTertiary,
          padding: "2px 8px",
          borderRadius: "6px",
        }}>
          {recipe.category}
        </span>
      </div>

      {/* 难度和匹配食材 */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: "12px"
      }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "6px"
        }}>
          <Flame size={14} color={getDifficultyColor(recipe.difficulty || "")} strokeWidth={1.5} />
          <span style={{
            fontSize: "12px",
            color: getDifficultyColor(recipe.difficulty || ""),
            background: getDifficultyBg(recipe.difficulty || ""),
            padding: "2px 8px",
            borderRadius: "6px",
            fontWeight: 500,
          }}>
            {recipe.difficulty || "未知"}
          </span>
        </div>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "6px"
        }}>
          <Clock size={14} color={theme.textMuted} strokeWidth={1.5} />
          <span style={{
            fontSize: "12px",
            color: theme.textMuted,
          }}>
            {recipe.matched_ingredients?.length || 0} 个匹配食材
          </span>
        </div>
      </div>

      {/* 匹配的食材标签 */}
      {recipe.matched_ingredients && recipe.matched_ingredients.length > 0 && (
        <div style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "6px"
        }}>
          {recipe.matched_ingredients.slice(0, 4).map((ingredient, index) => (
            <span
              key={index}
              style={{
                fontSize: "11px",
                color: theme.primary,
                background: theme.primaryLight,
                padding: "3px 8px",
                borderRadius: "4px",
              }}
            >
              {ingredient}
            </span>
          ))}
          {recipe.matched_ingredients.length > 4 && (
            <span
              style={{
                fontSize: "11px",
                color: theme.textMuted,
                background: theme.bgTertiary,
                padding: "3px 8px",
                borderRadius: "4px",
              }}
            >
              +{recipe.matched_ingredients.length - 4}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
