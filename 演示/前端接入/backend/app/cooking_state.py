# -*- coding: utf-8 -*-
"""
★ 演示文件 - StepGuide 游标状态机

模块对应：拿着任务清单一步一步带（游标 + 归档 + 安全递进）
完整实现：backend/app/models/cooking_state.py

本文件仅保留讲解所需的「状态枚举 + 游标推进」核心代码，
剥离 pydantic/数据库依赖，用于演示/讲解，不可直接执行。
"""

from enum import Enum
from typing import List, Optional

from recipe_service import Recipe, RecipeStep  # ★ 复用「获取菜谱」模块的结构


class CookingState(str, Enum):
    """做饭状态枚举（六阶段）"""
    IDLE = "idle"                          # 未开始做饭
    COLLECTING_INGREDIENTS = "collecting"  # 收集食材中
    RECOMMENDING = "recommending"          # 推荐菜谱中
    COOKING = "cooking"                    # 做饭中
    STEP_WAITING = "step_waiting"          # 等待用户确认
    FINISHED = "finished"                  # 做饭完成


class CookingStatus:
    """
    做饭状态模型（StepGuide 游标状态机）

    游标 current_step 指到哪一步，就教哪一步；
    completed_steps 归档已完成工序；state 表示流程阶段。
    """
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.state: CookingState = CookingState.IDLE
        self.recipe_details: Optional[Recipe] = None   # ★ 任务列表（来自菜谱.steps）
        self.current_step: int = 0                     # ★ 游标（1-based，0=未开始）
        self.completed_steps: List[int] = []           # ★ 归档区

    def start_cooking(self, recipe: Recipe) -> bool:
        """开始做菜：绑定菜谱（任务列表）并把游标拨到第1步"""
        if self.state not in (CookingState.IDLE, CookingState.FINISHED):
            return False
        self.recipe_details = recipe      # ★ 接住 get_recipe 返回的整道菜
        self.state = CookingState.COOKING
        self.current_step = 1             # 游标：从第1个任务开始
        self.completed_steps = []
        return True

    def confirm_step(self, step: int) -> bool:
        """
        用户说"好了" → 归档当前工序 + 安全驱动步骤递进

        1) 校验：只能确认当前步或之前的步骤，防乱确认
        2) 归档：该步骤记入 completed_steps（自动归档）
        3) 递进：确认的是当前步 → 有下一步则进入等待确认，
           没有下一步则整道菜完工（边界校验，防卡死/防越界）
        """
        if not self.recipe_details or not self.recipe_details.steps:
            return False
        if step < 1 or step > len(self.recipe_details.steps):
            return False                  # 边界：步骤序号越界直接拒绝
        if step > self.current_step:
            return False                  # 边界：不能确认还没教到的步骤

        if step not in self.completed_steps:         # 去重归档
            self.completed_steps.append(step)         # ✓ 自动归档当前工序
            self.completed_steps.sort()

        if step == self.current_step:                 # 确认的是当前步
            if self.current_step >= len(self.recipe_details.steps):
                self.state = CookingState.FINISHED    # 没有下一步 → 完工
            else:
                self.state = CookingState.STEP_WAITING  # 还有下一步 → 安全等待
        return True

    def next_step(self) -> Optional[RecipeStep]:
        """
        游标推进 → 返回下一步操作

        确认"好了"后由 confirm_step 落到 STEP_WAITING，
        下一步动作由本方法从等待状态拉回来：游标 +1、状态切回做饭中。
        """
        if not self.recipe_details or not self.recipe_details.steps:
            return None
        if self.current_step >= len(self.recipe_details.steps):
            return None                   # 边界：已无下一步
        self.current_step += 1            # 游标挪一格
        self.state = CookingState.COOKING # 状态回到做饭中
        return self.recipe_details.steps[self.current_step - 1]  # 返回下一步

    def get_current_step(self) -> Optional[RecipeStep]:
        """游标锁定：现在要教的任务（对应 StepGuide.get_current_step）"""
        if 1 <= self.current_step <= len(self.recipe_details.steps or []):
            return self.recipe_details.steps[self.current_step - 1]
        return None


# ============================
# 演示用法（对应讲稿一条线串完）
# ============================
if __name__ == "__main__":
    from recipe_service import get_recipe_service

    # ① 获取当前用户菜谱
    recipe = get_recipe_service().get_recipe("番茄炒蛋")
    # ② 菜谱落地 → 建立任务列表（游标 = 1，归档 = 空）
    guide = CookingStatus()
    guide.start_cooking(recipe)

    # ③ 一步一步带：做一步 → 归档一步 → 游标挪一步
    print("任务列表:", [step.content for step in guide.recipe_details.steps])
    for _ in range(len(guide.recipe_details.steps)):
        print("当前任务:", guide.get_current_step().content)
        guide.confirm_step(guide.current_step)   # 用户说"好了"
        guide.next_step()                        # 进入下一步
    print("全部完成:", guide.state, "| 已归档:", guide.completed_steps)