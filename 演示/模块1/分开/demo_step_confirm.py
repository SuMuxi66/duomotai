"""
做饭对话全流程 Demo（交互增强版）
==================================
对话风格对齐 demo_cooking.py：🤖 AI 助手说 / 👤 你说 / 框线状态库快照，
并且每句话进来都先打印「状态库判断」——展示通过游标+状态决定接下来输出什么：

  分支0 IDLE             没菜谱 → 等你说"我要做xxx" → 检索 → 挂状态机
  分支0.5 AWAITING_SERVINGS  菜谱已挂载 → 等"几人份" → 份量智能计算
                         （主料线性放大 / 调味料对数收敛）→ 进入做饭
  分支1 COOKING          做饭中，游标指向第N步 → 说"好了/完成了/OK了"等
                         → confirm_step 归档 → 立即 next_step 报下一道工序
  分支3 FINISHED         全部归档 → 完工，可退出/再来一道

运行: python demo_step_confirm.py  （在 演示/模块1/分开/ 目录下）

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
# 份量智能计算（复刻项目 cooking_core：主料线性放大 + 调味料对数收敛）
from app.cooking_core import (
    parse_servings_from_text, _multiply_amount_in_line,
    _seasoning_converged_multiplier, _SEASONING_KEYWORDS,
)


# ────────────────── 终端颜色（对齐 demo_cooking.py 风格） ──────────────────

class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"


def print_ai(text):
    print(f"\n{C.BOLD}{C.BLUE}🤖 AI 助手说:{C.RESET} {text}\n")


def print_user(text):
    print(f"{C.BOLD}{C.GREEN}👤 你说:{C.RESET} {text}")


# ────────────────── 敲写部分①：菜谱检索 ──────────────────

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
    svc = get_recipe_service()
    total = len(svc.get_all_recipes())
    print(f"{C.DIM}>>> 菜谱库共载入 {total} 道菜，开始检索《{dish_name}》...{C.RESET}")

    # 方式1: 精确命中 —— 菜谱以菜名为 key 存字典里，一次查找直接取出
    recipe = svc.get_recipe(dish_name)
    if recipe:
        print(f"{C.DIM}>>> 方式1 精确命中: 字典 key='{recipe.name}'，直接取出整道菜{C.RESET}")
        return recipe

    # 方式2: 模糊搜索兜底 —— 同义词归一(番茄=西红柿) → 子串匹配 → difflib 相似度
    print(f"{C.DIM}>>> 方式1 未命中，走模糊搜索兜底（同义词归一 → 子串匹配 → difflib 相似度）{C.RESET}")
    results = svc.search_by_name(dish_name)
    if results:
        print(f"{C.DIM}>>> 方式2 模糊命中: '{dish_name}' → 《{results[0].name}》{C.RESET}")
        return results[0]

    return None


# ────────────────── 敲写部分②：状态机两个核心动作 ──────────────────

def confirm_step(state, step):
    """用户说"好了" → 归档当前工序，判断完工还是安全等待"""
    # 1. 防重复：这一步没归档过才记入归档区
    if step not in state.completed_steps:
        state.completed_steps.append(step)    # 自动归档当前工序

    # 2. 校验：确认的必须是当前游标指向的步
    if step == state.current_step:
        if state.current_step >= len(state.current_recipe.steps):
            state.cooking_state = "FINISHED"      # 没下一步 → 完工
        else:
            state.cooking_state = "STEP_WAITING"  # 有下一步 → 安全等待


def next_step(state):
    """游标推进 → 状态回到做饭中，返回下一步"""
    state.current_step += 1                      # 游标挪一格
    state.cooking_state = "COOKING"              # 状态回到做饭中
    return state.current_recipe.steps[state.current_step - 1]   # 返回下一步


# ────────────────── 状态库快照（框线风格） ──────────────────

def print_state(state, label="状态库快照"):
    """打印状态库快照：状态字段 + 任务清单（✔已归档 →游标）"""
    print(f"\n{C.BOLD}{C.YELLOW}╔═══════════════════════ {label} ════════════════════╗{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} cooking_enabled  = {C.GREEN if state.cooking_enabled else C.RED}{state.cooking_enabled}{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} cooking_state    = {C.CYAN}{state.cooking_state:20s}{C.RESET}")
    if state.current_recipe:
        steps = state.current_recipe.steps
        print(f"{C.YELLOW}║{C.RESET} current_recipe   = {C.GREEN}{state.current_recipe.name}{C.RESET} ({len(steps)} 步)")
        print(f"{C.YELLOW}║{C.RESET} current_step     = {C.CYAN}{state.current_step}{C.RESET}  (共 {len(steps)} 步)")
        print(f"{C.YELLOW}║{C.RESET} completed_steps  = {C.GREEN}{state.completed_steps}{C.RESET}")
        for s in steps:
            if s.step_number in state.completed_steps:
                mark, color = "✔", C.GREEN          # 已归档
            elif s.step_number == state.current_step:
                mark, color = "→", C.MAGENTA        # 游标当前位置
            else:
                mark, color = " ", C.RESET
            title = s.title or s.content.splitlines()[0]
            print(f"{C.YELLOW}║{C.RESET}   {color}{mark} 任务{s.step_number}: {title}{C.RESET}")
    else:
        print(f"{C.YELLOW}║{C.RESET} current_recipe   = {C.RED}None{C.RESET}")
    print(f"{C.YELLOW}╚══════════════════════════════════════════════════╝{C.RESET}\n")


def print_decision(state, branch):
    """显式呈现：状态机通过游标+状态判断接下来输出什么"""
    total = len(state.current_recipe.steps) if state.current_recipe else 0
    print(f"{C.DIM}⚙️  状态库判断: cooking_state={state.cooking_state}"
          f" | 游标 current_step={state.current_step}/{total}"
          f" | 归档={state.completed_steps}"
          f" → 命中【{branch}】{C.RESET}")


def step_title(step) -> str:
    """取工序标题：优先 title，否则取内容第一行"""
    return step.title or step.content.splitlines()[0]


def reset_to_idle(state):
    """收工：全部状态清空回 IDLE"""
    state.cooking_enabled = False
    state.cooking_state = "IDLE"
    state.current_recipe = None
    state.current_step = 0
    state.completed_steps = []


# ────────────────── 对话主循环：每句话先查状态库再分发 ──────────────────

# 确认词库：说完当前工序的表达（不区分大小写，子串匹配）
CONFIRM_KEYWORDS = [
    "好了", "好了哦", "好啦", "好嘞", "完成", "完成了", "做好了", "做完了",
    "成了", "搞定", "搞定了", "妥了", "可以了", "没问题", "ok", "done",
]

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_step_number(text: str):
    """从话里提取"第X步"的步号 X：支持阿拉伯数字和中文数字，没提步号返回 None"""
    m = re.search(r"第(.+?)步", text)
    if not m:
        return None
    token = m.group(1).strip()
    if token.isdigit():
        return int(token)
    total = 0
    for ch in token:                       # 中文数字逐字累加（支持"十""十二"）
        if ch in _CN_NUM:
            total += _CN_NUM[ch]
    return total or None


def demo_cooking_conversation():
    state = SessionState()
    state.cooking_state = "IDLE"
    state.current_step = 0
    state.completed_steps = []

    print_ai("我是你的做饭助手👨‍🍳，想做什么菜？比如「我要做番茄炒蛋」")

    while True:
        text = input(f"{C.BOLD}{C.GREEN}👤 你说:{C.RESET} ").strip()
        print_user(text)
        if not text:
            continue

        # ══ 分支分发：先查状态库，再决定输出什么 ══

        # ── 分支0 IDLE: 没有菜谱 → 只等菜名 ──
        if state.current_recipe is None:
            print_decision(state, "分支0 空闲: 没有菜谱 → 从话里提取菜名去检索")
            if "退出" in text or "不做了" in text:
                print_ai("好的，随时叫我~")
                return
            dish_name = extract_dish_name(text)
            if not dish_name:
                print_ai("告诉我菜名呀，比如「我要做番茄炒蛋」")
                continue
            recipe = fetch_recipe_from_library(dish_name)
            if recipe is None:
                print_ai(f"菜谱库里没有《{dish_name}》，换个菜名试试？")
                continue
            # 挂上状态机：菜谱落地，先等"几人份"再算用量
            state.cooking_enabled = True
            state.cooking_state = "AWAITING_SERVINGS"
            state.current_recipe = recipe
            state.current_step = 1
            state.completed_steps = []
            state.awaiting_servings = True
            state.current_servings = None
            print_state(state, "菜谱挂载后")
            print_ai(f"找到了！《{recipe.name}》共 {len(recipe.steps)} 道工序。\n"
                     f"你准备做几人份？我帮你按人数调整食材和调味的用量~")
            continue

        # ── 分支0.5 AWAITING_SERVINGS: 菜谱已挂载，等"几人份"做份量计算 ──
        if state.cooking_state == "AWAITING_SERVINGS":
            print_decision(state, "分支0.5 等份数: 菜谱已挂载 → 听\"几人份\"做份量智能计算")
            servings = parse_servings_from_text(text)
            if servings is None:
                print_ai("请告诉我具体几人份，比如「2人」「3人份」~")
                continue
            state.current_servings = servings
            state.awaiting_servings = False

            # ── 🧮 份量智能计算（复刻项目 cooking_core 算法）──
            # 主料：线性放大  倍数 = 目标份数 / 原份数
            # 调味料：对数收敛 1 + 0.6×ln(倍数)，上限 2.5 —— 避免10人份放100g盐
            recipe = state.current_recipe
            note = getattr(recipe, "servings_note", "") or ""
            m = re.search(r"(\d+)\s*[个人份]", note)
            original = int(m.group(1)) if m else 2          # 默认按2人份基准
            multiplier = servings / original if original > 0 else servings

            per_lines = [re.sub(r"^[\s>*+-]+", "", ln).strip()
                         for ln in (recipe.per_serving_lines or [])]
            per_lines = [ln for ln in per_lines if ln][:10]
            if per_lines:
                print(f"\n{C.BOLD}{C.MAGENTA}╔══ 🧮 份量智能计算（项目算法） ═══════════════════════╗{C.RESET}")
                print(f"{C.MAGENTA}║{C.RESET} 原菜谱基准: {original} 人份 → 目标: {servings} 人份 → 主料倍数 ×{multiplier:.2f}")
                for clean in per_lines:
                    is_seasoning = any(kw in clean for kw in _SEASONING_KEYWORDS)
                    eff = _seasoning_converged_multiplier(clean, multiplier)
                    adjusted = _multiply_amount_in_line(clean, multiplier)
                    tag = ("🧂调味收敛" if is_seasoning and eff != multiplier
                           else "🧂调味(无需收敛)" if is_seasoning else "🥩主料线性")
                    print(f"{C.MAGENTA}║{C.RESET} {clean}  →  {C.GREEN}{adjusted}{C.RESET}  {C.DIM}[{tag} ×{eff:.2f}]{C.RESET}")
                print(f"{C.MAGENTA}╚═══════════════════════════════════════════════════╝{C.RESET}")
            else:
                print(f"{C.DIM}>>> 该菜谱未标注每份用量，跳过计算直接开始{C.RESET}")

            state.current_step = 1
            state.cooking_state = "COOKING"
            print_state(state, "份数确认后")
            print_ai(f"好的！{servings} 人份的《{recipe.name}》，用量已按人数调好。\n"
                     f"第1步: {step_title(recipe.steps[0])}\n做完说「好了」~")
            continue

        # ── 分支1 COOKING: 做饭中，游标指向待做工序 ──
        if state.cooking_state == "COOKING":
            steps = state.current_recipe.steps
            print_decision(state, f"分支1 做饭中: 游标停在第{state.current_step}步 → 听确认词归档并报下一步")

            if "退出" in text or "不做了" in text:
                progress = list(state.completed_steps)
                reset_to_idle(state)
                print_state(state, "收工后")
                print_ai(f"好的，已收工。刚才完成了 {progress} 步，下次继续！")
                return

            # 步号校验：用户点名"第X步完成了"，先核对是不是当前游标这一步
            mentioned = parse_step_number(text)
            if mentioned is not None and mentioned != state.current_step:
                print_ai(f"现在做的是第{state.current_step}步哦，不是第{mentioned}步~")
                continue

            # 确认词命中（好了/完成了/OK了/搞定了…）→ 归档后立即报下一道工序
            if any(k in text.lower() for k in CONFIRM_KEYWORDS):
                confirm_step(state, state.current_step)      # 归档 + 判断状态
                if state.cooking_state == "FINISHED":
                    print_state(state, "确认后")
                    print_ai(f"🎉 全部 {len(steps)} 道工序完成，《{state.current_recipe.name}》做好了，开饭！\n"
                             f"说「退出」结束，或告诉我再做一道菜~")
                    continue
                done_no = state.current_step
                step = next_step(state)                      # 游标推进，直接继续
                print_state(state, "确认并推进后")
                print_ai(f"第{done_no}步完成，已归档 ✔\n第{state.current_step}步: {step_title(step)}\n做完说「好了」~")
                continue

            if "跳过" in text:
                step = next_step(state)                      # 不归档，直接跳下一步
                print_state(state, "跳过后")
                print_ai(f"好的，跳过这步。第{state.current_step}步: {step_title(step)}")
                continue

            if "下一步" in text or "继续" in text:
                print_ai("这一步还没确认呢，做完后说「好了」「完成了」「OK了」都行，我归档了才走下一步（防跳步）")
                continue

            print_ai(f"我们在做《{state.current_recipe.name}》第{state.current_step}步:"
                     f" {step_title(steps[state.current_step - 1])}\n做完说「好了」「完成了」「OK了」都行~")
            continue

        # ── 分支2 STEP_WAITING: 本步已归档，只等游标推进 ──
        if state.cooking_state == "STEP_WAITING":
            print_decision(state, f"分支2 安全等待: 第{state.current_step}步已归档 → 听\"下一步\"推进游标")

            if "退出" in text or "不做了" in text:
                progress = list(state.completed_steps)
                reset_to_idle(state)
                print_state(state, "收工后")
                print_ai(f"好的，已收工。刚才完成了 {progress} 步，下次继续！")
                return

            if "下一步" in text or "继续" in text:
                step = next_step(state)                      # 游标推进
                print_state(state, "游标推进后")
                print_ai(f"第{state.current_step}步: {step_title(step)}\n做完说「好了」~")
                continue

            if "好了" in text:
                print_ai(f"第{state.current_step}步刚才已经归档过啦（防重复），说「下一步」继续~")
                continue

            print_ai("说「下一步」我们继续，或说「退出」收工~")
            continue

        # ── 分支3 FINISHED: 全部归档 ──
        if state.cooking_state == "FINISHED":
            print_decision(state, "分支3 完工: 全部工序已归档 → 等退出或新菜谱")
            if "退出" in text or "不做了" in text:
                dish = state.current_recipe.name
                reset_to_idle(state)
                print_state(state, "收工后")
                print_ai(f"《{dish}》之旅圆满结束，下次见！")
                return
            dish_name = extract_dish_name(text)
            if dish_name:
                recipe = fetch_recipe_from_library(dish_name)
                if recipe:
                    state.cooking_state = "AWAITING_SERVINGS"
                    state.current_recipe = recipe
                    state.current_step = 1
                    state.completed_steps = []
                    state.awaiting_servings = True
                    print_state(state, "新菜谱挂载后")
                    print_ai(f"新开一道《{recipe.name}》！做几人份？")
                    continue
            print_ai("说「退出」结束，或直接说「我要做xxx」再来一道~")
            continue


if __name__ == "__main__":
    demo_cooking_conversation()
