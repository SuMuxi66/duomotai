"""
油温监测提醒（Oil Watch）

用户要求"盯油温/油温到了提醒我下食材"时启动后台监测：
- 每 1 秒读一次红外测温数据（锅面最高温）
- 连续 3 次采样 >= 目标温度判定"油温到了"（防止单次毛刺误报）
- 达到后走 send_cooking_text_with_tts 主动语音提醒，一次性触发后自动停止
- 5 分钟未达到播报超时提示并停止，避免无限挂起

目标温度来源：
- LLM 工具标记 [COOKING:OIL_WATCH:目标温度]（按食材/步骤给定，未知用 auto=160）
- 退出做饭、打断、WS 断开时自动取消
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

from .logger import backend_log

# 监测参数
POLL_INTERVAL = 1.0        # 采样间隔（秒）
HIT_REQUIRED = 3           # 连续达到目标温度的采样次数（防抖）
TIMEOUT_SECONDS = 300.0    # 监测超时（秒）
TEMP_FRESH_SECONDS = 15.0  # 温度数据新鲜度上限（超过视为传感器无效）

# 无明确目标时的兜底温度（炒菜下料的常见油温）
FALLBACK_TARGET = 160

# ---- 菜谱油温要求提取 ----
# "X成热" → 温度区间（°C），参考 HowToCook 常规表述与快答参考文案
CHENG_TEMP_RANGE: dict[int, tuple[int, int]] = {
    1: (30, 60), 2: (60, 90), 3: (90, 120), 4: (120, 150),
    5: (150, 160), 6: (160, 180), 7: (180, 200), 8: (200, 240),
    9: (240, 260), 10: (260, 300),
}
_CHENG_NUM_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
# 具体温度：120°C / 180 ℃ / 160 摄氏度 / 200-240 度 / 60 度
_TEMP_NUM_RE = re.compile(r"(\d{2,3})\s*(?:°C|℃|摄氏度|度)")
_TEMP_RANGE_RE = re.compile(
    r"(\d{2,3})\s*(?:°C|℃|摄氏度|度)?\s*[-~～—至到]\s*(\d{2,3})\s*(?:°C|℃|摄氏度|度)"
)
# "X成热"（中文/阿拉伯数字）；"油温 6 成下锅" 这类无"热"字的单独成数
_CHENG_HOT_RE = re.compile(r"([一二两三四五六七八九十\d]+)\s*成热")
_CHENG_PLAIN_RE = re.compile(r"油温[^\d一二两三四五六七八九十]{0,6}([一二两三四五六七八九十\d]+)\s*成")

# ---- 油温快答拦截问法 ----
# 监听/提醒请求（"帮我盯油温"）不在此拦截，交给 LLM 输出 OIL_WATCH 工具标记
OIL_TEMP_MONITOR_KWS = ("盯", "提醒", "叫我", "监测", "看着", "帮我看")
# 显式提到"油温/锅温"时，需同时带查询特征词才拦截
OIL_TEMP_QUERY_CORE = ("多少", "到了", "够", "热", "几成", "成热", "怎么样", "状态", "到没到", "行吗", "可以吗", "行不行", "够不够")
# 疑问语气词（用于"不含油温字样"分支的防误判：语句必须有提问意味）
_OIL_TEMP_QUESTION_CUE = re.compile(r"[吗么没了吧？?\s]|几|多少|到没|够不够|能不能|行不行|可以")
# 不含"油温/锅温"但明确是油温/下锅时机问法的短语
_OIL_TEMP_EXTRA_PHRASE = re.compile(
    r"(?:几成|成热|到没到|油够|油热|够不够热)"
    r"|(?:可以|能|该)?\s*下(?:菜|料|锅|食材)\s*(?:了)?\s*[吗么没？?]"
    r"|下(?:菜|料|锅|食材)(?:了)?[吗么没]"
)


def get_valid_oil_temp() -> Optional[float]:
    """读取锅面最高温；传感器无数据/数据过期/读数为 0 时返回 None"""
    try:
        from .temperature import get_temperature

        data = get_temperature()
        maximum = float(data.get("maximum") or 0.0)
        if maximum <= 1.0:
            return None
        ts = data.get("timestamp")
        if ts:
            try:
                from datetime import datetime

                age = (datetime.now() - datetime.fromisoformat(str(ts))).total_seconds()
                if age > TEMP_FRESH_SECONDS:
                    return None
            except Exception:
                pass
        return maximum
    except Exception:
        return None


# ---------- 菜谱/步骤油温要求提取 ----------

def _cheng_to_temp_range(raw: str) -> tuple[int, int] | None:
    """把"X成热"表述映射为温度区间，如"五成热"→(150,160)。非法返回 None"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        cheng = int(raw)
    elif raw in _CHENG_NUM_MAP:
        cheng = _CHENG_NUM_MAP[raw]
    else:
        return None
    return CHENG_TEMP_RANGE.get(cheng)


