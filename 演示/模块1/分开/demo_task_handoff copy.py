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
    """根据菜名从菜谱库检索整道菜：先精确命中，再模糊搜索兜底"""
    # 1. 菜谱服务单例 —— 启动时已把菜谱目录下所有 .md 解析进内存字典（菜名 → 整道菜）
    svc = get_recipe_service()

    # 2. 方式1: 精确命中 —— 菜谱以菜名为 key 存字典里，一次查找直接取出
    recipe = svc.get_recipe(dish_name)
    if recipe:
        return recipe

    # 3. 方式2: 模糊搜索兜底 —— 同义词归一(番茄=西红柿) → 子串匹配 → difflib 相似度
    results = svc.search_by_name(dish_name)
    if results:
        # 命中多个时已按相似度排序，取第一个（最接近的菜名）
        return results[0]

    # 4. 两种方式都没搜到，返回 None 交给上层提示用户
    return None


def demo_recipe_task_handoff(user_text: str):
    """根据用户输入检索菜谱并交棒给状态机"""

    # ① 从用户输入里提取菜名（"我想做佛跳墙" → "佛跳墙"）
    dish_name = extract_dish_name(user_text)

    # ② 根据菜名去菜谱库检索
    recipe = fetch_recipe_from_library(dish_name)
    if recipe is None:
        return None

    # ③ 落地到会话状态：交棒给状态机
    state = SessionState()
    state.cooking_enabled = True          # 做饭模式开启
    state.cooking_state = "COOKING"       # 状态机流转: IDLE → COOKING
    state.current_recipe = recipe         # 菜谱落地（steps 就是任务清单）
    state.current_step = 1                # 游标：从第1个任务开始
    state.completed_steps = []            # 归档区：完成一个记一个
    return state


if __name__ == "__main__":
    user_text = input("你说: ").strip()
    demo_recipe_task_handoff(user_text)
