# -*- coding: utf-8 -*-
"""
★ 演示文件 - 获取当前菜谱

模块对应：获取当前用户菜谱（用户说菜名 → 从菜谱库调出整道菜）
完整实现：backend/app/services/recipe_service.py

本文件仅保留讲解所需的「菜谱检索 + 菜谱落地」核心代码，
用于演示/讲解，不可直接执行。
"""

from typing import List, Optional


class RecipeStep:
    """菜谱的单个步骤（任务列表里的一条任务）"""
    def __init__(self, step_number: int, content: str, title: Optional[str] = None):
        self.step_number = step_number   # 步骤序号
        self.title = title               # 步骤标题
        self.content = content           # 步骤内容


class Recipe:
    """菜谱详情（结构化数据：食材 / 份量 / 步骤 / 小贴士）"""
    def __init__(self, name: str, steps: List[RecipeStep]):
        self.name = name                 # 菜谱名称
        self.steps = steps               # ★ 步骤列表 = 任务列表本体
        self.ingredients: List[str] = []     # 食材列表
        self.servings_note: Optional[str] = None  # 份量说明


class RecipeService:
    """菜谱服务（演示简化版：预置一道菜，完整版启动时扫描 HowToCook 目录解析）"""

    def __init__(self):
        # 预置菜谱库（真实系统从 buzhou/HowToCook/dishes/*.md 解析而来）
        self._recipes = {
            "番茄炒蛋": Recipe(
                name="番茄炒蛋",
                steps=[
                    RecipeStep(1, "番茄洗净切块，鸡蛋打散备用"),
                    RecipeStep(2, "热锅倒油，倒入蛋液炒至凝固盛出"),
                    RecipeStep(3, "锅内放番茄翻炒出汁，倒回鸡蛋"),
                    RecipeStep(4, "加盐调味，翻炒均匀出锅"),
                ],
            )
        }

    def search_by_name(self, query: str, limit: int = 8) -> List[Recipe]:
        """按菜名模糊搜索（用户说话里的菜名 → 候选菜谱）"""
        query = (query or "").strip().lower()
        results = [
            r for name, r in self._recipes.items()
            if not query or query in name.lower() or name.lower() in query
        ]
        return results[:limit]

    def get_recipe(self, name: str) -> Optional[Recipe]:
        """
        获取菜谱详情（核心）

        用户说「教我做番茄炒蛋」→ 菜名 → 从菜谱库调出整道菜
        """
        return self._recipes.get(name)


# 全局单例（真实系统：get_recipe_service()）
def get_recipe_service() -> RecipeService:
    global _recipe_service
    if _recipe_service is None:
        _recipe_service = RecipeService()
    return _recipe_service


_recipe_service: Optional[RecipeService] = None