def _clean_hint_temp(lo: int, hi: int) -> tuple[int, int] | None:
    """限定油温合理区间 50-300°C，非法/噪声值返回 None"""
    if not (50 <= lo <= 300 and 50 <= hi <= 300) or lo > hi:
        return None
    return (lo, hi)


# 疑似干扰语境（水温/烤箱温等非锅油温）：仅在匹配位置附近出现时才丢弃该 hint
_TEMP_NOISE_RE = re.compile(r"沸水|水温|烤箱|预热|冰箱|冷藏|室温|冷冻|发酵|湿度|温度计")
_NOISE_CONTEXT_WINDOW = 10  # 噪声词需在温度数字前后这个字符数内才算关联


def _parse_temp_hints(text: str) -> list[tuple[int, int, str]]:
    """从步骤文本中提取油温要求，返回 [(lo, hi, desc), ...]，按可信度排序。

    优先级：具体温度范围 > 具体单点温度 > "X成热"表述。
    仅丢弃与"水温/烤箱/室温"等噪声词相邻的温度，避免把水温和烤箱温当油温。
    """
    if not text:
        return []
    hints: list[tuple[int, int, str]] = []

    def _near_noise(m: re.Match) -> bool:
        start = max(0, m.start() - _NOISE_CONTEXT_WINDOW)
        end = min(len(text), m.end() + _NOISE_CONTEXT_WINDOW)
        return bool(_TEMP_NOISE_RE.search(text[start:end]))

    # 具体温度范围："150 ℃ - 160 ℃" / "200-240 度"
    for m in _TEMP_RANGE_RE.finditer(text):
        if _near_noise(m):
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        clean = _clean_hint_temp(lo, hi)
        if clean:
            hints.append((clean[0], clean[1], f"约{clean[0]}-{clean[1]}°C"))

    # 具体单点温度："加热至 150℃"
    for m in _TEMP_NUM_RE.finditer(text):
        if _near_noise(m):
            continue
        v = int(m.group(1))
        clean = _clean_hint_temp(v, v)
        if clean:
            hints.append((clean[0], clean[1], f"约{clean[0]}°C"))

    # "X成热"："烧至 7 成热" / "加热至五成热"
    for m in _CHENG_HOT_RE.finditer(text):
        rng = _cheng_to_temp_range(m.group(1))
        if rng:
            cheng_text = f"{m.group(1)}成热"
            hints.append((rng[0], rng[1], f"{cheng_text}（约{rng[0]}-{rng[1]}°C）"))

    # "油温 6 成下锅" 这类无"热"字的单独成数
    for m in _CHENG_PLAIN_RE.finditer(text):
        rng = _cheng_to_temp_range(m.group(1))
        if rng:
            hints.append((rng[0], rng[1], f"{m.group(1)}成热（约{rng[0]}-{rng[1]}°C）"))

    # 去重（同区间只留描述最长的），并按具体温度 > 成热的可信度排序。
    # 可信度权重：温度范围 3 > 单点温度 2 > 成热 1
    weighted: list[tuple[int, int, str, int]] = []
    for lo, hi, desc in hints:
        if "-" in desc and "成热" in desc:
            weight = 1  # 成热映射
        elif "-" in desc:
            weight = 3  # 显式温度范围
        else:
            weight = 2  # 显式单点温度
        weighted.append((lo, hi, desc, weight))

    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int, str]] = []
    for lo, hi, desc, weight in sorted(
        weighted, key=lambda h: (-h[3], -(h[1] - h[0]), -len(h[2]))
    ):
        key = (lo, hi)
        if key in seen:
            continue
        seen.add(key)
        unique.append((lo, hi, desc))
    return unique


