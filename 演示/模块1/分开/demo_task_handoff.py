"""
菜谱任务交棒 Demo
==================
演示 ① 根据用户"想做什么"的输入 → ② 从菜谱库检索出整道菜 → ③ 落地到会话状态机
（steps 就是任务清单本体，current_step 是游标，completed_steps 是归档区）

运行: python demo_task_handoff.py  （在 演示/模块1/ 目录下）
输入示例: 我要做番茄炒蛋

不需要启动前端、不需要 API Key，纯本地演示。
"""
import re
import sys
import os

# 输出保护: 管道/重定向时 Windows 默认 GBK 编码打不出 emoji，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 让项目模块能被导入：从脚本所在目录逐层向上，找到含 backend/ 的项目根目录
_dir = os.path.dirname(os.path.abspath(__file__))
while _dir != os.path.dirname(_dir):
    if os.path.isdir(os.path.join(_dir, "backend")):
        break
    _dir = os.path.dirname(_dir)
sys.path.insert(0, os.path.join(_dir, "backend"))

from app.session import SessionState
from app.services.recipe_service import get_recipe_service


def extract_dish_name(text: str) -> str:
    """从用户的话里抠出菜名：去掉"我想做/帮我/来一份"等引导词"""
    cleaned = re.sub(
        r"我想做个?|我要做个?|我打算做|我准备做|我想|我要|帮我|麻烦|"
        r"做一道|做一?份|做一下|来一份|来一道|教我做|教我|做",
        "", text,
    )
    return cleaned.strip(" ，,。？?！!吧呢啊哦")


def fetch_recipe_from_library(dish_name: str):
    """呈现从菜谱库获取菜谱的过程：先精确命中，再模糊搜索兜底"""
    svc = get_recipe_service()
    total = len(svc.get_all_recipes())
    print(f">>> 菜谱库共载入 {total} 道菜，开始检索《{dish_name}》...")

    # 方式1: 精确命中 —— 菜谱以菜名为 key 存在字典里，一次查找直接取出
    recipe = svc.get_recipe(dish_name)
    if recipe:
        print(f">>> 方式1 精确命中: 字典 key='{recipe.name}'，直接取出整道菜")
        return recipe

    # 方式2: 模糊搜索兜底 —— 同义词归一(番茄=西红柿) → 子串匹配 → difflib 相似度
    print(">>> 方式1 未命中，走模糊搜索兜底（同义词归一 → 子串匹配 → difflib 相似度）")
    results = svc.search_by_name(dish_name)
    if results:
        print(f">>> 方式2 模糊命中: '{dish_name}' → 《{results[0].name}》")
        return results[0]

    return None


def demo_recipe_task_handoff(user_text: str = "我要做番茄炒蛋"):
    """根据用户输入检索菜谱并交棒给状态机，呈现全过程"""

    # ① 从用户输入里提取菜名（"我要做番茄炒蛋" → "番茄炒蛋"）
    dish_name = extract_dish_name(user_text)
    print(f"\n🎤 你说: {user_text}")
    print(f">>> 提取菜名: 《{dish_name}》")

    # ② 根据菜名去菜谱库检索（呈现检索过程）
    recipe = fetch_recipe_from_library(dish_name)
    if recipe is None:
        print(f">>> 菜谱库里没有《{dish_name}》，交棒中止")
        return None

    # ③ 落地到会话状态：挂到状态机
    state = SessionState()
    state.cooking_enabled = True          # 做饭模式开启
    state.cooking_state = "COOKING"       # 状态机流转: IDLE → COOKING
    state.current_recipe = recipe         # 菜谱落地（steps 就是任务清单）
    state.current_step = 1                # 游标：从第1个任务开始
    state.completed_steps = []            # 归档区：完成一个记一个

    # ④ 呈现状态机快照：状态字段 + 任务清单 + 游标 + 归档区
    print(f"\n📋 菜谱《{recipe.name}》已挂到状态机，状态机快照:")
    print(f"   cooking_enabled  = {state.cooking_enabled}")
    print(f"   cooking_state    = {state.cooking_state}")
    print(f"   current_recipe   = 《{state.current_recipe.name}》（{len(recipe.steps)} 个任务）")
    for step in recipe.steps:
        cursor = "→" if step.step_number == state.current_step else " "
        title = step.title or step.content.splitlines()[0]
        print(f"   {cursor} 任务{step.step_number}: {title}")
    print(f"   current_step     = {state.current_step}")
    print(f"   completed_steps  = {state.completed_steps}")
    return state


if __name__ == "__main__":
    user_text = input("你说（示例: 我要做番茄炒蛋）: ").strip()
    demo_recipe_task_handoff(user_text or "我要做番茄炒蛋")
