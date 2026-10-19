# -*- coding: utf-8 -*-
"""
★ 演示文件 - 获取菜谱 → 建立任务列表（串联入口）

模块对应：识别用户当前要做的菜 → 菜谱落地会话状态 → 交棒给游标状态机
完整实现：backend/app/ws_handlers/cooking.py

本文件仅保留讲解所需的「主入口分流 + 选中菜谱 + 落地任务列表」，用于演示/讲解。
"""

from recipe_service import get_recipe_service
from cooking_state import CookingState, CookingStatus

# 用户"一步一步/手把手"等引导词 → 直接进入步骤式教学
COOKING_START_GUIDE_KEYWORDS = ("步骤式", "一步一步", "一步步", "手把手")


class SessionState:
    """会话状态（演示版：只留做饭模式相关字段）"""
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.cooking_enabled = False                 # 是否启用做饭模式
        self.current_recipe = None                   # 当前菜谱（任务列表本体）
        self.current_step = 0                        # 游标（1-based）
        self.completed_steps = []                    # 已完成任务
        self.current_servings = None                 # 份数
        self.awaiting_servings = False               # 是否在等用户确认份数
        self.cooking_state = "IDLE"                  # 状态机阶段
        # 真实系统中还有 cooking_state 模型实例、上下文引擎、浮窗推送等


def control_select_recipe(state: SessionState, recipe_name: str) -> dict:
    """
    选中菜谱 → 建立任务列表（核心）

    用户说出菜名 → 从菜谱库拿到整道菜 → 落地到会话状态：
      current_recipe = 菜谱（steps 即任务清单）
      current_step   = 1   （游标从第1个任务开始）
      completed_steps= []  （归档区清零）
    然后进入"等待确认份数"，份数确认后状态机开始逐步骤教学。
    返回推送给前端浮窗的状态（cooking_status）。
    """
    recipe_service = get_recipe_service()
    recipe = recipe_service.get_recipe(recipe_name)   # ① 获取当前用户菜谱
    if recipe is None:
        return {"error": f"没有找到菜谱: {recipe_name}"}

    # ② 建立任务列表 + 落地会话状态
    state.current_recipe = recipe       # 菜谱落地（recipe.steps = 任务列表）
    state.current_step = 1              # 游标：从第1个任务开始
    state.completed_steps = []          # 归档区：初始为空
    state.current_servings = None
    state.awaiting_servings = True      # 等用户说"做几人份"
    state.cooking_state = "AWAITING_SERVINGS"

    # ③ 份数确认后 → 交棒给游标状态机（cooking_state.CookingStatus）
    task_list = recipe.steps            # 任务列表（直接取自菜谱）
    guide = CookingStatus()             # StepGuide 游标状态机
    guide.start_cooking(recipe)         # 游标拨到第1步，开始教学

    # ④ 浮窗同步（真实系统：await send_cooking_status(ws, state, ...)）
    return {
        "state": state.cooking_state,
        "recipe": state.current_recipe.name,
        "step": state.current_step,
        "completed_steps": state.completed_steps,
        "task_list": [s.content for s in task_list],   # 任务列表（可视化）
    }


def handle_cooking_user_text(state: SessionState, text: str) -> dict | None:
    """
    主入口分流（演示版：只保留"识别用户当前要做的菜"两条路）

    1) 明确引导词（一步一步/手把手…）→ 从本句话里解析菜名 → 直接建清单；
    2) 普通做饭意图 → 收集信息后由 AI 询问，再进步骤式教学。
    真实系统还有关键词分流、远程 LLM 兜底生成菜谱等，此处省略。
    """
    text = text.strip()
    if not text:
        return None

    wants_step_guide = any(kw in text for kw in COOKING_START_GUIDE_KEYWORDS)

    # 从用户话里抠菜名：剥掉"教我做/一步一步/手把手"等口水话
    candidate = text
    for prefix in ("一步一步教我做", "手把手教我做", "教我做", "我要做", "我想做"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):].strip()
            break

    # 识别到明确的菜 → 直接选菜、建清单
    if candidate and wants_step_guide:
        state.cooking_enabled = True
        return control_select_recipe(state, candidate)

    # 未明确引导词 → 保持普通问答（由大模型收集信息后再询问）
    return None


# ============================
# 演示用法
# ============================
if __name__ == "__main__":
    state = SessionState(session_id="demo-001")

    result = handle_cooking_user_text(state, "一步一步教我做番茄炒蛋")
    print("=== 获取菜谱 → 建立任务列表 ===")
    if result:
        print(f"状态: {result['state']} | 菜谱: {result['recipe']}")
        for i, task in enumerate(result["task_list"], 1):
            print(f"  任务{i}: {task}")