def extract_oil_temp_requirement(state: Any) -> dict | None:
    """从当前菜谱/步骤中提取油温要求（供快答"结合菜谱"使用）。

    优先级：当前步骤 → 其余步骤 → 小贴士。
    返回 {"lo","hi","desc","source","recipe","step_index"}，提取不到返回 None。
    """
    recipe = getattr(state, "current_recipe", None) or {}
    steps = recipe.get("steps") or []
    recipe_name = str(recipe.get("name") or "")
    if not steps:
        return None

    step_index = max(getattr(state, "current_step", 0) or 0, 1)

    def _hint_from_text(text: str) -> tuple[int, int, str] | None:
        hints = _parse_temp_hints(text)
        return hints[0] if hints else None

    # 1) 当前步骤
    if 0 < step_index <= len(steps):
        step = steps[step_index - 1]
        step_text = f"{step.get('title') or ''} {step.get('content') or ''}"
        hint = _hint_from_text(step_text)
        if hint:
            return {
                "lo": hint[0], "hi": hint[1], "desc": hint[2],
                "source": "step", "recipe": recipe_name, "step_index": step_index,
            }

    # 2) 其余步骤（有的菜谱油温要求写在后续步骤，如"复炸"）
    for idx, step in enumerate(steps, start=1):
        if idx == step_index:
            continue
        step_text = f"{step.get('title') or ''} {step.get('content') or ''}"
        hint = _hint_from_text(step_text)
        if hint:
            return {
                "lo": hint[0], "hi": hint[1], "desc": hint[2],
                "source": "other_step", "recipe": recipe_name, "step_index": idx,
            }

    # 3) 小贴士
    tips = str(recipe.get("tips") or "")
    if tips:
        hint = _hint_from_text(tips)
        if hint:
            return {
                "lo": hint[0], "hi": hint[1], "desc": hint[2],
                "source": "tips", "recipe": recipe_name, "step_index": None,
            }

    return None


# ---------- 油温快答拦截问法 ----------

def looks_like_oil_temp_query(text: str, state: Any = None) -> bool:
    """判断是否为油温查询类问句（确定性快答拦截条件，不走 LLM）。

    - 含"油温/锅温"：带查询特征词即拦截（保持原行为并扩展查询词）；
    - 不含"油温/锅温"但明确油温/下锅时机：需做饭场景开启才拦截，避免普通问答误伤。
    """
    if not text:
        return False
    if any(kw in text for kw in OIL_TEMP_MONITOR_KWS):
        return False

    if "油温" in text or "锅温" in text:
        return any(kw in text for kw in OIL_TEMP_QUERY_CORE)

    # 不含"油温/锅温"字样：要求做饭模式进行中（避免"可以下锅了吗"这类普通闲聊被拦截）
    cooking_active = bool(
        getattr(state, "cooking_enabled", False)
        or getattr(state, "current_recipe", None)
    )
    if not cooking_active:
        return False
    return bool(
        _OIL_TEMP_EXTRA_PHRASE.search(text)
        and _OIL_TEMP_QUESTION_CUE.search(text)
    )


