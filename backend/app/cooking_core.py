from __future__ import annotations

import json
import math
import re
import time
import traceback
import uuid
from typing import Any

from .logger import backend_log
from .prompts.cooking_prompts import (
    COOKING_SYSTEM_PROMPT,
    RECIPE_LOOKUP_PROMPT,
    SERVINGS_CALCULATION_PROMPT,
)
from .services.recipe_service import get_recipe_service
from .session import SessionState

COOKING_START_KEYWORDS = (
    "做饭",
    "做菜",
    "下厨",
    "教我做",
    "带我做",
    "我要做",
    "我想做",
    "帮我做",
    "我想吃",
)
COOKING_EXIT_KEYWORDS = ("退出做饭", "结束做饭", "不做了", "先不做了", "退出", "结束")
COOKING_EXIT_CONFIRM_KEYWORDS = (
    "是",
    "是的",
    "对",
    "对的",
    "确认",
    "确认退出",
    "退出吧",
    "可以",
    "可以退出",
    "行",
    "好",
    "好的",
    "好啊",
    "嗯",
    "好了",
)
COOKING_EXIT_CANCEL_KEYWORDS = (
    "不退出",
    "先不退出",
    "继续做饭",
    "继续",
    "取消",
    "算了",
    "别退",
    "不想退出",
    "不用退出",
)
COOKING_CONFIRM_KEYWORDS = ("好了", "完成了", "可以了", "搞定了", "下一步", "继续", "好了好了", "ok", "OK", "okay", "好", "行")
COOKING_SKIP_KEYWORDS = ("跳过", "先跳过", "这一部跳过", "这一步跳过")

# Bug3 修复：确认词分级匹配。
# - 单字/极短词（好/行/ok/okay）以及高频问句词（下一步）：只有"去标点空白后整句相等"才命中，
#   避免长流程中任何含这些字的无关语句（如"这条鱼好不好""下一步怎么做"）被当成确认导致跳步。
# - 多字且语境特异度高的确认词（好了/完成了/可以了/搞定了/继续等）：保留子串匹配
#   （语音识别常带标点/口语前后缀，子串兼容"那我们继续吧"这类表达）。
# frozenset：仅用于规范化后"整句相等"的精确匹配（O(1) 且语义上防止误加子串用法）
COOKING_CONFIRM_EXACT_KEYWORDS = frozenset({"好", "行", "ok", "okay", "下一步", "做完了"})
COOKING_CONFIRM_SUBSTRING_KEYWORDS = ("好了", "完成了", "可以了", "搞定了", "好了好了", "继续")

# 食材就绪确认（"食材都准备好了吗？"）的整句应答词：整句相等才命中，
# 避免"我还没准备""什么时候开始"这类语句被误判为已备齐。
COOKING_INGREDIENTS_READY_EXACT_KEYWORDS = frozenset({
    "好", "好了", "好的", "好啊", "好呀", "行", "行啊", "嗯", "嗯嗯",
    "可以", "可以了", "准备", "准备了", "准备好了", "都准备好了", "食材准备好了",
    "备好了", "备齐了", "开始", "开始吧", "是的", "是", "对", "对的",
    "ok", "okay",
})

# parse_ingredient_text 的常见应答词黑名单：整句命中即认为不是食材清单，返回空列表
# （此前"好了"会被拆成['好','了']之类被当作食材加进清单）
INGREDIENT_RESPONSE_BLOCKLIST = frozenset({
    "好了", "好的", "好", "行", "行的", "嗯", "嗯嗯", "哦",
    "可以", "可以了", "不用", "不用了", "不用做", "不需要", "不做了", "先不做了",
    "退出", "退出做饭", "结束", "结束做饭", "算了", "取消",
    "没有", "没有了", "没了", "还没", "还没好", "还没准备好",
    "完成了", "搞定了", "下一步", "继续", "跳过",
    "准备", "准备了", "准备好了", "都准备好了", "开始", "开始吧",
    "是", "是的", "对", "对的", "确认",
})


def normalize_utterance(text: str) -> str:
    """
    口语语句规范化：去除首尾及中间的空白、中英文标点等非文字字符（保留中英文与数字），
    并统一转小写。用于"整句相等"级别的关键词匹配，兼容语音识别带标点/前后缀的情况。
    """
    return _RE_UTTERANCE_NOISE.sub("", text or "").lower()


def matches_cooking_confirmation(text: str) -> bool:
    """COOKING 态确认词匹配（替代对 COOKING_CONFIRM_KEYWORDS 的裸子串判断）"""
    normalized = normalize_utterance(text)
    if not normalized:
        return False
    if normalized in COOKING_CONFIRM_EXACT_KEYWORDS:
        return True
    return any(kw in normalized for kw in COOKING_CONFIRM_SUBSTRING_KEYWORDS)


# 问句 cue：命中即视为疑问句而非食材清单
QUESTION_CUES = (
    "什么", "哪些", "哪个", "怎么", "怎样", "如何", "为什么", "多少", "几",
    "多久", "多长", "吗", "呢", "有没有", "是不是", "能不能", "会不会", "？", "?",
)
# 烹饪域词：问句里带这些词时仍视为做饭语境（留在做饭流程/AI 兜底处理），
# 不强制转普通问答（如"用这些能做什么菜""第几步了"）
COOKING_DOMAIN_CUES = ("菜", "做", "吃", "食材", "推荐", "厨房", "锅", "步", "饭")


def looks_like_meta_question(text: str) -> bool:
    """问句形且不含烹饪域词的非做饭提问（如"你现在手上有什么工具""现在几点了"）。

    这类文本会被 parse_ingredient_text 拆成假食材（实测"你现在手上有什么工具"→
    ['你现在手上','什么工具']，收集态直接添加并误推荐），须在进入食材流程前剔除。
    """
    t = (text or "").strip()
    if not t:
        return False
    if not any(cue in t for cue in QUESTION_CUES):
        return False
    return not any(kw in t for kw in COOKING_DOMAIN_CUES)


def matches_ingredients_ready_confirmation(text: str) -> bool:
    """食材就绪确认匹配：仅整句（规范化后）等于应答词才命中"""
    normalized = normalize_utterance(text)
    if not normalized:
        return False
    return normalized in COOKING_INGREDIENTS_READY_EXACT_KEYWORDS

# 明确要求"开始步骤式引导做饭"的引导词：命中即直接进入步骤引导流程（写死入口，不依赖 LLM）。
# 除"步骤式"外，覆盖常见的同义表达：一步一步/一步步/手把手。
# 若用户仅表达做饭意图（如"教我做番茄炒蛋"）而未含引导词，保持普通问答由 AI 收集信息后询问。
COOKING_START_GUIDE_KEYWORDS = (
    "步骤式",
    "一步一步",
    "一步步",
    "手把手",
)

# 明确拒绝/暂缓开始步骤引导的词：保持普通问答
COOKING_DECLINE_GUIDE_KEYWORDS = (
    "不用了", "不用做", "先不", "先不用", "算了", "不需要", "不做了",
    "只是问问", "就问问", "问一下", "随便问问", "了解一下", "看看就行",
    "先聊聊", "先了解", "下次再说", "以后再说",
)

# 用户在 AI 主动询问"是否需要一步一步带你做"后的确认词（进入步骤式引导）
COOKING_ACCEPT_GUIDE_KEYWORDS = (
    "可以", "可以啊", "可以的", "好", "好的", "好啊", "行", "行啊", "嗯", "嗯嗯",
    "要", "要的", "需要", "需要啊", "需要你", "来吧", "开始吧", "开始", "是的", "是", "对", "对的", "当然",
    "麻烦你了", "好呀", "行吧", "可以呀", "教吧", "带我做", "做吧",
)

# 检测 AI 回复末尾是否主动询问了"是否需要一步一步带你做"（从而进入待确认状态）
STEP_GUIDE_ASK_PATTERNS = (
    "需要我一步一步",
    "需要我一步步",
    "需要我带您一步一步",
    "需要我带您一步步",
    "要我一步一步",
    "要我一步步",
    "要不要我一步一步",
    "要不要我一步步",
    "是否要我一步一步",
    "是否要我一步步",
    "需要我带你做",
    "要我带你做",
    "带你一步一步",
    "带你一步步",
    "带您一步一步",
    "带您一步步",
    "开始步骤式引导",
    "进行步骤式引导",
)
COOKING_UNSURE_KEYWORDS = ("不知道", "不确定", "随便", "推荐", "看看推荐", "想吃点什么", "不知道做什么")
COOKING_MORE_RECOMMENDATION_KEYWORDS = ("别的", "其他", "更多", "换一批", "换个", "再来", "再推荐", "类似", "相似", "同类", "同样")
COOKING_MORE_RECOMMENDATION_PHRASES = (
    "还有吗",
    "还有别的吗",
    "还有没有",
    "还有推荐吗",
    "再换一批",
    "再来一批",
    "换一换",
)
# 菜名/做法提示词：仅保留 ≥2 字词。单字（炒/面/锅/蛋…）子串匹配会命中日常词
# （"画面"→面、"锅里"→锅、"鸡蛋和番茄能一起吃吗"→蛋），把无关提问整体劫持成
# 菜名请求（实测"看得见画面吗"进入做饭预设）；真实菜名请求由菜谱库匹配分支兜底。
COOKING_RECIPE_HINTS = (
    "豆腐", "番茄", "西红柿", "土豆", "青椒", "茄子", "排骨",
    "红烧", "清蒸", "宫保", "鱼香", "酸辣", "麻婆", "糖醋", "孜然", "火锅", "饺子",
    "包子", "馄饨", "披萨", "汉堡", "寿司", "沙拉", "凉拌",
)
COOKING_INGREDIENT_SPLIT_RE = re.compile(r"[,\s]+")

