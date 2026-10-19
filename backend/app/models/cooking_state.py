# -*- coding: utf-8 -*-
"""
做饭状态管理模型
支持做饭流程的状态转换和数据持久化
"""

import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# 配置路径以支持从 backend 目录导入
# 项目根目录是 backend 的父目录
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 导入菜谱相关模型
from app.services.recipe_service import Recipe, RecipeStep


class CookingState(str, Enum):
    """做饭状态枚举"""
    IDLE = "idle"                          # 未开始做饭
    COLLECTING_INGREDIENTS = "collecting"  # 收集食材中
    RECOMMENDING = "recommending"          # 推荐菜谱中
    COOKING = "cooking"                     # 做饭中
    STEP_WAITING = "step_waiting"          # 等待用户确认
    FINISHED = "finished"                  # 做饭完成


class CookingStatus(BaseModel):
    """
    做饭状态模型

    用于管理做饭引导流程的状态、进度和相关信息
    """
    # 当前状态
    state: CookingState = Field(default=CookingState.IDLE, description="当前状态")

    # 会话信息
    session_id: str = Field(..., description="会话ID")
    user_id: Optional[str] = Field(None, description="用户ID")

    # 菜谱信息
    selected_recipe: Optional[str] = Field(None, description="选中的菜谱名称")
    recipe_details: Optional[Recipe] = Field(None, description="菜谱详情")

    # 步骤进度
    current_step: int = Field(default=0, description="当前步骤（1-based，0表示未开始）")
    completed_steps: List[int] = Field(default_factory=list, description="已完成的步骤列表")

    # 用户食材
    user_ingredients: List[str] = Field(default_factory=list, description="用户提供的食材")

    # 时间戳
    started_at: Optional[datetime] = Field(None, description="开始时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

    def _update_timestamp(self) -> None:
        """更新修订时间戳"""
        self.updated_at = datetime.now()

    def start_cooking(self, recipe_name: str) -> bool:
        """
        开始做菜

        Args:
            recipe_name: 菜谱名称

        Returns:
            是否成功开始
        """
        if self.state not in [CookingState.IDLE, CookingState.COLLECTING_INGREDIENTS,
                              CookingState.RECOMMENDING, CookingState.FINISHED]:
            return False

        self.selected_recipe = recipe_name
        self.state = CookingState.COOKING
        self.current_step = 1
        self.completed_steps = []
        self.started_at = datetime.now()
        self._update_timestamp()
        return True

    def next_step(self) -> Optional[RecipeStep]:
        """
        进入下一步

        Returns:
            下一步的 RecipeStep，如果已无下一步则返回 None
        """
        if not self.recipe_details or not self.recipe_details.steps:
            return None

        # 检查是否还有下一步
        if self.current_step >= len(self.recipe_details.steps):
            return None

        self.current_step += 1
        self.state = CookingState.COOKING
        self._update_timestamp()

        return self.recipe_details.steps[self.current_step - 1]

    def confirm_step(self, step: int) -> bool:
        """
        确认某步骤完成

        Args:
            step: 步骤序号（1-based）

        Returns:
            是否成功确认
        """
        if not self.recipe_details or not self.recipe_details.steps:
            return False

        # 验证步骤序号有效
        if step < 1 or step > len(self.recipe_details.steps):
            return False

        # 只能确认当前步骤或之前的步骤
        if step > self.current_step:
            return False

        # 添加到已完成步骤（避免重复）
        if step not in self.completed_steps:
            self.completed_steps.append(step)
            self.completed_steps.sort()

        # 如果确认的是当前步骤，自动进入下一步
        if step == self.current_step:
            if self.current_step >= len(self.recipe_details.steps):
                # 所有步骤完成
                self.state = CookingState.FINISHED
            else:
                # 进入等待用户确认状态
                self.state = CookingState.STEP_WAITING

        self._update_timestamp()
        return True

    def skip_step(self, step: int) -> bool:
        """
        跳过某步骤

        Args:
            step: 步骤序号（1-based）

        Returns:
            是否成功跳过
        """
        if not self.recipe_details or not self.recipe_details.steps:
            return False

        # 验证步骤序号有效
        if step < 1 or step > len(self.recipe_details.steps):
            return False

        # 只能跳过当前步骤
        if step != self.current_step:
            return False

        # 添加到已完成步骤（标记为跳过）
        if step not in self.completed_steps:
            self.completed_steps.append(step)
            self.completed_steps.sort()

        # 进入下一步
        if self.current_step >= len(self.recipe_details.steps):
            # 所有步骤完成
            self.state = CookingState.FINISHED
        else:
            self.current_step += 1
            self.state = CookingState.STEP_WAITING

        self._update_timestamp()
        return True

    def exit_cooking(self) -> None:
        """退出做饭模式，重置状态"""
        self.state = CookingState.IDLE
        self.selected_recipe = None
        self.recipe_details = None
        self.current_step = 0
        self.completed_steps = []
        self.user_ingredients = []
        self.started_at = None
        self._update_timestamp()

    def to_context_dict(self) -> Dict:
        """
        转换为上下文字典，用于 prompt 构建

        Returns:
            包含做饭状态相关信息的字典
        """
        context = {
            "state": self.state.value if isinstance(self.state, CookingState) else self.state,
            "session_id": self.session_id,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
        }

        # 添加用户信息
        if self.user_id:
            context["user_id"] = self.user_id

        # 添加用户食材
        if self.user_ingredients:
            context["user_ingredients"] = self.user_ingredients

        # 添加菜谱信息
        if self.selected_recipe:
            context["selected_recipe"] = self.selected_recipe

        if self.recipe_details:
            context["recipe_name"] = self.recipe_details.name
            context["total_steps"] = len(self.recipe_details.steps)

            # 添加当前步骤详情
            if 1 <= self.current_step <= len(self.recipe_details.steps):
                step = self.recipe_details.steps[self.current_step - 1]
                context["current_step_info"] = {
                    "step_number": step.step_number,
                    "title": step.title,
                    "content": step.content,
                }

            # 添加已完成步骤的摘要
            if self.completed_steps:
                completed_info = []
                for step_num in self.completed_steps:
                    if 1 <= step_num <= len(self.recipe_details.steps):
                        step = self.recipe_details.steps[step_num - 1]
                        completed_info.append({
                            "step_number": step.step_number,
                            "title": step.title or f"步骤{step.step_number}"
                        })
                context["completed_steps_info"] = completed_info

            # 添加剩余步骤数量
            remaining = len(self.recipe_details.steps) - len(self.completed_steps)
            context["remaining_steps"] = remaining

        # 添加时间信息
        if self.started_at:
            context["started_at"] = self.started_at.isoformat()
        if self.updated_at:
            context["updated_at"] = self.updated_at.isoformat()

        return context