# ---- 油温盯守确认门（2026-09-06）----
# 油温快答 fallback 文案末尾"需要的话说一声，我帮你盯着油温"发出后，等用户下一条短确认。
# 参照盐量脉冲确认门（cooking.py SALT_PULSE_*）：短词整句/≤8 字子串 + 长短语子串两级匹配。
OIL_WATCH_CONFIRM_EXPIRE_S = 600.0  # 确认门 10 分钟过期，防陈旧标志劫持后续"好"
# 短确认词：整句相等或 ≤8 字包含即确认（"好"/"需要"）
OIL_WATCH_CONFIRM_WORDS = (
    "好", "好的", "好啊", "行", "行吧", "可以", "需要", "嗯", "对", "是",
    "ok", "okay", "继续", "开始",
)
# 长确认短语：子串命中即确认（"帮我盯着吧"/"继续盯着"）
OIL_WATCH_CONFIRM_PHRASES = (
    "帮我盯着", "盯着吧", "继续盯", "开始盯", "帮我盯", "帮我看", "看着吧",
    "盯油温", "帮我看着", "盯着油温", "开始监测", "帮我监测",
)
# 取消词：子串命中即取消（"不用了/算了/不需要"）
OIL_WATCH_CANCEL_WORDS = (
    "不用", "不要", "取消", "算了", "暂不", "先不", "不需要", "不用盯",
    "别盯", "不盯", "别管了", "不用了",
)
# 疑问语气词：含这些的语句不视为确认（避免"我要怎么做"被"要"误判、避免"油温多少"误判）
_OIL_WATCH_QUESTION_CUE_RE = re.compile(r"[吗么呢嘛？?]|怎么|什么|为什么|怎样|如何|为何|多少")


def is_oil_watch_confirm_text(text: str) -> bool:
    """油温盯守提议后的短确认匹配（两级：长短语子串 + 短词整句/≤8 字子串）。

    含疑问语气词的语句一律不视为确认，避免"我要怎么做这道菜"被"要"误命中。
    """
    normalized = (text or "").strip()
    if not normalized:
        return False
    if _OIL_WATCH_QUESTION_CUE_RE.search(normalized):
        return False
    if any(p in normalized for p in OIL_WATCH_CONFIRM_PHRASES):
        return True
    return any(
        w == normalized or (w in normalized and len(normalized) <= 8)
        for w in OIL_WATCH_CONFIRM_WORDS
    )


def is_oil_watch_cancel_text(text: str) -> bool:
    """油温盯守提议后的取消匹配"""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return any(w in normalized for w in OIL_WATCH_CANCEL_WORDS)


def resolve_target(param: str) -> int:
    """解析目标温度：auto/空 → 兜底 160°C；非法值同样兜底；限制在 50-300°C"""
    param = (param or "").strip()
    if not param or param.lower() == "auto":
        return FALLBACK_TARGET
    try:
        return max(50, min(300, int(float(param))))
    except Exception:
        return FALLBACK_TARGET


async def start_oil_watch(runtime: Any, ws, state: Any, target: int) -> None:
    """启动油温监测（每会话同时只有一个，重复启动会替换旧监测）"""
    stop_oil_watch(state)
    state.oil_watch = {"target": target, "started_at": time.time(), "consecutive": 0}
    state.oil_watch_task = asyncio.create_task(_monitor_loop(runtime, ws, state))
    backend_log(f"[OilWatch] 启动监测: 目标 {target}°C")


def stop_oil_watch(state: Any) -> None:
    """停止油温监测（静默，取消后台任务）"""
    task = getattr(state, "oil_watch_task", None)
    if task is not None:
        task.cancel()
    state.oil_watch = None
    state.oil_watch_task = None


def is_active(state: Any) -> bool:
    return getattr(state, "oil_watch", None) is not None


async def _monitor_loop(runtime: Any, ws, state: Any) -> None:
    """监测主循环：达标播报 / 超时播报 / 异常自清理"""
    watch = state.oil_watch or {}
    target = int(watch.get("target") or FALLBACK_TARGET)
    started_at = float(watch.get("started_at") or time.time())
    consecutive = 0
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            if state.oil_watch is None or state.interrupted:
                return

            if time.time() - started_at > TIMEOUT_SECONDS:
                await _announce(
                    runtime, ws, state,
                    f"油温监测超时，{int(TIMEOUT_SECONDS // 60)} 分钟内温度没有达到 {target}°C，可以先检查一下火力。需要的话再说一声让我继续盯。",
                )
                state.oil_watch = None
                state.oil_watch_task = None
                return

            temp = get_valid_oil_temp()
            if temp is None:
                # 传感器无有效数据：不计入连续命中，也不打断用户
                consecutive = 0
                continue

            if temp >= target:
                consecutive += 1
                if consecutive >= HIT_REQUIRED:
                    await _announce(runtime, ws, state, f"油温到了（约{temp:.0f}°C），可以下食材了！")
                    state.oil_watch = None
                    state.oil_watch_task = None
                    return
            else:
                consecutive = 0
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        backend_log(f"[OilWatch] 监测异常: {exc}")
    finally:
        # 任务自身结束（非被 stop 替换）时清干净状态，避免悬挂引用
        if getattr(state, "oil_watch_task", None) is asyncio.current_task():
            state.oil_watch = None
            state.oil_watch_task = None


