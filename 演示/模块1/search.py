"""
【实操动作2：双维度智能检索功能】实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第一模块·选手2实操手1：

为适配口语化交互、解决语音识别误差和食材叫法不统一导致的检索失败问题：
- 维度一：食材双向模糊匹配 + 同义词归一化（俗称/简称 → 标准名）+ 评分降序排序
- 维度二：菜名字串容错搜索（输入核心关键词即可命中，适配口语化不完整输入）
- 系统自动统计匹配食材数量、计算匹配分数，优先推送最贴合用户需求的菜谱

用法：
  python search.py          # 双维度检索完整流程演示
  python -i search.py       # 演示后进入交互模式，现场敲代码验证（见文末标注）
"""

import sys
from typing import List, Optional
from dataclasses import dataclass

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')


# ==================== 数据结构定义 ====================

@dataclass
class Recipe:
    """【数据结构】菜谱实体类"""
    name: str
    ingredients: List[str]
    category: Optional[str] = None


@dataclass
class RecipeMatch:
    """【数据结构】匹配结果实体类 - 记录匹配到的菜谱、命中的食材与匹配分数"""
    recipe: Recipe
    matched_ingredients: List[str]
    match_score: int


# ==================== 双维度智能检索器 ====================

class RecipeMatcher:
    """
    双维度智能检索器

    维度一：食材双向模糊匹配 + 评分排序（适配俗称/简称 + 同义词归一化）
    维度二：菜名字串容错搜索（适配口语化不完整输入）
    """

    # 口语化食材同义词映射：日常俗称 → 标准食材名
    SYNONYMS = {
        "西红柿": "番茄", "洋芋": "土豆", "马铃薯": "土豆", "包菜": "卷心菜",
        "肉": "猪肉", "猪肉片": "猪肉", "肥肉": "五花肉", "老抽": "酱油",
    }

    def __init__(self, recipes: List[Recipe]):
        self.recipes = recipes

    def _normalize(self, ingredient: str) -> str:
        """食材同义词归一化：俗称 → 标准名"""
        return self.SYNONYMS.get(ingredient, ingredient)

    # ----- 维度一：食材双向模糊匹配 + 评分排序 -----
    def search_by_ingredients(self, user_ingredients: List[str]) -> List[RecipeMatch]:
        """
        食材模糊匹配：先将口语化食材同义词归一化为标准名，再做正向 + 反向
        包含检查（适配俗称、简称），按成功匹配的食材数量计算匹配分数并降序
        排序，让匹配度最高、最贴合用户需求的菜谱优先展示。
        """
        results: List[RecipeMatch] = []

        for recipe in self.recipes:
            matched: List[str] = []
            for ingredient in user_ingredients:
                normalized = self._normalize(ingredient)  # 同义词归一化
                for ri in recipe.ingredients:
                    # 双向模糊匹配，适配简称（大小写不敏感）
                    if normalized.lower() in ri.lower() or ri.lower() in normalized.lower():
                        matched.append(ingredient)
                        break  # 一个食材只匹配一次

            if matched:
                results.append(RecipeMatch(
                    recipe=recipe,
                    matched_ingredients=matched,
                    match_score=len(matched),  # 匹配分数 = 命中的食材数量
                ))

        # 按匹配度从高到低排序，优先推送最贴合用户需求的菜谱
        results.sort(key=lambda x: x.match_score, reverse=True)
        return results

    # ----- 维度二：菜名字串容错搜索 -----
    def search_by_name(self, query: str) -> List[Recipe]:
        """
        菜名字串子串匹配：摒弃传统死板的完全匹配模式，用户只需说出菜名
        核心关键词即可精准命中，前端已提前过滤口语助词、多余语气词，
        有效规避语音识别误差，完全适配日常口语对话场景。
        """
        query = query.lower().strip()
        results: List[Recipe] = []
        for recipe in self.recipes:
            if query in recipe.name.lower():
                results.append(recipe)
        return results


# 预置菜谱库（来自实操1 结构化知识库，常驻内存；提升为模块级便于现场交互验证）
DEMO_RECIPES = [
    Recipe("番茄炒蛋", ["番茄", "鸡蛋", "葱", "盐", "糖"]),
    Recipe("青椒炒肉", ["青椒", "猪肉", "蒜", "生抽", "盐"]),
    Recipe("红烧肉", ["五花肉", "酱油", "糖", "八角", "桂皮", "姜"]),
    Recipe("番茄鸡蛋汤", ["番茄", "鸡蛋", "葱", "盐", "香油"]),
    Recipe("青椒肉丝", ["青椒", "猪肉", "姜", "生抽", "淀粉"]),
]


# ==================== 现场敲代码区（答辩演示预留） ====================

