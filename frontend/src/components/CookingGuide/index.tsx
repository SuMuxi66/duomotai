import { CookingGuideProps, CookingState } from "./types";
import { StepGuide } from "./StepGuide";
import { CookingStatus } from "./CookingStatus";
import {
  IdleView,
  CollectingIngredientsView,
  RecommendingView,
  PreparationView,
  FinishedView,
} from "./CookingControls";
import { radius, zIndex } from "@/styles";

/**
 * 做饭引导主组件
 * 负责管理做饭的整个流程：收集食材 -> 推荐菜谱 -> 步骤引导 -> 完成
 */
export function CookingGuide({
  isActive,
  cookingState,
  currentRecipe,
  currentStep,
  currentServings,
  userIngredients,
  recipes,
  timers,
  onIngredientsSubmit,
  onRecipeSelect,
  onStepConfirm,
  onSkipStep,
  onCancelTimer,
  onExit,
  theme,
}: CookingGuideProps) {
  if (!isActive) {
    return null;
  }

  // 渲染步骤引导状态
  const renderCooking = () => {
    if (!currentRecipe?.steps?.length) {
      return null;
    }
    const normalizedStep = Math.min(Math.max(currentStep || 1, 1), currentRecipe.steps.length);
    const currentStepData = currentRecipe.steps[normalizedStep - 1];
    if (!currentStepData) {
      return null;
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div style={{ padding: "16px 20px" }}>
          <CookingStatus
            recipeName={currentRecipe.name}
            currentStep={normalizedStep}
            totalSteps={currentRecipe.steps.length}
            timers={timers}
            onCancelTimer={onCancelTimer}
            onExit={onExit}
            theme={theme}
          />
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <StepGuide
            step={currentStepData}
            totalSteps={currentRecipe.steps.length}
            onConfirm={onStepConfirm}
            onSkip={onSkipStep}
            theme={theme}
          />
        </div>
      </div>
    );
  };

  // 根据状态渲染不同界面
  const renderContent = () => {
    switch (cookingState) {
      case CookingState.IDLE:
        return <IdleView theme={theme} onStart={() => onIngredientsSubmit("")} />;
      case CookingState.COLLECTING_INGREDIENTS:
        return (
          <CollectingIngredientsView
            theme={theme}
            userIngredients={userIngredients}
            onSubmit={onIngredientsSubmit}
            onExit={onExit}
          />
        );
      case CookingState.AWAITING_SERVINGS:
        return (
          <PreparationView
            theme={theme}
            currentRecipe={currentRecipe}
            currentServings={currentServings}
            onExit={onExit}
          />
        );
      case CookingState.RECOMMENDING:
        return (
          <RecommendingView
            theme={theme}
            cookingState={cookingState}
            userIngredients={userIngredients}
            recipes={recipes}
            onRecipeSelect={onRecipeSelect}
            onBack={() => onIngredientsSubmit("")}
          />
        );
      case CookingState.COOKING:
      case CookingState.STEP_WAITING:
        return renderCooking();
      case CookingState.FINISHED:
        return <FinishedView theme={theme} currentRecipe={currentRecipe} onExit={onExit} />;
      default:
        return <IdleView theme={theme} onStart={() => onIngredientsSubmit("")} />;
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: "90%",
        maxWidth: "480px",
        height: "80%",
        maxHeight: "600px",
        background: theme.bg,
        borderRadius: radius.xl,
        border: `1px solid ${theme.borderLight}`,
        boxShadow: `0 24px 80px rgba(0, 0, 0, 0.3)`,
        zIndex: zIndex.modal,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        animation: "modalIn 0.25s ease-out",
      }}
    >
      <style>{`
        @keyframes modalIn {
          from { opacity: 0; transform: translate(-50%, -50%) scale(0.95); }
          to { opacity: 1; transform: translate(-50%, -50%) scale(1); }
        }
      `}</style>
      {/* 头部装饰线 */}
      <div style={{
        height: "3px",
        background: `linear-gradient(90deg, ${theme.primary}, ${theme.secondary})`,
      }} />
      {/* 内容区域 */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        {renderContent()}
      </div>
    </div>
  );
}