async def _announce(runtime: Any, ws, state: Any, text: str) -> None:
    from . import cooking_core

    backend_log(f"[OilWatch] 播报: {text}")
    await cooking_core.send_cooking_text_with_tts(runtime, ws, state, text)


def _build_recipe_combined_reply(temp: float, req: dict, state: Any) -> str:
    """结合菜谱/步骤油温要求的快答文案：实测温度 + 步骤要求 + 对比建议"""
    lo, hi = int(req["lo"]), int(req["hi"])
    desc = req["desc"]
    recipe = str(req.get("recipe") or "")
    source = str(req.get("source") or "step")
    step_index = req.get("step_index")

    if source == "step" and step_index:
        ctx = f"现在做「{recipe}」第{step_index}步，这一步要求油温{desc}"
    elif source == "other_step" and step_index:
        ctx = f"这道「{recipe}」后面的第{step_index}步要求油温{desc}"
    elif source == "tips":
        ctx = f"这道「{recipe}」的小贴士建议油温{desc}"
    else:
        ctx = f"这道「{recipe}」要求油温{desc}"

    if temp >= hi:
        suggestion = "已经达到要求，可以放心下锅了！"
    elif temp >= lo:
        suggestion = "已经到要求范围了，可以下锅。"
    else:
        gap = lo - temp
        suggestion = f"还差约 {gap:.0f}°C，建议再等等，等油温再上来一点。"

    watch = getattr(state, "oil_watch", None)
    if watch:
        suggestion += "我还在持续监测，到了会主动提醒你。"
    return f"当前锅面最高温度约 {temp:.0f}°C。{ctx}，{suggestion}"


# ---- 问题文本意图 → 目标油温（2026-09-06 用户需求）----
# 用户没有菜谱上下文、直接问"能下锅吗/还要等多久"时，从问句里识别烹饪意图，
# 按意图给出：实测温度 + 目标区间 + 温差结论（还差多少/已达到） + 下锅后操作建议
# （如"炸物下锅不用关小火"）。表按特异性排序：长词优先（"滑炒"先于"炒"）。
QUESTION_OIL_INTENTS = (
    ("滑炒", 100, 120, "滑炒肉片", "滑炒肉片下锅后快速滑散，变色就捞出"),
    ("炸", 170, 180, "炸软炸里脊、炸鱼块这类挂糊炸的菜", "下锅后不用关小火，保持中火炸制，炸到定型捞出，想更酥可以最后升大火复炸半分钟"),
    ("煎", 150, 170, "煎制类菜", "下锅后转中小火慢煎，别急着翻面"),
    ("炝锅", 150, 180, "炝锅下葱花姜片", "爆香后马上下主料翻炒"),
    ("葱花", 150, 180, "下葱花姜片", "爆香后马上下主料翻炒"),
    ("爆炒", 200, 220, "爆炒", "下锅后大火快翻，断生就出锅"),
    ("炒", 150, 180, "一般炒菜下主料", "翻炒至断生即可"),
)


def _extract_question_oil_intent(text: str):
    """从用户问句识别烹饪意图，返回 (关键词, lo, hi, 场景描述, 下锅后建议) 或 None"""
    normalized = (text or "").strip()
    if not normalized:
        return None
    for keyword, lo, hi, scene, after in QUESTION_OIL_INTENTS:
        if keyword in normalized:
            return keyword, lo, hi, scene, after
    return None