# ===== 模块级预编译正则（供热路径函数复用，替代函数内裸 pattern / 重复编译）=====
_RE_UTTERANCE_NOISE = re.compile(r"[\s\W_]+", flags=re.UNICODE)
_RE_STEP_CN_PREFIX = re.compile(r"^\s*第\s*\d+\s*步\s*[:：\-\s]*")
_RE_STEP_ALT_PREFIX = re.compile(r"^\s*步骤\s*\d+\s*[:：\-\s]*")
_RE_STEP_NUMERIC_PREFIX = re.compile(r"^\s*\d+\s*[\.)、：:\-\s]*")
_RE_LIST_BULLET = re.compile(r"^[\s>*+-]+")
_RE_AMOUNT_WITH_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(g|ml|克|毫升|个|只|根|瓣|勺|匙|条|片|块|汤匙|茶匙|杯|碗|斤|两|kg|mg|l|颗|粒|头|尾|把|捆|扎|包|袋|盒|罐|瓶)?"
)
_RE_SERVINGS_NUM = re.compile(r"(\d+)\s*(?:人|个人|人份|份)")
_RE_ORIG_SERVINGS = re.compile(r"(\d+)\s*[个人份]")
_RE_TRAILING_ZERO_DOT = re.compile(r"(\d+)\.0\b")
# 倒计时解析（parse_timer_request）
_RE_TIMER_HOURS = re.compile(r"(\d+)\s*小时")
_RE_TIMER_MINUTES = re.compile(r"(\d+)\s*(?:分钟|分)")
_RE_TIMER_SECONDS = re.compile(r"(\d+)\s*秒")
_RE_TIMER_SIMPLE = re.compile(r"(\d+)\s*(分钟|分|秒|小时|h|m|s)")
_RE_TIMER_NUM_ONLY = re.compile(r"(?:定时|倒计时|计时|设个闹钟|设个定时|定个闹钟|定个时|提醒我)\s*(\d+)")
_RE_TIMER_CN_UNIT = re.compile(r"(?:定时|倒计时|计时|提醒我)\s*([\u4e00-\u9fa5]+?)\s*(分钟|分|秒|小时)")

# 倒计时相关关键词
TIMER_KEYWORDS = ("定时", "倒计时", "计时", "设个闹钟", "设个定时", "定个闹钟", "定个时", "提醒我", "闹钟", "定时器")
TIMER_CANCEL_KEYWORDS = ("取消定时", "取消倒计时", "取消计时", "取消闹钟", "不用定时了", "取消提醒", "取消所有", "全部取消", "清空定时")

# 中文数字映射（parse_timer_request 用，模块级常量避免每次调用重建）
_CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
    "二十五": 25, "三十": 30, "三十五": 35, "四十": 40,
    "四十五": 45, "五十": 50, "五十五": 55, "六十": 60,
    "九十": 90, "一百二十": 120,
}