# ↓↓↓ 演示时在此处照讲解稿现场敲入核心代码（模块级顶格缩进） ↓↓↓
# ① 食材双向模糊匹配 + 分数排序（讲解稿 search_by_ingredients）
# ② 菜名字串容错模糊检索（讲解稿 search_by_name）
# 敲入前先执行：from search import DEMO_RECIPES, RecipeMatch
# 敲入参考与验证：见文末【现场演示敲代码标注】，敲完直接运行 python search.py 验证
# ↓↓↓ 空位（在此空白行敲入代码） ↓↓↓
























# ↑↑↑ 现场敲代码区结束 ↑↑↑


# ==================== 实操交互运行演示 ====================

def main():
    import re
    print("=" * 60)
    print("【实操动作2】双维度智能检索功能实操演示")
    print("=" * 60)
    matcher = RecipeMatcher(DEMO_RECIPES)
    print("\n输入食材或菜名关键词进行检索；输入 q / 退出 / quit 退出程序\n")
    print("示例：")
    print("  - 输入食材「西红柿 肉」→ 按食材双向模糊匹配 + 评分降序")
    print("  - 输入菜名「红烧」→ 按菜名字串容错检索")
    print("-" * 60)

    while True:
        try:
            raw = input("请输入食材或菜名 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue
        if raw.lower() in {"q", "quit", "退出", "exit"}:
            print("已退出检索。")
            break

        # 以空格 / 逗号 / 顿号分隔用户输入，得到若干关键词
        parts = [p.strip() for p in re.split(r"[，,、\s]+", raw) if p.strip()]

        # 先按食材维度匹配（含同义词归一化 + 双向模糊 + 评分降序）
        ingredient_results = matcher.search_by_ingredients(parts)
        # 再按菜名字串维度匹配（容错检索）
        name_results = matcher.search_by_name(raw)

        print(f"\n输入关键词: {parts}")

        # 维度一：食材匹配结果
        if ingredient_results:
            print("  维度一 · 食材双向模糊匹配（按匹配分数降序）：")
            for r in ingredient_results:
                print(f"    - {r.recipe.name}  匹配分数: {r.match_score}  命中食材: {r.matched_ingredients}")
        else:
            print("  维度一 · 食材匹配：未命中任何菜谱")

        # 维度二：菜名匹配结果
        if name_results:
            print("  维度二 · 菜名字串容错检索：")
            for r in name_results:
                print(f"    - {r.name}")
        else:
            print("  维度二 · 菜名检索：未命中任何菜谱")

        print("-" * 60)

    print("\n✅ 实操动作2 演示完成：食材双向匹配 + 菜名字串检索，双维度互补，全程闭环。")


# ==================== 现场演示敲代码标注（答辩用） ====================
r'''
【实操动作2 现场演示 · 边讲边敲代码】—— 现场照讲解稿敲这两段核心代码

运行：python -i search.py 进入交互模式（先自动跑完整演示，再交互敲代码）。

① 现场照稿敲：食材双向模糊匹配 + 分数排序（讲解稿 search_by_ingredients）
>>> from search import DEMO_RECIPES, RecipeMatch
>>> def search_by_ingredients(user_ingredients):
...     results = []
...     for recipe in DEMO_RECIPES:
...         matched = []
...         for ingredient in user_ingredients:
...             for ri in recipe.ingredients:
...                 # 双向模糊匹配，适配俗称、简称
...                 if ingredient.lower() in ri.lower() or ri.lower() in ingredient.lower():
...                     matched.append(ingredient)
...         if matched:
...             results.append(RecipeMatch(recipe, matched, len(matched)))
...     # 按匹配度从高到低排序
...     results.sort(key=lambda x: x.match_score, reverse=True)
...     return results

② 现场照稿敲：菜名字串容错模糊检索（讲解稿 search_by_name）
>>> def search_by_name(query):
...     results = []
...     for recipe in DEMO_RECIPES:
...         # 关键词子串匹配，适配口语不完整输入
...         if query.lower() in recipe.name.lower():
...             results.append(recipe)
...     return results

③ 现场调用验证（文件内完整实现 RecipeMatcher，含同义词归一化 _normalize，效果更完善）
>>> from search import RecipeMatcher, DEMO_RECIPES
>>> matcher = RecipeMatcher(DEMO_RECIPES)
>>> rs = matcher.search_by_ingredients(["西红柿", "肉"])
>>> for r in rs: print(r.recipe.name, r.match_score, r.matched_ingredients)
>>> [r.name for r in matcher.search_by_name("红烧")]

演示提示：先对着讲解稿在终端敲 ①② 两段核心逻辑，再用 ③ 调用文件内完整实现验证；
讲解稿是核心逻辑，落地实现还加了同义词归一化（西红柿→番茄），"西红柿"也能精准命中。
'''


if __name__ == "__main__":
    main()