def _build_question_intent_reply(temp: float, intent: tuple, state: Any) -> str:
    """按问句意图回答：现在油温 + 目标区间 + 温差结论（还差多少/已达到） + 下锅后建议。

    还没到下限 temperature 时附带提议盯守（复用既有确认门，目标=intent 下限），
    用户回"好/帮我盯着"即开始按该目标监测。
    """
    _kw, lo, hi, scene, after = intent
    parts = [f"当前锅面最高温度约 {temp:.0f}°C。"]
    if temp >= hi:
        verdict = f"{scene}需要油温约 {lo}-{hi}°C——现在已经高于下锅温度，可以直接下锅，别再等了（再高炸物容易外糊里生）。"
    elif temp >= lo:
        verdict = f"{scene}需要油温约 {lo}-{hi}°C——现在已经达到，可以下锅了。"
    else:
        gap = lo - temp
        verdict = f"{scene}需要油温约 {lo}-{hi}°C——现在还差约 {gap:.0f}°C，还需要再等一会。"
    parts.append(verdict)
    parts.append(after)
    reply = "".join(parts)
    if not reply.endswith(("。", "！", "？")):
        reply += "。"
    if temp < lo:
        reply += "需要的话我帮你盯着，到了就叫你。"
        # 提议盯守：置确认门标志（须在 send_cooking_text_with_tts 之前写入，
        # TTS 队列背压会阻塞数十秒，否则期间用户回复会被旧状态路由错过确认门）。
        state.awaiting_oil_watch_confirm = True
        state.oil_watch_proposed_target = lo
        state.oil_watch_proposed_at = time.time()
        backend_log(f"[油温盯守] 意图识别后已发出盯守提议（目标 {lo}°C），进入待确认状态")
    return reply


async def answer_oil_temp_query(runtime: Any, ws, state: Any, text: str) -> bool:
    """确定性快答"油温到了吗"类问句（不走 LLM）。调用方已确认是查询意图。

    优先结合当前菜谱/步骤的油温要求给出对比建议；无菜谱要求时维持原有快答。
    """
    from . import cooking_core

    temp = get_valid_oil_temp()
    if temp is None:
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            "温度传感器暂时没有读到有效数据，请确认红外测温探头已对准锅面。",
        )
        return True

    req = extract_oil_temp_requirement(state)
    if req:
        reply = _build_recipe_combined_reply(temp, req, state)
    else:
        # 问句自带烹饪意图（如"炸软炸里脊能下锅吗"）→ 按意图目标回答（2026-09-06）
        intent = _extract_question_oil_intent(text)
        if intent:
            reply = _build_question_intent_reply(temp, intent, state)
        else:
            watch = getattr(state, "oil_watch", None)
            if watch:
                target = int(watch.get("target") or FALLBACK_TARGET)
                if temp >= target:
                    reply = f"当前锅面最高温度约 {temp:.0f}°C，已经达到 {target}°C 的目标，可以下食材了！"
                else:
                    reply = f"当前锅面最高温度约 {temp:.0f}°C，距离 {target}°C 的目标还差约 {target - temp:.0f}°C，我正在持续监测，到了会第一时间提醒你。"
            else:
                reply = (
                    f"当前锅面最高温度约 {temp:.0f}°C。参考：三四成热约 90-120°C 适合滑炒，"
                    f"五六成热约 150-180°C 适合下葱花姜片和主料，七八成热 200°C 以上适合煎炸。"
                    f"需要的话说一声，我帮你盯着油温。"
                )
                # 提议盯守：置确认门标志（须在 send_cooking_text_with_tts 之前写入，
                # TTS 队列背压会阻塞数十秒，否则期间用户回复会被旧状态路由错过确认门）。
                state.awaiting_oil_watch_confirm = True
                state.oil_watch_proposed_target = FALLBACK_TARGET
                state.oil_watch_proposed_at = time.time()
                backend_log("[油温盯守] 已发出盯守提议，进入待确认状态")
    await cooking_core.send_cooking_text_with_tts(runtime, ws, state, reply)
    return True