# 中文数字前常见的触发/填充词，提取数值时必须先剥离，避免被贪婪正则吞入
_CN_PREFIX_WORDS = (
    "定时", "倒计时", "计时", "设个闹钟", "设个定时", "定个闹钟", "定个时",
    "提醒我", "闹钟", "定时器", "设个", "定个", "帮我", "给我", "个", "我",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def recipe_to_dict(recipe: Any) -> dict[str, Any] | None:
    if recipe is None:
        return None
    if hasattr(recipe, "model_dump"):
        data = recipe.model_dump()
    elif isinstance(recipe, dict):
        data = dict(recipe)
    else:
        data = {"name": str(recipe)}
    return {
        "name": data.get("name", ""),
        "category": data.get("category"),
        "difficulty": data.get("difficulty"),
        "ingredients": data.get("ingredients") or [],
        "prep_items": data.get("prep_items") or [],
        "servings_note": data.get("servings_note") or "",
        "per_serving_lines": data.get("per_serving_lines") or [],
        "steps": data.get("steps") or [],
        "tips": data.get("tips") or "",
        "match_count": data.get("match_count", 0),
        "matched_ingredients": data.get("matched_ingredients") or [],
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_generated_recipe(data: dict[str, Any], fallback_name: str = "") -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or fallback_name or "").strip()
    steps = data.get("steps") or []
    if not name or not isinstance(steps, list) or not steps:
        return None
    normalized_steps = []
    for step in steps:
        if isinstance(step, dict):
            raw_title = str(step.get("title") or "").strip()
            normalized_steps.append({
                "title": _normalize_step_title(raw_title) or "未命名步骤",
                "content": str(step.get("content") or "").strip(),
            })
        elif isinstance(step, str) and step.strip():
            normalized_steps.append({"title": "未命名步骤", "content": step.strip()})
    if not normalized_steps:
        return None
    return {
        "name": name,
        "category": str(data.get("category") or "").strip() or None,
        "difficulty": str(data.get("difficulty") or "").strip() or None,
        "ingredients": [str(item).strip() for item in (data.get("ingredients") or []) if str(item).strip()],
        "prep_items": [str(item).strip() for item in (data.get("prep_items") or []) if str(item).strip()],
        "servings_note": str(data.get("servings_note") or "").strip(),
        "per_serving_lines": [str(item).strip() for item in (data.get("per_serving_lines") or []) if str(item).strip()],
        "steps": normalized_steps,
        "tips": str(data.get("tips") or "").strip(),
    }


def recipe_search_result_to_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        data = item.model_dump()
    elif isinstance(item, dict):
        data = dict(item)
    else:
        data = {"name": str(item)}
    return {
        "name": data.get("name", ""),
        "category": data.get("category"),
        "difficulty": data.get("difficulty"),
        "matched_ingredients": data.get("matched_ingredients") or [],
        "match_count": len(data.get("matched_ingredients") or []),
    }


def latest_cooking_observation(state: SessionState) -> str | None:
    if not state.vision_observations:
        return None
    latest = state.vision_observations[-1]
    if isinstance(latest, dict):
        return latest.get("text") or None
    if isinstance(latest, str):
        return latest
    return None


def _normalize_step_title(title: str) -> str:
    cleaned = str(title or "").strip()
    if not cleaned:
        return ""

    cleaned = _RE_STEP_CN_PREFIX.sub("", cleaned)
    cleaned = _RE_STEP_ALT_PREFIX.sub("", cleaned)
    cleaned = _RE_STEP_NUMERIC_PREFIX.sub("", cleaned)
    return cleaned.strip()


def build_step_guidance_text(
    state: SessionState,
    intro: str = "",
    include_vision_observation: bool = False,
) -> str:
    recipe = state.current_recipe or {}
    steps = recipe.get("steps") or []
    if not steps:
        return intro or "当前菜谱没有可执行步骤。"

    step_index = max(state.current_step, 1)
    step_data = steps[step_index - 1]
    title = _normalize_step_title(step_data.get("title") or "") or ""
    content = step_data.get("content") or "请按照菜谱继续操作。"
    # 播报文本不带"第几步/共几步"（步骤进度由前端面板展示）；无标题（如"步骤N"
    # 被清洗后为空）时直接用内容作为操作描述
    body = f"{title}\n{content}" if title else content
    extras: list[str] = []
    if include_vision_observation:
        observation = latest_cooking_observation(state)
        if observation:
            extras.append(f"当前画面观察：{observation}")
    if recipe.get("tips") and step_index == len(steps):
        extras.append(f"小贴士：{recipe['tips']}")
    extras_text = f"\n\n" + "\n".join(extras) if extras else ""
    prefix = f"{intro}\n\n" if intro else ""
    return (
        f"{prefix}{body}"
        f"{extras_text}\n\n"
        f"完成后请告诉我“好了”，我继续带您做下一步。"
    )


def build_preparation_text(recipe: dict[str, Any]) -> str:
    prep_items = recipe.get("prep_items") or []
    servings_note = recipe.get("servings_note") or ""
    per_serving_lines = recipe.get("per_serving_lines") or []

    parts: list[str] = []
    header = "先准备这些原料和工具"
    if servings_note:
        # 修复：原文无分隔符直接拼接，导致"…原料和工具份量说明：…"粘连
        header += f"\n份量说明：{servings_note}"
    parts.append(header)
    if per_serving_lines:
        preview = "\n".join(f"- {_RE_LIST_BULLET.sub('', line).strip()}" for line in per_serving_lines[:6])
        parts.append("菜谱里的每份用量参考：\n" + preview)
    parts.append("请告诉我你准备做几人份。")
    return "\n\n".join(parts).strip()


def parse_servings_from_text(text: str) -> int | None:
    normalized = text.strip()
    if not normalized:
        return None

    chinese_map = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "俩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

    match = _RE_SERVINGS_NUM.search(normalized)
    if match:
        value = int(match.group(1))
        return value if value > 0 else None

    if normalized.isdigit():
        value = int(normalized)
        return value if value > 0 else None

    if normalized in chinese_map:
        value = chinese_map[normalized]
        return value if value > 0 else None

    for char, value in chinese_map.items():
        if char in normalized and any(token in normalized for token in ("人", "份", "个人")):
            return value if value > 0 else None

    return None


def parse_original_servings_from_recipe(recipe: dict[str, Any]) -> int:
    """
    从菜谱的 servings_note 中提取原份数。
    例："一份正好够 3 个人吃" → 3
    """
    servings_note = recipe.get("servings_note", "")
    if not servings_note:
        return 2  # 默认2人份

    # 匹配数字+人/份的模式
    match = _RE_ORIG_SERVINGS.search(servings_note)
    if match:
        return int(match.group(1))

    return 2  # 默认2人份


def _multiply_amount_in_line(line: str, multiplier: float) -> str:
    """
    提取行中的数字并乘以倍数，返回调整后的行。
    支持各种单位：g, ml, 克, 毫升, 个, 只, 根, 瓣, 勺等
    """
    # 调味料收敛：盐/糖/酱油等增加比例显著低于倍数（避免做10人份放100g盐）
    eff_multiplier = _seasoning_converged_multiplier(line, multiplier)

    def replace_amount(match: re.Match) -> str:
        amount = float(match.group(1))
        unit = match.group(2) or ""
        # 计算新数值
        new_amount = amount * eff_multiplier
        # 格式化：整数不显示小数，小数保留1位
        if new_amount == int(new_amount):
            formatted = str(int(new_amount))
        else:
            formatted = f"{new_amount:.1f}"
        return f"{formatted}{unit}"

    return _RE_AMOUNT_WITH_UNIT.sub(replace_amount, line)


# 调味料/香料关键词（收敛处理：不随份数线性放大）
# 盐糖酱醋油等调味料 + 蒜姜葱洋葱等去腥增香香料（同样存在味觉饱和）
_SEASONING_KEYWORDS = (
    "盐", "糖", "冰糖", "酱油", "生抽", "老抽", "蚝油", "醋", "香醋", "米醋", "陈醋",
    "料酒", "食用油", "橄榄油", "芝麻油", "香油", "味精", "鸡精", "豆瓣酱", "黄豆酱",
    "辣椒", "花椒", "胡椒", "孜然", "五香粉", "十三香", "淀粉", "生粉", "番茄酱", "芥末",
    "蜂蜜", "红糖", "白糖", "胡椒粉", "辣椒油", "甜面酱", "叉烧酱", "沙茶酱",
    "蒜", "姜", "葱", "洋葱", "蒜瓣", "姜片", "葱花", "蒜末", "姜末",
)


def _seasoning_converged_multiplier(line: str, multiplier: float) -> float:
    """
    调味料的收敛倍数：份数越多，调味料增加比例越低于倍数。
    经验法则（以原1人份为基准）：
      目标 ≤ 原份数       → 1.0
      2-3人份            → ~1.3-1.7
      4-6人份            → ~1.7-2.1
      7-10人份           → ~2.2-2.4
      10人份以上         → ~2.5 封顶
    实现：对数收敛 curve，避免机械线性放大。
    """
    if multiplier <= 1.0:
        return multiplier
    if not any(kw in line for kw in _SEASONING_KEYWORDS):
        return multiplier
    # 对数收敛：1 + 0.6 * ln(multiplier)，上限 2.5
    converged = 1 + 0.6 * math.log(multiplier)
    return round(min(converged, 2.5), 2)


def build_servings_confirmation_text(recipe: dict[str, Any], servings: int, original_servings: int = 2) -> str:
    """
    构建份数确认文本（智能计算版本）。
    尝试提取并计算食材用量，失败则回退到简单替换。
    """
    per_serving_lines = recipe.get("per_serving_lines") or []
    if not per_serving_lines:
        return f"好的，按 {servings} 人份来做。"

    # 计算倍数
    multiplier = servings / original_servings if original_servings > 0 else servings

    adjusted_lines: list[str] = []
    for line in per_serving_lines[:10]:
        original = _RE_LIST_BULLET.sub("", line).strip()

        # 尝试提取数字并计算
        adjusted = _multiply_amount_in_line(original, multiplier)

        # 如果没有数字变化（没有匹配到数字），使用简单替换
        if adjusted == original:
            adjusted = original.replace("* 份数", f"× {servings}")
            adjusted = adjusted.replace("份数 *", f"{servings} ×")
            adjusted = adjusted.replace("份数", str(servings))

        adjusted_lines.append(f"- {adjusted}")

    return (
        f"好的，按 {servings} 人份来准备。\n\n"
        f"份量参考（基于原{original_servings}人份计算）：\n"
        + "\n".join(adjusted_lines)
    )


def smart_calc_source_lines(recipe: dict[str, Any]) -> list[str]:
    """智能份数计算的待计算项标签（与 calculate_servings_with_llm 的输入同源，供前端转圈占位）"""
    per_serving_lines = recipe.get("per_serving_lines") or []
    if per_serving_lines:
        return [str(line) for line in per_serving_lines[:10]]
    ingredients = recipe.get("ingredients") or []
    return [str(ing) for ing in ingredients[:10]]


async def calculate_servings_intro_parts(
    runtime: Any,
    recipe: dict[str, Any],
    servings: int,
    original_servings: int = 2,
) -> tuple[str, list[str]]:
    """
    与 build_servings_confirmation_text_smart 相同，但额外返回
    "智能计算的食材用量"各行（不含 "- " 前缀），供前端逐项揭示计算进度。
    """
    # 先尝试使用LLM进行智能计算
    llm_result = await calculate_servings_with_llm(runtime, recipe, servings, original_servings)

    if llm_result and llm_result.get("calculated_ingredients"):
        # 使用LLM计算结果
        ingredients = llm_result["calculated_ingredients"]
        lines = []
        for ing in ingredients[:10]:  # 最多显示10项
            name = ing.get("name", "未知食材")
            # "3.0个"→"3个"：LLM 偶发输出一位小数，整数量展示去掉 .0
            calculated = _RE_TRAILING_ZERO_DOT.sub(r"\1", str(ing.get("calculated", "")))
            # 逐项解释性备注（"按倍数1.5倍精确计算"等）不进展示文案，只保留干净用量
            lines.append(f"{name}: {calculated}")

        result_parts = [
            f"好的，按 {servings} 人份来准备。",
            "",
            "智能计算的食材用量：",
        ]
        result_parts.extend(f"- {line}" for line in lines)

        # 添加烹饪调整建议
        adjustments = llm_result.get("cooking_adjustments", {})
        if adjustments:
            result_parts.append("")
            result_parts.append("烹饪建议：")
            time_note = adjustments.get("time_note")
            equipment_note = adjustments.get("equipment_note")
            if time_note:
                result_parts.append(f"⏱ {time_note}")
            if equipment_note:
                result_parts.append(f"🍳 {equipment_note}")

        # 添加小贴士
        tips = llm_result.get("tips")
        if tips:
            result_parts.append("")
            result_parts.append(f"💡 {tips}")

        return "\n".join(result_parts), lines

    # LLM计算失败，回退到简单替换版本（现在支持数字计算）
    return build_servings_confirmation_text(recipe, servings, original_servings), []


async def build_servings_confirmation_text_smart(
    runtime: Any,
    recipe: dict[str, Any],
    servings: int,
    original_servings: int = 2,
) -> str:
    """
    智能构建份数确认文本，使用LLM进行复杂换算。

    Args:
        runtime: 运行时（含 llm provider）
        recipe: 菜谱字典
        servings: 目标份数
        original_servings: 原菜谱份数（默认2人份）

    Returns:
        份数确认文本，包含智能计算后的食材用量
    """
    text, _ = await calculate_servings_intro_parts(runtime, recipe, servings, original_servings)
    return text


# 食材清单句中的引导/流程噪音词：切分前整体替换为空格。
# 顺序敏感：必须与原实现一致（"我有"在"有"之前、长词优先），否则替换结果不同。
_INGREDIENT_NOISE_PHRASES = (
    "我有", "有", "还有", "食材", "材料", "冰箱里",
    "帮我做", "我要做饭", "我要做菜", "开始做饭", "开始做菜",
    "做饭", "做菜", "下厨", "炒菜", "烹饪", "料理",
    "我想做饭", "我想做菜",
)

# 切分后无实义的停用词：整词命中则丢弃（frozenset：仅做精确成员查找）
_INGREDIENT_STOP_WORDS = frozenset({
    "没", "没有", "我", "你", "想", "要", "做", "菜", "饭",
    "一下", "这个", "那个", "教", "我们", "做饭", "做菜",
    "另外", "另外的", "还", "还有", "还有吗",
    "个别", "其他", "其他的", "更多", "换一批", "换个",
    "再来", "再推荐", "类似", "相似", "同类", "同样",
    "吗", "吗？",
})


def parse_ingredient_text(text: str) -> list[str]:
    # 常见应答词黑名单：整句（规范化后）命中即不是食材清单，返回空列表
    # （此前"好了"会被当食材拆进清单，触发误推荐）
    if normalize_utterance(text) in INGREDIENT_RESPONSE_BLOCKLIST:
        return []

    normalized = text
    for phrase in _INGREDIENT_NOISE_PHRASES:
        normalized = normalized.replace(phrase, " ")
    normalized = normalized.replace("，", ",").replace("、", ",")

    candidates = [part.strip() for part in COOKING_INGREDIENT_SPLIT_RE.split(normalized) if part.strip()]
    ingredients: list[str] = []
    for candidate in candidates:
        if candidate in _INGREDIENT_STOP_WORDS or len(candidate) > 12:
            continue
        if any(keyword in candidate for keyword in COOKING_MORE_RECOMMENDATION_KEYWORDS) or "推荐" in candidate:
            continue
        if candidate not in ingredients:
            ingredients.append(candidate)
    return ingredients


def recipe_difficulty_score(recipe: Any) -> int:
    difficulty = ""
    if hasattr(recipe, "difficulty"):
        difficulty = getattr(recipe, "difficulty") or ""
    elif isinstance(recipe, dict):
        difficulty = str(recipe.get("difficulty") or "")
    return difficulty.count("★")


def get_top_recipes(limit: int = 10) -> list[dict[str, Any]]:
    service = get_recipe_service()
    recipes = service.get_all_recipes()
    normalized = []
    for recipe in recipes:
        item = recipe_to_dict(recipe)
        if item:
            normalized.append(item)
    normalized.sort(key=lambda item: (-recipe_difficulty_score(item), len(item.get("steps") or []), item.get("name", "")))
    return normalized[:limit]


def _looks_like_more_recommendation_request(text: str, state: SessionState) -> bool:
    normalized = text.strip()
    if not normalized:
        return False

    if any(phrase in normalized for phrase in COOKING_MORE_RECOMMENDATION_PHRASES):
        return True

    if any(keyword in normalized for keyword in COOKING_MORE_RECOMMENDATION_KEYWORDS):
        return True

    if state.cooking_state == "RECOMMENDING":
        return any(token in normalized for token in ("还有", "还", "继续", "再来", "换", "别的", "其他")) or "推荐" in normalized

    return False


def _collect_recipe_seed_ingredients(state: SessionState) -> list[str]:
    seed_ingredients: list[str] = []

    def append_unique(values: list[str] | None) -> None:
        if not values:
            return
        for value in values:
            text = str(value or "").strip()
            if not text or text in seed_ingredients:
                continue
            seed_ingredients.append(text)

    append_unique(state.user_ingredients)
    if state.current_recipe:
        append_unique(state.current_recipe.get("ingredients") or [])
        append_unique(state.current_recipe.get("prep_items") or [])
    return seed_ingredients

#进行做饭前的初始化操作
def _reset_cooking_session_state(state: SessionState) -> None:
    state.cooking_enabled = True# ① 打开做饭模式总开关
    state.cooking_state = "COLLECTING_INGREDIENTS"# ② 状态机回到"收集食材"初始态
    state.current_recipe = None# ③ 清空之前选过的菜谱
    state.current_step = 0 # ④ 游标归零
    state.completed_steps = []# ⑤ 已完成清单清空
    state.user_ingredients = [] # ⑥ 用户说过的食材清空
    state.last_recommended_recipe_names = [] # ⑦ 上次推荐过的菜名清空（避免重复推）
    state.current_servings = None# ⑧ 份数清空
    state.awaiting_servings = False # ⑨ 关掉"等份数"标记
    state.awaiting_cooking_exit_confirmation = False # ⑩ 关掉"等退出确认"标记
    state.awaiting_step_guide_confirmation = False # ⑪ 关掉"等步骤引导确认"标记
    state.awaiting_ingredients_ready = False
    state.pending_guide_recipe = None
    # 换菜/重开时清理本会话的 cooking 类定时器，避免旧菜的
    # "翻面/关火"提醒插播进新菜（按会话隔离，不影响其他会话）
    try:
        from .timer_manager import timer_manager

        timer_manager.cancel_all_timers(
            category="cooking",
            session_id=getattr(state, "session_id", "default"),
        )
    except Exception as exc:
        backend_log(f"[做饭] 清理本会话烹饪定时器失败: {exc}")


def _build_related_recipe_recommendations(
    recipe_service,
    state: SessionState,
    limit: int = 10,
) -> list[dict[str, Any]]:
    excluded_names = {name for name in (state.last_recommended_recipe_names or []) if name}
    if state.current_recipe and state.current_recipe.get("name"):
        excluded_names.add(str(state.current_recipe.get("name")))

    seed_ingredients = _collect_recipe_seed_ingredients(state)
    payload: list[dict[str, Any]] = []

    if seed_ingredients:
        matched = recipe_service.search_by_ingredients(seed_ingredients)
        for item in matched:
            item_dict = recipe_search_result_to_dict(item)
            name = item_dict.get("name", "")
            if not name or name in excluded_names:
                continue
            payload.append(item_dict)
            if len(payload) >= limit:
                break

    if not payload:
        for item in get_top_recipes(limit * 2):
            name = item.get("name", "")
            if not name or name in excluded_names:
                continue
            payload.append(item)
            if len(payload) >= limit:
                break

    return payload[:limit]


def _store_last_recommended_recipe_names(state: SessionState, recipes_payload: list[dict[str, Any]]) -> None:
    state.last_recommended_recipe_names = [
        str(item.get("name") or "").strip()
        for item in recipes_payload
        if str(item.get("name") or "").strip()
    ]


def _log_tts_chunk_list(log_prefix: str, request_id: str | None, label: str, chunks: list[str]) -> None:
    request_part = f" request_id={request_id}" if request_id else ""
    backend_log(f"[{log_prefix}]{request_part} {label}拆分结果: 共 {len(chunks)} 块")
    for index, chunk in enumerate(chunks, start=1):
        chunk_text = chunk.replace("\r", "\\r").replace("\n", "\\n")
        backend_log(f"[{log_prefix}]{request_part} {label}拆分块[{index}/{len(chunks)}] len={len(chunk)} text={chunk_text}")


async def send_cooking_status(ws, state: SessionState, recipes: list[dict[str, Any]] | None = None) -> None:
    from .ws import ws_send
    from .timer_manager import get_all_countdowns

    # 只取本会话的烹饪倒计时快照：全局快照会让 A 页面看到 B 页面的倒计时（串台）
    cooking_timers = get_all_countdowns(
        category="cooking",
        session_id=getattr(state, "session_id", "default"),
    )

    await ws_send(ws, "cooking_status", {
        "enabled": state.cooking_enabled,
        "state": state.cooking_state,
        "recipe": state.current_recipe,
        "step": state.current_step,
        "servings": state.current_servings,
        "awaiting_servings": state.awaiting_servings,
        "awaiting_ingredients_ready": state.awaiting_ingredients_ready,
        "awaiting_cooking_exit_confirmation": state.awaiting_cooking_exit_confirmation,
        "ingredients": state.user_ingredients,
        "recipes": recipes or [],
        "completed_steps": state.completed_steps,
        "timers": cooking_timers,
    })


def parse_timer_request(text: str) -> dict[str, Any] | None:
    """解析用户倒计时请求，返回 {seconds: int, description: str} 或 None"""
    normalized = text.strip()
    if not normalized:
        return None

    # 检查是否包含倒计时关键词
    has_timer_keyword = any(kw in normalized for kw in TIMER_KEYWORDS)
    if not has_timer_keyword:
        return None

    def _extract_cn_value(candidate_text: str, unit_pattern: str):
        """提取单位前的中文数字秒值：「半」返回 'HALF'；数字返回秒倍数 int；无匹配返回 None"""
        m = re.search(r"([\u4e00-\u9fa5]+?)\s*" + unit_pattern, candidate_text)
        if not m:
            return None
        frag = m.group(1)
        for kw in _CN_PREFIX_WORDS:
            frag = frag.replace(kw, "")
        frag = frag.strip()
        if not frag:
            return None
        if frag == "半":
            return "HALF"
        if frag in _CHINESE_NUM_MAP:
            return _CHINESE_NUM_MAP[frag]
        last = frag[-1]
        if last in _CHINESE_NUM_MAP:
            return _CHINESE_NUM_MAP[last]
        return None

    seconds = 0

    # 模式1: X小时Y分钟 / X小时 / X分 / X分钟 / X秒
    hour_match = _RE_TIMER_HOURS.search(normalized)
    min_match = _RE_TIMER_MINUTES.search(normalized)
    sec_match = _RE_TIMER_SECONDS.search(normalized)

    if hour_match:
        hours = int(hour_match.group(1))
        seconds += hours * 3600
    if min_match:
        minutes = int(min_match.group(1))
        seconds += minutes * 60
    if sec_match:
        secs = int(sec_match.group(1))
        seconds += secs

    # 中文数字模式: 两小时/半小时/一小时三十分钟 等
    # 前缀（定时/倒计时/闹钟等触发词）会被贪婪正则吞入 val，
    # 统一用 _extract_cn_value 剥离触发词后再取值（修复"倒计时半小时/定个闹钟五分钟"失败）
    if seconds == 0:
        cn_hour = _extract_cn_value(normalized, "小时")
        if cn_hour == "HALF":
            seconds += 1800
        elif isinstance(cn_hour, int):
            seconds += cn_hour * 3600

        cn_min = _extract_cn_value(normalized, r"(?:分钟|分)")
        if cn_min == "HALF":
            seconds += 30
        elif isinstance(cn_min, int):
            seconds += cn_min * 60

    # 模式2: 纯数字 + 时间单位
    if seconds == 0:
        simple_match = _RE_TIMER_SIMPLE.search(normalized)
        if simple_match:
            num = int(simple_match.group(1))
            unit = simple_match.group(2)
            if unit in ("小时", "h"):
                seconds = num * 3600
            elif unit in ("分钟", "分", "m"):
                seconds = num * 60
            elif unit in ("秒", "s"):
                seconds = num

    # 模式3: 只有数字（默认分钟）
    if seconds == 0:
        num_only = _RE_TIMER_NUM_ONLY.search(normalized)
        if num_only:
            num = int(num_only.group(1))
            seconds = num * 60  # 默认分钟

    # 模式4: 中文数字直接跟时间词
    if seconds == 0:
        cn_with_unit = _RE_TIMER_CN_UNIT.search(normalized)
        if cn_with_unit:
            val = cn_with_unit.group(1)
            unit = cn_with_unit.group(2)
            if val in _CHINESE_NUM_MAP:
                num = _CHINESE_NUM_MAP[val]
                if unit in ("小时",):
                    seconds = num * 3600
                elif unit in ("分钟", "分"):
                    seconds = num * 60
                elif unit in ("秒",):
                    seconds = num

    if seconds <= 0:
        return None
    # 上限 24 小时
    seconds = min(seconds, 86400)

    # 提取描述
    description = ""
    # 去掉定时相关关键词和时间数字
    desc_text = normalized
    for kw in TIMER_KEYWORDS:
        desc_text = desc_text.replace(kw, " ")
    desc_text = re.sub(r"\d+\s*(小时|分钟|分|秒钟|秒|钟|h|m|s)", "", desc_text)
    desc_text = re.sub(r"[\u4e00-\u9fa5]+?\s*(小时|分钟|分|秒钟|秒|钟)", "", desc_text)
    desc_text = re.sub(r"\d+", "", desc_text)
    # 清理停用词
    for stop in ("帮我", "给我", "一下", "吧", "的", "了", "个", "啊", "呀", "钟"):
        desc_text = desc_text.replace(stop, "")
    description = desc_text.strip()
    # 去掉首尾标点
    description = re.sub(r"^[，,。.!！?？、；;：:\s]+", "", description)
    description = re.sub(r"[，,。.!！?？、；;：:\s]+$", "", description)
    if not description:
        description = "计时"

    return {"seconds": seconds, "description": description}


def format_timer_duration(seconds: int) -> str:
    """格式化倒计时时长为人类可读文本"""
    if seconds >= 3600:
        hours = seconds // 3600
        remaining = seconds % 3600
        if remaining > 0:
            mins = remaining // 60
            return f"{hours}小时{mins}分钟" if mins else f"{hours}小时"
        return f"{hours}小时"
    elif seconds >= 60:
        mins = seconds // 60
        remaining = seconds % 60
        if remaining > 0:
            return f"{mins}分{remaining}秒"
        return f"{mins}分钟"
    else:
        return f"{seconds}秒"


async def send_cooking_text_with_tts(
    runtime: Any,
    ws,
    state: SessionState,
    text: str,
    tts_provider: str | None = None,
    tts_model: str | None = None,
    tts_api_key: str | None = None,
    log_prefix: str = "TTS",
    request_id: str | None = None,
) -> None:
    from .config import settings
    from .runtime_providers import can_stream_tts
    from .ws import (
        _StreamingTTSRelay,
        _should_flush_tts_buffer,
        _split_text_for_display,
        _split_text_for_streaming,
        clean_text_for_tts,
        generate_tts,
        get_tts_api_key,
        get_tts_model,
        get_tts_voice,
        ws_send,
    )

    # 主动播报即视为新指令周期：清掉上次打断残留。interrupted 只在用户下一条文本
    # 等少数路径复位，定时器到点回调 / control_* 动作这类不经过 handle_user_text
    # 的播报入口，若不清会被 TtsRelay.enqueue 开头的
    # "if not cleaned_text or self.state.interrupted: return" 静默丢弃（打断一次 →
    # 之后所有定时器/步骤播报全灭）。打断若发生在本次播报进行中，下方循环内的
    # interrupted 检查与 relay 逐段检查仍会停，不受影响。
    state.interrupted = False

    normalized_text = text.strip()
    if not normalized_text:
        return

    # 无 request_id 的预设引导消息生成独立 id：前端按 request_id 归属流式气泡，
    # 连续多条预设消息共用空 id 时，下一条会被拼进上一条已定稿的气泡里
    if not request_id:
        request_id = f"cook-{uuid.uuid4().hex[:12]}"

    # TTS 使用清洗后的文本，避免把 markdown 标记读出来
    tts_clean_text = clean_text_for_tts(normalized_text)
    if not tts_clean_text:
        return
    tts_chunks = _split_text_for_streaming(tts_clean_text)
    if not tts_chunks:
        return

    # 前端显示使用原始 markdown 格式文本，按句子分割但保留格式
    display_chunks = _split_text_for_display(normalized_text)
    backend_log(f"[{log_prefix}] TTS清洗后文本: {tts_clean_text}")
    _log_tts_chunk_list(log_prefix, request_id, "显示文本", display_chunks)
    _log_tts_chunk_list(log_prefix, request_id, "TTS文本", tts_chunks)

    effective_provider = tts_provider or state.tts_provider or settings.tts_provider
    effective_model = get_tts_model(effective_provider, tts_model or state.tts_model)
    effective_api_key = get_tts_api_key(effective_provider, tts_api_key or state.tts_api_key)
    tts_relay: _StreamingTTSRelay | None = None
    tts_buffer = ""
    streaming_tts_started = False
    if effective_api_key and can_stream_tts(effective_provider, runtime.tts):
        voice = state.tts_voice or get_tts_voice(effective_provider)
        tts_relay = _StreamingTTSRelay(
            ws,
            state,
            effective_provider,
            runtime.tts,
            voice,
            request_id,
            log_prefix,
        )

    emitted: list[str] = []
    tts_index = 0
    for index, display_chunk in enumerate(display_chunks):
        if state.interrupted:
            await ws_send(ws, "interrupted", {"message": "AI 生成已被打断"})
            if tts_relay is not None:
                await tts_relay.close()
            return

        emitted.append(display_chunk)
        # 前端显示保留 markdown 格式的文本
        await ws_send(ws, "assistant_delta", {"delta": display_chunk, "request_id": request_id})

        # TTS 使用对应的分割块（如果存在）
        tts_chunk = tts_chunks[tts_index] if tts_index < len(tts_chunks) else ""
        if tts_relay is not None and tts_chunk:
            backend_log(
                f"[{log_prefix}] request_id={request_id} 发送 TTS 分片[{tts_index + 1}/{len(tts_chunks)}] len={len(tts_chunk)} text={tts_chunk.replace(chr(13), ' ').replace(chr(10), ' ')}"
            )
            tts_index += 1
            tts_buffer += tts_chunk
            if _should_flush_tts_buffer(tts_buffer):
                backend_log(
                    f"[{log_prefix}] request_id={request_id} 触发 TTS 入队 len={len(tts_buffer)} text={tts_buffer.replace(chr(13), ' ').replace(chr(10), ' ')}"
                )
                await tts_relay.enqueue(tts_buffer)
                streaming_tts_started = True
                tts_buffer = ""
        if index < len(display_chunks) - 1:
            import asyncio

            # 快速连播：大块长停顿（len/15，120字块要停8秒）会让气泡输出一顿一顿的。
            # 封顶 0.2s 只保留轻微节奏感，文本几乎连续呈现，TTS 由中继队列自行连续播放。
            await asyncio.sleep(min(max(len(display_chunk), 1) / 60, 0.2))

    final_text = "".join(emitted)
    await ws_send(ws, "assistant_final", {"text": final_text, "request_id": request_id})

    # 将系统引导消息记录到上下文引擎（用于做饭场景历史记录）
    try:
        if hasattr(state, 'context_engine') and state.context_engine:
            session_id = getattr(state, 'session_id', 'default')
            state.context_engine.record_assistant_message(
                session_id=session_id,
                content=final_text,
                metadata={"scene": "cooking", "type": "guidance"}
            )
    except Exception as e:
        backend_log(f"[send_cooking_text_with_tts] 记录上下文失败: {e}")

    if tts_relay is not None:
        while tts_index < len(tts_chunks):
            tts_buffer += tts_chunks[tts_index]
            backend_log(
                f"[{log_prefix}] request_id={request_id} 追加尾部 TTS 分片[{tts_index + 1}/{len(tts_chunks)}] len={len(tts_chunks[tts_index])} text={tts_chunks[tts_index].replace(chr(13), ' ').replace(chr(10), ' ')}"
            )
            tts_index += 1
            if _should_flush_tts_buffer(tts_buffer):
                backend_log(
                    f"[{log_prefix}] request_id={request_id} 尾部触发 TTS 入队 len={len(tts_buffer)} text={tts_buffer.replace(chr(13), ' ').replace(chr(10), ' ')}"
                )
                await tts_relay.enqueue(tts_buffer)
                streaming_tts_started = True
                tts_buffer = ""
        if tts_buffer:
            backend_log(
                f"[{log_prefix}] request_id={request_id} 发送最终残余 TTS 入队 len={len(tts_buffer)} text={tts_buffer.replace(chr(13), ' ').replace(chr(10), ' ')}"
            )
            await tts_relay.enqueue(tts_buffer)
            streaming_tts_started = True
        await tts_relay.close()
    else:
        await generate_tts(
            runtime,
            ws,
            state,
            final_text,
            provider=effective_provider,
            model=effective_model,
            api_key=effective_api_key,
            log_prefix=log_prefix,
            request_id=request_id,
        )


def _looks_like_recipe_request(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(hint in normalized for hint in COOKING_RECIPE_HINTS)


def _wants_step_guide(text: str) -> bool:
    """用户是否明确要求进入步骤式引导做饭（如"步骤式"/"一步一步"/"手把手"）"""
    normalized = text.strip()
    if not normalized:
        return False
    # 拒绝/暂缓词优先，避免"不需要一步一步"被误判为引导请求
    if _declines_step_guide(normalized):
        return False
    return any(kw in normalized for kw in COOKING_START_GUIDE_KEYWORDS)


def _declines_step_guide(text: str) -> bool:
    """用户是否明确拒绝/暂缓开始步骤引导（保持普通问答）"""
    normalized = text.strip()
    if not normalized:
        return False
    return any(kw in normalized for kw in COOKING_DECLINE_GUIDE_KEYWORDS)


def _accepts_step_guide(text: str) -> bool:
    """用户是否确认开始步骤式引导（在 AI 主动询问之后，如"可以/好/要/嗯"）"""
    normalized = text.strip()
    if not normalized:
        return False
    # 限制为短确认语（≤4 字），避免"我要做红烧肉"里的"要"被误判为确认
    if len(normalized) > 4:
        return False
    return any(kw in normalized for kw in COOKING_ACCEPT_GUIDE_KEYWORDS)


def _looks_like_step_guide_ask(text: str) -> bool:
    """AI 回复末尾是否询问了"是否需要一步一步带你做"这类问题"""
    if not text:
        return False
    return any(pattern in text for pattern in STEP_GUIDE_ASK_PATTERNS)


def _looks_like_cooking_question(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    cues = (
        "火候",
        "多久",
        "多长",
        "几分钟",
        "几分",
        "多少",
        "怎么",
        "能不能",
        "可以吗",
        "行吗",
        "是不是",
        "这样",
        "状态",
        "温度",
        "重量",
        "调料",
        "盐",
        "糖",
        "油",
    )
    return any(cue in normalized for cue in cues)


def _looks_like_ingredient_request(text: str, ingredients: list[str]) -> bool:
    if not ingredients:
        return False
    normalized = text.strip()
    if not normalized:
        return False
    ingredient_markers = (
        "有什么",
        "我有",
        "有",
        "食材",
        "材料",
        "冰箱",
        "剩下",
        "还剩",
    )
    return any(marker in normalized for marker in ingredient_markers) or any(
        hint in normalized for hint in COOKING_RECIPE_HINTS
    )


def _extract_recipe_name_candidate(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        return ""

    # 食材清单句式（我有XX/我还有XX）不算菜名请求：
    # "我有番茄、鸡蛋"→ 交给食材收集流程，而不是被当成"番茄鸡蛋"菜名请求
    if re.match(r"^(我有|我还有|我有一些|家里有|家里还有|现在有|手上有|手头有|只剩|剩下)", normalized):
        return ""

    prefixes = (
        "一步一步教我做",
        "一步步教我做",
        "手把手教我做",
        "一步一步带我",
        "教我做",
        "教我做一下",
        "我要做",
        "我想做",
        "帮我做",
        "带我",
        "做个",
        "做一道",
        "来个",
        "我想吃",
        "帮我来个",
        "做",
    )
    suffixes = (
        "怎么做法",
        "怎么做",
        "的做法",
        "做法",
        "教程",
    )

    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break

    # 后缀按长度降序匹配：长后缀（如"的做法"）优先于其后缀（"做法"），
    # 否则 "红烧肉的做法" 会先被 "做法" 剥离成 "红烧肉的"。
    for suffix in sorted(suffixes, key=len, reverse=True):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break

    normalized = re.sub(r"[？?。！!，,、]+$", "", normalized).strip()
    normalized = re.sub(r"\s+", "", normalized)
    # 疑问语气词结尾=提问而非报菜名（"看得见画面吗"/"红烧肉好吃吗"），
    # 不生成菜名候选，避免疑问句被菜名请求分支劫持进做饭预设
    if normalized.endswith(("吗", "呢", "么", "嘛")):
        return ""
    # 纯数字不是菜名（如份数回答"2"），避免下游模糊搜索误命中名称含数字的菜谱（如 B52轰炸机）
    if normalized.isdigit():
        return ""
    return normalized if 1 <= len(normalized) <= 20 else ""


def _is_generic_cooking_entry_candidate(candidate: str) -> bool:
    return candidate in {"饭", "菜", "做饭", "做菜", "下厨", "烹饪"}


def _looks_like_cooking_exit_confirmation(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(keyword in normalized for keyword in COOKING_EXIT_CONFIRM_KEYWORDS)


def _looks_like_cooking_exit_cancellation(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(keyword in normalized for keyword in COOKING_EXIT_CANCEL_KEYWORDS)


def _find_direct_recipe_matches(recipe_service: Any, text: str) -> list[Any]:
    # 纯数字输入（如份数回答"2"）不触发菜谱搜索，避免模糊命中名称含数字的菜谱（如 B52轰炸机）
    if text.strip().isdigit():
        return []
    candidate = _extract_recipe_name_candidate(text)
    search_terms: list[str] = []
    if candidate:
        search_terms.append(candidate)

    normalized = text.strip()
    if normalized and normalized not in search_terms:
        search_terms.append(normalized)

    matches: list[Any] = []
    seen_names: set[str] = set()
    for term in search_terms:
        for recipe in recipe_service.search_by_name(term, limit=5):
            recipe_name = getattr(recipe, "name", "") or ""
            if recipe_name in seen_names:
                continue
            seen_names.add(recipe_name)
            matches.append(recipe)
    return matches


def _build_recipe_lookup_prompt(user_input: str) -> str:
    # 模板占位符为 dish_name（无 user_input）；此前只传 user_input 会 KeyError，
    # 导致远程兜底从未成功（被上游 try/except 静默吞掉 → 用户只见"重新询问菜名"）
    dish_name = user_input.strip()
    return RECIPE_LOOKUP_PROMPT.format(dish_name=dish_name, user_input=dish_name)


async def _collect_llm_text(
    runtime: Any,
    prompt: str,
    system_prompt: str | None = None,
    enable_thinking: bool = False,
    timeout_seconds: float = 25.0,
) -> str:
    llm = getattr(runtime, "llm", None)
    if llm is None:
        return ""

    chunks: list[str] = []
    start_time = time.time()
    try:
        async for delta in llm.stream_chat(
            prompt,
            system_prompt=system_prompt,
            enable_thinking=enable_thinking,
        ):
            # 检查是否超时
            if time.time() - start_time > timeout_seconds:
                backend_log(f"[LLM] 响应超时({timeout_seconds}秒)，终止收集")
                break
            if isinstance(delta, tuple):
                delta = delta[0]
            if delta:
                chunks.append(delta)
    except Exception as exc:
        backend_log(f"[做饭兜底搜索] LLM 结构化菜谱生成失败: {exc}\n{traceback.format_exc()}")
        return ""

    return "".join(chunks).strip()


async def lookup_remote_recipe(runtime: Any, user_input: str) -> dict[str, Any] | None:
    candidate_name = _extract_recipe_name_candidate(user_input)
    prompt = _build_recipe_lookup_prompt(user_input)
    raw_text = await _collect_llm_text(runtime, prompt)
    if not raw_text:
        return None

    parsed = _extract_json_object(raw_text)
    if not parsed:
        backend_log(f"[做饭兜底搜索] 大模型返回内容无法解析为 JSON: {raw_text[:200]}")
        return None

    recipe = _normalize_generated_recipe(parsed, fallback_name=candidate_name)
    if not recipe:
        backend_log(f"[做饭兜底搜索] 结构化菜谱缺少必要字段: {parsed}")
        return None

    if not recipe.get("name"):
        return None

    return recipe


async def calculate_servings_with_llm(
    runtime: Any,
    recipe: dict[str, Any],
    target_servings: int,
    original_servings: int = 2,
) -> dict[str, Any] | None:
    """
    使用LLM智能计算份数调整后的食材用量。

    Args:
        runtime: 运行时（含 llm provider）
        recipe: 菜谱字典，包含 name, ingredients, per_serving_lines 等
        target_servings: 目标份数
        original_servings: 原菜谱份数（默认2人份）

    Returns:
        计算结果字典，包含调整后食材列表和烹饪建议；失败返回None
    """
    try:
        # 构建食材清单
        ingredients = recipe.get("ingredients") or []
        per_serving_lines = recipe.get("per_serving_lines") or []

        # 优先使用 per_serving_lines，否则使用 ingredients
        if per_serving_lines:
            ingredients_list = "\n".join(f"- {line}" for line in per_serving_lines[:20])
        elif ingredients:
            ingredients_list = "\n".join(f"- {ing}" for ing in ingredients[:20])
        else:
            return None

        prompt = SERVINGS_CALCULATION_PROMPT.format(
            recipe_name=recipe.get("name", "未知菜谱"),
            original_servings=original_servings,
            target_servings=target_servings,
            ingredients_list=ingredients_list,
        )

        # 调用LLM进行计算（复用 _collect_llm_text：关闭思考加快响应，带超时与异常保护）
        response = await _collect_llm_text(
            runtime,
            prompt,
            system_prompt="你是一位专业的烹饪用量计算助手，擅长根据份数调整食材用量。直接输出 JSON，不要思考过程。",
            enable_thinking=False,
            timeout_seconds=15.0,
        )

        if not response:
            return None

        # 提取JSON（复用 _extract_json_object，非 dict / 解析失败统一返回 None）
        result = _extract_json_object(response)
        if not result:
            return None
        return result

    except Exception as e:
        backend_log(f"[calculate_servings_with_llm] Error: {e}")
        return None


# ===== 步骤自动下料：克数提取 =====
# 修复"菜谱调料 → 自动下料"链路两个缺口：
#   ① 原实现 search() 只取第一个克数匹配，若它是食材量/超限（如"500g 五花肉切块，加入 5g 盐"
#      的首匹配 500g 超限）则整段放弃，后续真调料克数被漏；改为遍历全部匹配。
#   ② 原实现无调料词关联，"取一块 50g 姜""面团 100 克"这类食材量也会触发下料机；
#      改为优先取与调料词（盐/糖/生抽/料酒等）相邻（前后 8 字符去空白）的克数，
#      无调料词相邻时回退取第一个合法克数，但跳过紧邻食材词的克数（降低误触发）。
_DISPENSE_GRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:克|g(?![a-zA-Z]))", re.IGNORECASE)
DISPENSE_GRAM_MIN = 0.5
DISPENSE_GRAM_MAX = 100.0
DISPENSE_ADJ_WINDOW = 8

# 下料可投放的调料词（含用户指定的盐/糖/鸡精/味精/生抽/老抽/醋/料酒，及常见粉末/液体调料）
DISPENSE_SEASONING_WORDS = (
    "盐", "食用盐", "食盐", "白糖", "砂糖", "冰糖", "红糖", "糖", "鸡精", "味精",
    "胡椒粉", "白胡椒", "黑胡椒", "椒盐", "花椒粉", "辣椒粉", "五香粉", "十三香",
    "孜然粉", "咖喱粉", "生粉", "淀粉", "泡打粉", "酵母粉", "小苏打",
    "生抽", "老抽", "酱油", "豉油", "蒸鱼豉油", "醋", "香醋", "陈醋", "米醋",
    "料酒", "黄酒", "蚝油", "豆瓣酱", "番茄酱", "黄豆酱", "甜面酱", "海鲜酱", "味淋",
    "香油", "芝麻油", "花椒油", "辣椒油", "蜂蜜", "炼乳", "鱼露", "孜然", "芝麻",
)
# 常见食材量词：克数紧邻这些词时判为食材量（切块/切丝/面团分份/加水/加奶/煎肉等），不应触发下料
DISPENSE_FOOD_WORDS = (
    "姜", "葱", "蒜", "蒜末", "姜丝", "姜片", "姜末", "姜蓉", "葱花", "蒜片", "蒜蓉",
    "肉", "牛肉", "猪肉", "鸡肉", "鸭肉", "羊肉", "鱼肉", "鱼片", "蟹", "虾", "蛋液",
    "面团", "小面团", "面粉", "糯米粉", "大米粉", "中筋面粉", "高筋面粉", "低筋面粉",
    "水", "清水", "温水", "热水", "凉水", "冰水", "牛奶", "淡奶油", "鸡蛋", "蛋黄",
    "蛋白", "黄油", "奶油", "植物油", "花生油", "菜籽油", "食用油", "玉米油", "橄榄油",
    "猪油", "茶", "红茶", "绿茶", "茉莉", "薄荷", "果肉", "柠檬", "坚果", "奶粉",
    "冬瓜", "土豆", "芋泥", "手粉", "巧克力", "葡萄干", "芝士", "奶酪", "白萝卜",
    "青椒", "菠萝", "绿豆芽", "鸡汤", "冰块", "干辣椒", "蒜苗", "芹菜", "小面团",
    "香蕉", "苹果", "梨", "草莓", "米饭", "糯米", "大米", "豆芽", "香菜", "洋葱",
)


def extract_dispense_grams(guidance_text: str) -> float | None:
    """从步骤引导文本提取应自动下料的克数；返回 None 表示不触发。

    匹配策略（相对原 search() 只取第一个匹配的修复）：
    1) 遍历全部 "N 克/g" 匹配，先过滤出合法范围（DISPENSE_GRAM_MIN..DISPENSE_GRAM_MAX）；
    2) 优先取第一个与调料词相邻（前/后 8 字符去空白）的合法克数；
    3) 无调料词相邻时回退取第一个合法克数，但若其紧邻食材量词则跳过（避免食材量误触发）；
       全部合法克数都被食材词排除则返回 None。
    """

    def _near(mtch, words) -> bool:
        pre = re.sub(r"\s", "", guidance_text[max(0, mtch.start() - DISPENSE_ADJ_WINDOW):mtch.start()])
        post = re.sub(r"\s", "", guidance_text[mtch.end():mtch.end() + DISPENSE_ADJ_WINDOW])
        return any(w in pre or w in post for w in words)

    matches = list(_DISPENSE_GRAM_RE.finditer(guidance_text or ""))
    if not matches:
        return None
    legal = [m for m in matches if DISPENSE_GRAM_MIN <= float(m.group(1)) <= DISPENSE_GRAM_MAX]
    if not legal:
        return None
    # 1) 调料词相邻优先
    for m in legal:
        if _near(m, DISPENSE_SEASONING_WORDS):
            return float(m.group(1))
    # 2) 回退：跳过紧邻食材量的克数
    for m in legal:
        if not _near(m, DISPENSE_FOOD_WORDS):
            return float(m.group(1))
    return None


# ===== 用量问答自动下料：问句判定 + 建议克数提取 =====
# 需求：做菜进行中菜谱没写清某味调料用量，用户问"盐放多少"，AI 给出克数建议后
# 系统捕获该克数自动接通下料机。与步骤引导的 extract_dispense_grams 相比多两道收紧：
#   - 问句必须同时命中调料词 + 用量询问 cue，避免"盐放多了怎么办"这类求救问句
#     被误当用量询问触发下料；
#   - 建议克数只取与调料词相邻的合法匹配且多候选取最小：AI 回答常见
#     "每 100 克肉放 3 克盐"，背景量 100 的后窗也含"盐"，取最小对冲误提；
#     无调料词相邻的克数一律不触发——问答场景宁可不下料，也不能把答非所问的数字下进锅里。
SEASONING_AMOUNT_QUESTION_CUES = ("多少", "几克", "几g", "用量", "放几", "要几")


def looks_like_seasoning_amount_question(text: str) -> bool:
    """用户是否在询问某味调料的用量（如"盐放多少""要几克糖""生抽用量"）。"""
    normalized = (text or "").strip()
    if not normalized:
        return False
    has_seasoning = any(w in normalized for w in DISPENSE_SEASONING_WORDS)
    has_cue = any(c in normalized for c in SEASONING_AMOUNT_QUESTION_CUES)
    return has_seasoning and has_cue


def extract_recommended_dispense_grams(answer_text: str) -> float | None:
    """从 AI 用量建议回答中提取应自动下料的克数；返回 None 表示不触发。"""
    matches = list(_DISPENSE_GRAM_RE.finditer(answer_text or ""))
    candidates: list[float] = []
    for m in matches:
        grams = float(m.group(1))
        if not (DISPENSE_GRAM_MIN <= grams <= DISPENSE_GRAM_MAX):
            continue
        pre = re.sub(r"\s", "", answer_text[max(0, m.start() - DISPENSE_ADJ_WINDOW):m.start()])
        post = re.sub(r"\s", "", answer_text[m.end():m.end() + DISPENSE_ADJ_WINDOW])
        if any(w in pre or w in post for w in DISPENSE_SEASONING_WORDS):
            candidates.append(grams)
    return min(candidates) if candidates else None


# ===== 显式下料指令确定性拦截 =====
# 实测（2026-09-04 19:57）："下3克盐"在非做饭模式（cooking_enabled=False）落普通问答，
# 模型回复"无法直接控制自动下料设备，请手动添加"——显式指令不能依赖 LLM 输出
# [COOKING:DISPENSE] 工具标记的合规性，与倒计时/油温快答同款走确定性拦截。
# 整句严匹配（动词开头 + 克数 + 可选调料后缀），讨论式语句（"配方里加3克糖"）
# 不命中、交给问答链路，防闲聊误触发硬件。
_DISPENSE_CMD_POLITE = r"(?:帮我|给我|请|麻烦|开始|继续|先|再|直接)*"
_DISPENSE_CMD_VERB = r"(?:下料|下|放|加|投|撒|倒)(?:一点|点|上|入|些)?"
_DISPENSE_CMD_UNIT = r"(?:克|g(?![a-zA-Z]))"
# 克数支持阿拉伯数字与中文数字（语音 STT 常输出"下三克盐/放两克糖"）
_DISPENSE_CMD_NUM = r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十]+(?:点[零一二三四五六七八九]+)?|半)"
# 动词+克数+调料："下3克盐/放5g白糖/帮我加3克的盐/下料3克/下三克盐"
_DISPENSE_CMD_A_RE = re.compile(
    rf"^{_DISPENSE_CMD_POLITE}{_DISPENSE_CMD_VERB}{_DISPENSE_CMD_NUM}\s*{_DISPENSE_CMD_UNIT}的?"
    r"([\u4e00-\u9fa5]{0,3}?)(?:吧|呗)?$",
    re.IGNORECASE,
)
# 调料+克数："下盐3克/放生抽5克/下盐三克"
_DISPENSE_CMD_B_RE = re.compile(
    rf"^{_DISPENSE_CMD_POLITE}{_DISPENSE_CMD_VERB}([\u4e00-\u9fa5]{{1,3}}?){_DISPENSE_CMD_NUM}\s*{_DISPENSE_CMD_UNIT}(?:吧|呗)?$",
    re.IGNORECASE,
)
# 调料+动词+克数："盐巴下3克/白糖加5克"（口语常见调料前置语序，2026-09-05 用户实测表达）
_DISPENSE_CMD_C_RE = re.compile(
    rf"^{_DISPENSE_CMD_POLITE}([\u4e00-\u9fa5]{{1,3}}?){_DISPENSE_CMD_VERB}{_DISPENSE_CMD_NUM}\s*{_DISPENSE_CMD_UNIT}(?:吧|呗)?$",
    re.IGNORECASE,
)
# 疑问语气守卫："要放3克盐吗"是提问不是命令
_DISPENSE_CMD_QUESTION_MARKERS = ("吗", "么", "呢", "嘛", "？", "?")

_CN_NUM_CHAR = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
                "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _cn_int_part(s: str) -> float | None:
    if "十" in s:
        left, _, right = s.partition("十")
        if (left and left not in _CN_NUM_CHAR) or (right and right not in _CN_NUM_CHAR):
            return None
        tens = float(_CN_NUM_CHAR[left]) if left else 1.0
        ones = float(_CN_NUM_CHAR[right]) if right else 0.0
        return tens * 10 + ones
    if not s or any(ch not in _CN_NUM_CHAR for ch in s):
        return None
    return float("".join(_CN_NUM_CHAR[ch] for ch in s))


def _dispense_num_to_float(raw: str) -> float | None:
    """下料指令克数解析：阿拉伯数字直转，中文数字（三/两/十五/二十三/半/三点五）转数值。"""
    try:
        return float(raw)
    except ValueError:
        pass
    if raw == "半":
        return 0.5
    if "点" in raw:
        int_part, _, frac_part = raw.partition("点")
        base = _cn_int_part(int_part) if int_part else 0.0
        if base is None or not frac_part or any(ch not in _CN_NUM_CHAR for ch in frac_part):
            return None
        return float(f"{base:g}." + "".join(_CN_NUM_CHAR[ch] for ch in frac_part))
    return _cn_int_part(raw)


def _cmd_seasoning_ok(snippet: str) -> bool:
    """下料指令尾部词校验：空（裸指令"下3克"）或含可投放调料词（"加100克水"的水不在词表，拒绝）。"""
    if not snippet:
        return True
    return any(w in snippet for w in DISPENSE_SEASONING_WORDS)


def parse_dispense_command(text: str) -> float | None:
    """识别显式下料指令，返回克数（DISPENSE_GRAM_MIN~MAX）；非指令返回 None。

    整句严匹配三种语序："下3克盐"（动词+克数+调料）、"下盐3克"（动词+调料+克数）、
    "盐巴下3克"（调料+动词+克数），允许前缀礼貌/副词（帮我/先/再…）与尾部语气词
    （吧/呗），克数支持中文数字。
    """
    normalized = (text or "").strip()
    if not normalized:
        return None
    if any(q in normalized for q in _DISPENSE_CMD_QUESTION_MARKERS):
        return None
    for pattern, num_group, seasoning_group in (
        (_DISPENSE_CMD_A_RE, 1, 2),
        (_DISPENSE_CMD_B_RE, 2, 1),
        (_DISPENSE_CMD_C_RE, 2, 1),
    ):
        m = pattern.match(normalized)
        if not m:
            continue
        grams = _dispense_num_to_float(m.group(num_group))
        if grams is None:
            continue
        if DISPENSE_GRAM_MIN <= grams <= DISPENSE_GRAM_MAX and _cmd_seasoning_ok(m.group(seasoning_group)):
            return grams
    return None


__all__ = [
    "COOKING_SYSTEM_PROMPT",
    "COOKING_START_KEYWORDS",
    "COOKING_EXIT_KEYWORDS",
    "COOKING_EXIT_CONFIRM_KEYWORDS",
    "COOKING_EXIT_CANCEL_KEYWORDS",
    "COOKING_CONFIRM_KEYWORDS",
    "COOKING_SKIP_KEYWORDS",
    "normalize_utterance",
    "matches_cooking_confirmation",
    "matches_ingredients_ready_confirmation",
    "COOKING_START_GUIDE_KEYWORDS",
    "COOKING_DECLINE_GUIDE_KEYWORDS",
    "COOKING_UNSURE_KEYWORDS",
    "COOKING_MORE_RECOMMENDATION_KEYWORDS",
    "COOKING_MORE_RECOMMENDATION_PHRASES",
    "COOKING_RECIPE_HINTS",
    "TIMER_KEYWORDS",
    "TIMER_CANCEL_KEYWORDS",
    "recipe_to_dict",
    "recipe_search_result_to_dict",
    "latest_cooking_observation",
    "build_step_guidance_text",
    "build_preparation_text",
    "parse_servings_from_text",
    "parse_original_servings_from_recipe",
    "build_servings_confirmation_text",
    "build_servings_confirmation_text_smart",
    "parse_ingredient_text",
    "get_top_recipes",
    "_looks_like_more_recommendation_request",
    "_collect_recipe_seed_ingredients",
    "_reset_cooking_session_state",
    "_build_related_recipe_recommendations",
    "_store_last_recommended_recipe_names",
    "_looks_like_recipe_request",
    "_wants_step_guide",
    "_declines_step_guide",
    "_accepts_step_guide",
    "_looks_like_step_guide_ask",
    "_looks_like_cooking_question",
    "_looks_like_ingredient_request",
    "_extract_recipe_name_candidate",
    "_is_generic_cooking_entry_candidate",
    "_looks_like_cooking_exit_confirmation",
    "_looks_like_cooking_exit_cancellation",
    "_find_direct_recipe_matches",
    "_build_recipe_lookup_prompt",
    "lookup_remote_recipe",
    "send_cooking_status",
    "send_cooking_text_with_tts",
    "parse_timer_request",
    "format_timer_duration",
    "calculate_servings_with_llm",
    "extract_dispense_grams",
    "SEASONING_AMOUNT_QUESTION_CUES",
    "looks_like_seasoning_amount_question",
    "extract_recommended_dispense_grams",
    "parse_dispense_command",
]