# ---- 油温超温报警（2026-09-05）：锅面温度超安全上限 → 自动弹窗 + 语音提醒 ----
# 用户指定上限 200°C（多数菜肴不需要更高油温，超过易冒烟起火）。
# 复用视觉危险的 danger_detected 弹窗通道（前端 8s 大弹窗），并同 vision_handler
# 模式清打断状态后走 generate_tts 语音。防毛刺：连续 3 次采样超温才报
# （HX711 尖峰教训）；每会话 60s 冷却防刷屏；尊重 danger_alert_enabled 总开关。
OIL_TEMP_MAX_C = 200.0
OVERTEMP_HIT_REQUIRED = 3
OVERTEMP_ALERT_COOLDOWN_S = 60.0
_overtemp_task: Optional[asyncio.Task] = None
_overtemp_consecutive = 0


async def _overtemp_check_once(runtime: Any) -> int:
    """单次超温巡检：读温度 → 连续判定 → 向在线会话推送弹窗+语音。返回本次报警连接数。

    温度低于阈值即重置连续计数；达到连续次数后报警并重置（冷却期内不重报）。
    """
    global _overtemp_consecutive
    from .ws.connection import iter_session_connections
    from .ws import generate_tts, ws_send

    temp = get_valid_oil_temp()
    if temp is None or temp < OIL_TEMP_MAX_C:
        _overtemp_consecutive = 0
        return 0
    _overtemp_consecutive += 1
    if _overtemp_consecutive < OVERTEMP_HIT_REQUIRED:
        return 0
    _overtemp_consecutive = 0  # 重置计数：持续超温时按冷却节奏再次提醒

    now = time.time()
    message = f"油温过高（当前锅面 {temp:.0f}°C），超过 {OIL_TEMP_MAX_C:.0f}°C 安全上限，请注意控制温度！"
    spoken = f"油温过高，当前锅面 {temp:.0f} 度，超过安全上限，请注意控制温度"
    alerted = 0
    for session_id, (ws, state) in iter_session_connections().items():
        try:
            if not getattr(state, "danger_alert_enabled", True):
                continue
            if now - getattr(state, "last_oil_overtemp_alert_time", 0.0) < OVERTEMP_ALERT_COOLDOWN_S:
                continue
            state.last_oil_overtemp_alert_time = now
            await ws_send(ws, "danger_detected", {
                "message": message,
                "danger_types": ["油温过高"],
                "description": message,
                "severity": "high",
                "is_high_energy": False,
                "timestamp": int(now * 1000),
            })
            # 警报为高优先级播报：清打断残留，确保语音必播（同 vision_handler 模式）
            if getattr(state, "interrupted", False):
                state.interrupted = False
            await generate_tts(runtime, ws, state, spoken, log_prefix="油温超温警报")
            alerted += 1
        except Exception as exc:
            backend_log(f"[油温超温监控] 推送报警失败 session={session_id}: {exc}")
    if alerted:
        backend_log(f"[油温超温监控] 锅面 {temp:.0f}°C 连续超温，已向 {alerted} 个会话推送弹窗+语音")
    return alerted


async def _overtemp_loop() -> None:
    from .ws import AppRuntime  # AppRuntime 定义在 ws 包入口（connection 同款用法）

    runtime = AppRuntime()
    backend_log(
        f"[油温超温监控] 已启动（上限 {OIL_TEMP_MAX_C:.0f}°C，连续 {OVERTEMP_HIT_REQUIRED} 次判定，"
        f"冷却 {OVERTEMP_ALERT_COOLDOWN_S:.0f}s）"
    )
    while True:
        try:
            await _overtemp_check_once(runtime)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            backend_log(f"[油温超温监控] 轮询异常: {exc}")
        await asyncio.sleep(POLL_INTERVAL)


def start_overtemp_monitor() -> None:
    """应用启动时调用：启动全局超温监控任务（幂等，重复调用不叠加）。"""
    global _overtemp_task
    if _overtemp_task and not _overtemp_task.done():
        return
    _overtemp_task = asyncio.create_task(_overtemp_loop())
