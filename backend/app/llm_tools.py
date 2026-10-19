# -*- coding: utf-8 -*-
"""主对话大模型的 function calling 工具层。

演进（2026-09-09）：
- 起步为只读工具层（温度/重量/风扇状态/菜谱），解决模型凭聊天历史幻觉设备状态；
- 2026-09-09 晚用户授权接入全部 11 个工具（与 backend/mcp_server.py 同构）：
  新增去皮/下料启停与状态/风扇开关等写操作。安全设计：
  1. 确定性拦截链路仍优先——"下3克盐"等显式指令在进入 LLM 前就被拦截执行；
  2. dispense_start 带跨轮确认门：首次调用只"预约"（返回 confirmation_required），
     模型必须向用户复述克数征得同意，用户确认后的下一轮再调用才真正执行；
     同一轮工具循环里重复调用不生效（armed_turn 守卫防绕过）；
  3. dispense_stop（安全动作）、风扇开关、去皮直接执行，结果如实回给模型；
- 2026-09-10 增至 14 个工具：语音模式三件套（持续识别开启/关闭/状态，经
  app/voice_mode.py 向前端 WS 广播 voice_mode 指令执行），仍与 mcp_server.py 同构。
- 2026-09-20 dispense_start/stop 改经**真实 MCP 链路**执行（app/mcp_client.py 常驻
  stdio 子进程跑 mcp_server.py，MCP 协议调用后由其 HTTP 回环到本机 /dispense/*），
  「通过MCP」从话术变为事实；链路失败按 MCP_DISPENSE_FALLBACK 回退进程内直调。
- 执行器永远不抛异常、单个工具带超时，失败以 {"error": ...} 返回由模型自行兜底。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Awaitable, Callable

from .config import settings
from .logger import backend_log

# 单工具执行上限：fan_status 走 miio 网络可能到秒级，其余都是本地内存读
_TOOL_TIMEOUT_S = 10.0
# dispense_start 内部等串口就绪最长 15s，超时给 25s（与 mcp_server.py 一致）
_TOOL_TIMEOUT_OVERRIDES: dict[str, float] = {"dispense_start": 25.0}

# 下料写操作经真实 MCP 链路执行（2026-09-20 用户要求：dispense_start/stop 走 mcp_server.py，
# 「通过MCP」从话术变成事实）。MCP_DISPENSE_ENABLED=0 整体旁路回直调；链路失败时按
# MCP_DISPENSE_FALLBACK 决定回退直调（默认开，安全硬件保证「永远能动」）或返回错误。
_MCP_DISPENSE_ENABLED = os.getenv("MCP_DISPENSE_ENABLED", "1").strip().lower() not in ("0", "false", "off")
_MCP_DISPENSE_FALLBACK = os.getenv("MCP_DISPENSE_FALLBACK", "1").strip().lower() not in ("0", "false", "off")

# dispense_start 确认门：预约后 120s 内、跨轮（下一轮用户请求）、同克数的再次调用才放行；
# 另一条放行路径：用户本轮消息本身是确认词（模型上一轮口头征询、用户刚答复确认）。
_DISPENSE_CONFIRM_WINDOW_S = 120.0
_DISPENSE_GATES: dict[str, dict] = {}   # session_id -> {"target_g", "armed_turn", "expires_at"}
_TURN_SEQ: dict[str, int] = {}          # session_id -> 单调递增轮次（每个用户请求 +1）
# 每 session 最近一次实际执行（turn, target_g, 时间戳）——防同一轮工具循环里重复执行
_DISPENSE_LAST_EXEC: dict[str, tuple[int, float, float]] = {}

# 确认词判定（模型口头征询后用户的简短肯定答复）。只认短促肯定；
# 含否定/取消/变更语义的一律不算，防止"不对""别放""换 20 克"误放行。
_CONFIRM_EXACT = {
    "好", "好的", "行", "可以", "对", "是", "是的", "嗯", "嗯嗯", "没错",
    "确认", "同意", "没问题", "ok", "okay", "执行", "开始", "就这么办",
    # 2026-09-16 放盐/调料流程：SALT_FLOW_HINT 引导用户回"准备好了"，必须能放行
    "准备好了", "好了", "好啦", "放吧", "放盐巴", "放盐吧", "开始放盐",
    "准备完毕", "准备完成", "就绪", "锅铲放好了",
    # 2026-09-16 追加：直接下开阀指令时不该再绕一圈确认
    "打开吧", "打开", "开吧", "开阀", "出料", "出吧", "来吧", "开始吧", "倒吧",
}
_CONFIRM_SUBSTR = ("确认", "同意", "就这么", "准备好了", "放盐巴", "锅铲放好")
_CONFIRM_NEGATIVE = ("不", "别", "取消", "停止", "停", "换", "等")

# 2026-09-16：用户常常"先讲一段再下指令"（"……而且也不需要二次确认这种东西。打开吧"），
# 旧逻辑对超过 12 字的整句一律判否，导致明明说了"打开吧"还要再确认一次。这里对长句
# 只取**最后一句**判断：末尾是明确的开阀指令就算确认（否定词同样只看最后一句）。
_TAIL_CMD_RE = re.compile(
    r"(打开(?:吧|啊|呀|了)?|开吧|开阀|出料(?:吧)?|出吧|放吧|放盐(?:吧)?|"
    r"开始放(?:盐)?|来吧|倒吧)[\s。！？!?.，,]*$"
)


def _confirmation_verdict(text: str) -> bool | None:
    """确认词判定（三态）：True=明确确认；False=明确否定/无关；None=规则拿不准。

    None 是给语义兜底留的口子（2026-09-16 用户选定方案 B）：规则命中时零延迟走快车道，
    规则够不着时才花一次极短 LLM 调用。安全语义由规则硬拦——否定词一律 False，绝不交给模型。
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    if len(t) > 12:
        # 长句：只看最后一句，避免"不需要…""别…"这类前面出现的否定词误伤末尾指令
        tail = re.split(r"[。！？!?；;\n]", t)[-1].strip()
        if not tail:
            return None
        if any(neg in tail for neg in _CONFIRM_NEGATIVE):
            return False
        if _TAIL_CMD_RE.search(tail):
            return True
        return None      # 长句、尾句既非否定也非明确指令 → 交给语义判断
    if any(neg in t for neg in _CONFIRM_NEGATIVE):
        return False
    if t in _CONFIRM_EXACT:
        return True
    if any(k in t for k in _CONFIRM_SUBSTR):
        return True
    return None          # 短句但不在词表 → 交给语义判断（新说法）


def _is_confirmation_phrase(text: str) -> bool:
    """兼容包装：只关心"是否明确确认"，None（拿不准）按 False 处理。"""
    return _confirmation_verdict(text) is True


# 语义兜底（方案 B，2026-09-16 用户选定）：规则拿不准时，用一次极短 LLM 调用判断用户
# 是不是在同意放盐。**只在"模型刚请求过确认 + 用户回复规则认不出"这一种情况触发**，
# 正常对话零影响、零额外开销。超时/异常/没答"是"一律按不放行处理（安全优先）。
_SEMANTIC_CONFIRM_TIMEOUT_S = 4.0


async def _semantic_confirm(runtime, user_text: str) -> bool:
    if runtime is None:
        return False
    vlm = getattr(runtime, "vlm", None)
    if vlm is None:
        return False
    prompt = (
        "厨房助手刚刚问用户是否确认放盐（打开调料出口 1.5 秒）。\n"
        f"用户回复：{user_text}\n"
        "这句话是在表示同意、让助手现在放盐吗？只回答一个字：是 或 否。不要解释。"
    )
    text = ""

    async def _collect() -> None:
        nonlocal text
        async for delta, _reasoning in vlm.stream_chat(
            prompt,
            system_prompt="你是意图判定器，只输出「是」或「否」。",
            temperature=0.0,
            max_tokens=4,
            enable_thinking=False,
        ):
            text += delta or ""
            if len(text) >= 6:
                break

    try:
        await asyncio.wait_for(_collect(), timeout=_SEMANTIC_CONFIRM_TIMEOUT_S)
    except Exception as exc:
        backend_log(f"[确认门] 语义兜底失败（按不放行处理）: {type(exc).__name__}: {exc}")
        return False
    head = text.strip()
    yes = head.startswith("是") or "同意" in head or head.lower().startswith("yes")
    backend_log(f"[确认门] 语义兜底：{head[:16]!r} → {'放行' if yes else '不放行'}")
    return yes

_TOOL_SCHEMA_NAMES = (
    "get_temperature", "get_weight", "weight_tare",
    "dispense_start", "dispense_stop", "dispense_status",
    "search_recipes", "get_recipe",
    "get_fan_status", "fan_on", "fan_off",
    "voice_continuous_start", "voice_continuous_stop", "voice_continuous_status",
)

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_temperature",
            "description": "读取红外测温传感器实测数据（摄氏度）。返回锅面目标/平均/最高/最低温度。"
                           "凡用户问温度、油温、锅温、能否下锅、还要等多久，必须调用本工具取实测值，"
                           "禁止自己编造温度数字；拿到实测后结合该菜品所需油温给出能否下锅的明确结论与火候建议。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weight",
            "description": "读取称重传感器当前读数（克）。凡用户问重量、秤上多少克、食材多重，"
                           "必须调用本工具取实测值，禁止自己编造重量数字。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fan_status",
            "description": "读取排风扇（小米智能插座）当前开关状态。只查状态，不能开关风扇。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_recipes",
            "description": "按食材列表搜索本地菜谱库（362 道家常菜），返回匹配菜谱名列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ingredients": {
                        "type": "string",
                        "description": "食材，逗号分隔，如：鸡蛋,西红柿",
                    },
                },
                "required": ["ingredients"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": "按菜名查询菜谱详情（用料与分步做法）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "菜谱名，如：红烧肉"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weight_tare",
            "description": "称重去皮：以秤面当前读数为新零点。秤面需为空，否则后续读数会偏小。"
                           "用户说去皮、清零、归零时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispense_start",
            "description": "启动下料 / 打开调料出口（经厨房硬件 MCP 服务真实执行）。两种模式二选一："
                           "① 定时出料——传 pulse_seconds（秒），通电指定秒数后自动关闭，放盐/放调料用它；"
                           "② 称重闭环——传 target_g（克），持续出料到称重达标后自动停止。"
                           "本工具有确认门：首次调用只返回待确认状态（不会执行），必须先向用户复述"
                           "并征得明确同意，等用户口头同意后的下一轮对话再调用一次才真正执行；"
                           "同一轮回复内重复调用不会执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pulse_seconds": {
                        "type": "number",
                        "description": "定时出料秒数（0.5~30），如 1.5 表示通电 1.5 秒后自动关闭。放盐/放调料用。",
                    },
                    "target_g": {
                        "type": "number",
                        "description": "称重闭环目标克数（0.5~100），如 3 表示下 3 克。与 pulse_seconds 二选一。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispense_stop",
            "description": "立即停止下料并给下料机断电（紧急/手动停止）。用户要求停止下料时直接调用，无需确认。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispense_status",
            "description": "查询下料进行状态：是否进行中、目标克数、当前重量、剩余克数。无副作用。"
                           "不确定下料是否执行/完成时，先调用本工具核实再回答。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fan_on",
            "description": "打开排风扇。用户要求开风扇、或油烟大需要排风时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fan_off",
            "description": "关闭排风扇。用户要求关风扇时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_continuous_start",
            "description": "开启持续识别模式：免唤醒词持续收音，进入通话式对话（说完即答，长静默自动挂断）。"
                           "用户说'打开持续识别/持续唤醒/一直听我说'时调用。需要前端页面在线才生效。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_continuous_stop",
            "description": "关闭持续识别/挂断通话，恢复唤醒词模式。用户说'挂断/退出持续识别/别听了'时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_continuous_status",
            "description": "查询当前语音模式（持续识别是否开启）与在线前端页面数。无副作用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# 工具调用前给用户一句即时口头反馈的提示词（拼进 system prompt，仅在工具层激活时）。
# 背景（2026-09-11）：工具决策轮（模型决定调什么工具）实测 3~28 秒且期间无任何
# 可见/可听输出，用户体感"发了消息没动静"；要求模型"边播报边调工具"后，播报立即
# 流式上屏并进 TTS，死时间变成即时反馈（播报+工具同轮由 unified._stream_with_tool_loop
# 执行工具并续答实际结果）。
TOOL_PREAMBLE_HINT = (
    "【工具调用风格】凡是要调用工具（测温/称重/去皮/下料/风扇/菜谱/语音模式等），"
    "开场先用一句简短口语说明你接下来要做什么，如\"我来看一下温度\"\"我称一下重量\"，"
    "紧接着在同一轮直接发起工具调用，不要等用户回复；"
    "拿到工具结果后，回答里点明数据来自该工具（如\"刚测到锅面 25 度\"），再结合语义给出结论。"
    "开场播报必须简短（一句话以内），禁止在播报里编造或预告具体结果数字。\n"
    "⚠️ 硬性边界（2026-09-16 修订）：说一句\"我调用了某某工具\"不等于真的调用了——工具是否执行"
    "由系统决定，不是由你的口播决定。因此：\n"
    "  1) 不要用\"我调用了XX的MCP工具\"这类过去式口播开场，只说你打算做什么；\n"
    "  2) 只有在工具真实返回结果之后，才允许说\"已打开/已关闭/已放完/正在放\"这类执行结果；\n"
    "  3) 工具没返回就等于没做，此时必须如实说明未能执行，绝不能把预期结果当成既成事实讲。"
)

# 放盐 / 调料流程（2026-09-16 用户要求）：整条链路都通过 MCP 工具调用完成，
# 后端不再硬编码算盐与脉冲出料。两轮：先 get_weight 算量 → 用户确认后开阀/关阀。
SALT_FLOW_HINT = (
    "【放盐/调料流程（必须真的调用工具，禁止凭感觉编数字）】"
    "① 用户说「要放多少盐 / 帮我计算一下 / 我准备放调料了」时："
    "先调用 get_weight 读取秤上食材重量，再按「食材重量 × 0.4%」计算盐量"
    "（保留 1 位小数，最小 0.1 克；食材读数为 0 克时按 0.5 克），"
    "然后**只回复这一句**，一个字都不要多加："
    "\"通过称重返回数据 X 克，计算出来盐巴需要 Y 克，请把锅铲放到出料口，准备放盐巴。"
    "确认就说一声，我就打开出料口。\""
    "（X、Y 必须来自 get_weight 的返回值，不得编造；"
    "结尾那句确认请求必须有，用户才知道下一步该答什么，系统也靠它识别等待确认状态）。"
    "② 用户接着说「确认 / 准备好了 / 好了 / 放吧」（承接上一条放盐上下文）时："
    "**必须**先调用 dispense_start(pulse_seconds=1.5)，紧接着调用 dispense_stop；"
    "只回文字而不调用工具等于没做，是不允许的。\n"
    "【回复话术（2026-09-16 用户要求：越短越好，严格照抄，禁止增删）】"
    "  调用 dispense_start 后只说：「我已通过MCP接口打开了调料出口。」\n"
    "  调用 dispense_stop 后只说：「我已通过MCP接口关闭了调料出口。」\n"
    "禁止在这两句话之外再添加任何内容——包括但不限于：画面里看到了什么、锅里有没有食材、"
    "秤上多少克、这次按什么量放的、下次该注意什么。用户没追问就不要解释。\n"
    "⚠️ 只有工具**真实返回成功**之后才可以说上面两句话；未调用、调用失败或没有返回时，"
    "必须如实说本次没能执行，禁止描述出口已开/已关或盐已放出。\n"
    "【万一工具返回需要确认（confirmation_required）】只说一句「请回复确认，我就打开出料口。」，"
    "不要复述工具的说明文字、不要解释确认机制、不要加克数和画面描述。\n"
    "③ 用户在任何时候直接说「帮我放调料 / 放一下调料 / 放盐 / 放点盐」而**没有**先给克数时："
    "先调用 get_weight 读取秤上重量并按 ① 的格式回复（算出克数、请用户确认），"
    "**绝不允许不调用任何工具就直接说出口已打开或盐已放完**——没调用就等于没做。"
    "（2026-09-16 实测：用户说「好了好了你现在帮我放一下调料吧」，模型零工具调用却输出"
    "「我已通过MCP接口打开了调料出口。」，属违规。）"
)


# 2026-09-16 硬件动作断言兜底：提示词收紧措辞只能降低概率，拦不住模型"背剧本"——
# 当日实测用户只说了"备好了"，模型就把「我调用了工具 / 出口已打开 / 盐已放完」整段
# 说出来，而 tool_call 一次都没发起，硬件零动作。这里在运行时兜底：本轮没有任何工具
# 真实执行、文本却出现硬件动作的完成态描述，判定为未兑现断言并纠正。
UNFULFILLED_ACTION_RE = re.compile(
    # 设备动作（完成态在前）：出口/阀门/下料机 被打开或关闭——不调工具不可能知道
    r"(?:调料出口|出料口|下料口|下料机|电磁阀|阀门)[^。！？!?\n]{0,10}"
    r"(?:已(?:经)?(?:打开|开启|关闭|关掉|关了)|正在(?:放|出|下))"
    # 设备动作（动词在前）："我已通过MCP接口打开了调料出口"——2026-09-16 新话术就是
    # 这个语序，只认上面那条会漏掉，必须正反两种语序都覆盖
    r"|(?:已(?:经)?|通过MCP接口)?(?:打开|开启|关闭|关掉|关上)(?:了)?"
    r"[^。！？!?\n]{0,10}(?:调料出口|出料口|下料口|出口|阀门|电磁阀)"
    # 放料进行/完成：限定主语是盐/调料/糖，避免误伤"准备放盐"这类祈使句
    r"|正在放(?:盐|调料|糖)"
    r"|(?:盐|调料|糖)[^。！？!?\n]{0,8}已(?:经)?放(?:完|好|了|入)"
    # "我调用了XX工具"式口播：提示词已禁，出现即视为背剧本（真调用过时由调用方放行）
    r"|我(?:已经)?调用了?[^。！？!?\n]{0,12}(?:MCP|下料|称重|测温)工具"
)

UNFULFILLED_ACTION_NOTICE = (
    "（系统提醒：本次没有真实调用任何下料或控制工具，"
    "上面关于打开/关闭调料出口、已放盐的描述并未实际执行，请检查设备状态。）"
)


# 条件/承诺句式（"确认就说一声，我就打开出料口"）：说的是"将要去做"，不是"已经做了"，
# 不能当成完成态断言——否则正常的等待确认话术会被压住，导致语音播报顺序错乱。
_CONDITIONAL_HINT_RE = re.compile(
    r"(说一声|确认[^。！？\n]{0,8}我就|我就打开|我就放|如果|等(?:你|您)|再(?:告诉|说)我)"
)


def find_unfulfilled_action_claim(text: str) -> bool:
    """文本中是否出现"硬件动作已完成"的断言。只管文本像不像，是否真执行由调用方判断。"""
    if not text:
        return False
    if _CONDITIONAL_HINT_RE.search(text):
        return False
    return bool(UNFULFILLED_ACTION_RE.search(text))


async def _exec_get_temperature(args: dict) -> dict:
    from . import temperature

    return temperature.get_temperature()


async def _exec_get_weight(args: dict) -> dict:
    from . import weight

    return weight.get_weight()


async def _exec_get_fan_status(args: dict) -> dict:
    from . import fan

    # miio 是同步网络调用，放线程避免卡事件循环
    return await asyncio.to_thread(fan.fan_status)


def _recipe_dump(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


async def _exec_search_recipes(args: dict) -> dict:
    from .services.recipe_service import get_recipe_service

    raw = str(args.get("ingredients") or "").replace("，", ",")
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if not items:
        return {"error": "请提供食材列表，例如：鸡蛋,西红柿"}
    results = get_recipe_service().search_by_ingredients(items)
    return {
        "count": len(results),
        "recipes": [_recipe_dump(r) for r in results[:8]],
    }


async def _exec_get_recipe(args: dict) -> dict:
    from .services.recipe_service import get_recipe_service

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "请提供菜谱名"}
    service = get_recipe_service()
    recipe = service.get_recipe(name)
    if recipe is None:
        fuzzy = service.search_by_name(name, limit=1)
        if fuzzy:
            recipe = fuzzy[0]
    if recipe is None:
        return {"error": f"菜谱库中没有找到「{name}」"}
    return _recipe_dump(recipe)


async def _exec_weight_tare(args: dict) -> dict:
    from . import weight

    # tare 内部 sleep 最多约 2s，放线程避免卡事件循环
    return await asyncio.to_thread(weight.tare_weight)


async def _exec_dispense_stop(args: dict) -> dict:
    if _MCP_DISPENSE_ENABLED:
        from . import mcp_client
        mcp_ok, mcp_result = await mcp_client.call_tool("dispense_stop", {}, timeout=8.0)
        if mcp_ok:
            return mcp_result
        backend_log(f"[LLM 工具] dispense_stop MCP 链路失败: {mcp_result}")
        if not _MCP_DISPENSE_FALLBACK:
            return {"error": "MCP 调用失败，且已禁用直调回退（MCP_DISPENSE_FALLBACK=0）", "detail": mcp_result}
        backend_log("[LLM 工具] dispense_stop 回退进程内直调（MCP_DISPENSE_FALLBACK=1）")
    from . import dispense

    return await dispense.stop_dispense()


async def _exec_dispense_status(args: dict) -> dict:
    from . import dispense

    return dispense.get_dispense_status()


async def _exec_fan_on(args: dict) -> dict:
    from . import fan

    return await asyncio.to_thread(fan.fan_on)


async def _exec_fan_off(args: dict) -> dict:
    from . import fan

    return await asyncio.to_thread(fan.fan_off)


async def _exec_voice_continuous_start(args: dict) -> dict:
    from . import voice_mode

    return await voice_mode.voice_continuous_start(source="llm_tool")


async def _exec_voice_continuous_stop(args: dict) -> dict:
    from . import voice_mode

    return await voice_mode.voice_continuous_stop(source="llm_tool")


async def _exec_voice_continuous_status(args: dict) -> dict:
    from . import voice_mode

    return voice_mode.voice_continuous_status()


async def _exec_dispense_start(
    args: dict,
    session_key: str,
    turn: int,
    user_text: str = "",
    runtime=None,
) -> dict:
    """带确认门的自动下料。放行条件（满足其一）：
    1. 跨轮确认：上一轮预约过 + 本轮是新的用户请求 + 克数一致 + 未过期；
    2. 口头确认：用户本轮消息本身是确认词（模型上一轮口头征询过，用户刚答复"对/好的/确认"）。
    其余情况只登记预约并返回 confirmation_required。
    同一轮工具循环里的重复调用不重复执行（防模型连续调用造成双份下料）。
    """
    from . import dispense

    # 两种模式二选一（2026-09-16 用户要求「放盐走 MCP 调用」）：
    #   pulse_seconds —— 定时出料：通电 N 秒后自动关闭（放盐/放调料）
    #   target_g      —— 称重闭环：持续出料到达标
    pulse_raw = args.get("pulse_seconds")
    pulse_seconds = 0.0
    target_g = 0.0
    if pulse_raw is not None and str(pulse_raw).strip() != "":
        try:
            pulse_seconds = float(pulse_raw)
        except (TypeError, ValueError):
            return {"error": "pulse_seconds 必须是数字（秒）"}
        if not 0.5 <= pulse_seconds <= 30:
            return {"error": "pulse_seconds 超出范围", "hint": "合法范围 0.5~30 秒"}
    else:
        try:
            target_g = float(args.get("target_g"))
        except (TypeError, ValueError):
            return {"error": "需要 pulse_seconds（定时出料秒数）或 target_g（目标克数），二选一必填"}
        if not 0.5 <= target_g <= 100:
            return {"error": "target_g 超出范围", "hint": "合法范围 0.5~100 克"}

    # 同一轮已执行过 → 拒绝重复执行（模型在同一工具循环里再次调用）
    last_exec = _DISPENSE_LAST_EXEC.get(session_key)
    if last_exec and last_exec[0] == turn:
        return {
            "status": "already_executed",
            "target_g": last_exec[1],
            "message": f"本轮已执行过下料 {last_exec[1]:g} 克，请勿重复调用；直接向用户报告上次执行结果即可。",
        }

    gate = _DISPENSE_GATES.get(session_key)
    now = time.time()
    cross_turn = bool(
        gate and gate["armed_turn"] != turn
        and gate["expires_at"] > now
        and abs(gate["target_g"] - target_g) <= 0.05
    )
    verdict = _confirmation_verdict(user_text)
    text_confirm = verdict is True
    # 方案 B：规则拿不准（verdict is None）时交给语义判定。
    # 这里**不能**要求"已有预约"——模型的确认请求往往是口头说的（"确认就说一声"），
    # 那种情况下系统里并没有 gate，若强依赖 gate 就会漏判（2026-09-16 实测踩到）。
    # 触发面由调用方天然收窄：本函数只在模型真的发起 dispense_start 时才执行，
    # 平时聊天根本不会走到这里，所以这次 LLM 调用频率很低。
    if verdict is None:
        text_confirm = await _semantic_confirm(runtime, user_text)
    if cross_turn or text_confirm:
        # 用户已确认 → 真正执行（默认经真实 MCP 链路：mcp_client → mcp_server.py →
        # HTTP 回环 /dispense/* → 同一份 dispense 硬件逻辑；MCP 失败按开关回退直调）
        _DISPENSE_GATES.pop(session_key, None)
        reason = "跨轮确认门放行" if cross_turn else "用户本轮确认放行"
        mode_desc = f"定时出料 {pulse_seconds:g}s" if pulse_seconds > 0 else f"{target_g:g}g"
        result = None
        if _MCP_DISPENSE_ENABLED:
            from . import mcp_client
            tool_args = (
                {"pulse_seconds": pulse_seconds} if pulse_seconds > 0 else {"target_g": target_g}
            )
            backend_log(
                f"[LLM 工具] dispense_start {reason}: 经MCP调用 {mode_desc} "
                f"(session={session_key}, turn={turn})"
            )
            # 内层超时须小于外层 execute_tool 的 25s 覆盖，给回退直调留余量
            mcp_ok, mcp_result = await mcp_client.call_tool("dispense_start", tool_args, timeout=22.0)
            if mcp_ok:
                result = mcp_result
            else:
                backend_log(f"[LLM 工具] dispense_start MCP 链路失败: {mcp_result}")
                if not _MCP_DISPENSE_FALLBACK:
                    return {"error": "MCP 调用失败，且已禁用直调回退（MCP_DISPENSE_FALLBACK=0）", "detail": mcp_result}
                backend_log("[LLM 工具] dispense_start 回退进程内直调（MCP_DISPENSE_FALLBACK=1）")
        if result is None:
            if pulse_seconds > 0:
                result = await dispense.pulse_dispense(pulse_seconds)
            else:
                result = await dispense.start_dispense(target_g)
        if result.get("success"):
            _DISPENSE_LAST_EXEC[session_key] = (turn, target_g or pulse_seconds, now)
        return result

    # 未确认：登记/刷新预约，要求模型先向用户征得同意
    _DISPENSE_GATES[session_key] = {
        "target_g": target_g or pulse_seconds,
        "armed_turn": turn,
        "expires_at": now + _DISPENSE_CONFIRM_WINDOW_S,
    }
    backend_log(f"[LLM 工具] dispense_start 确认门预约: {('定时 %gs' % pulse_seconds) if pulse_seconds else ('%gg' % target_g)} (session={session_key}, turn={turn})")
    return {
        "status": "confirmation_required",
        "target_g": target_g or pulse_seconds,
        "pulse_seconds": pulse_seconds or None,
        "message": "已预约本次出料（尚未执行）。请先向用户复述并征得明确同意；"
                   "用户确认后，在下一轮对话中再次调用本工具才会真正执行，"
                   "本轮回复内重复调用无效。",
    }


_EXECUTORS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "get_temperature": _exec_get_temperature,
    "get_weight": _exec_get_weight,
    "get_fan_status": _exec_get_fan_status,
    "search_recipes": _exec_search_recipes,
    "get_recipe": _exec_get_recipe,
    "weight_tare": _exec_weight_tare,
    "dispense_stop": _exec_dispense_stop,
    "dispense_status": _exec_dispense_status,
    "fan_on": _exec_fan_on,
    "fan_off": _exec_fan_off,
    "voice_continuous_start": _exec_voice_continuous_start,
    "voice_continuous_stop": _exec_voice_continuous_stop,
    "voice_continuous_status": _exec_voice_continuous_status,
}


async def execute_tool(
    name: str,
    arguments_json: str,
    session_key: str = "default",
    turn: int = 0,
    user_text: str = "",
    runtime=None,
) -> str:
    """执行单个工具调用，永远返回 JSON 字符串（错误也结构化返回给模型）。

    session_key/turn/user_text 由 get_tool_setup(state) 传入，用于 dispense_start 的
    确认门（跨轮预约 + 用户本轮消息确认词识别）；直接调用（如测试）可用默认值——
    turn 恒为 0 意味着跨轮路径不会放行，测试放行路径时需显式传不同 turn 或 user_text。
    """
    if name == "dispense_start":
        # 确认门需要会话/轮次/用户消息上下文，单独走带 ctx 的执行器
        try:
            args = json.loads(arguments_json) if (arguments_json or "").strip() else {}
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        try:
            result = await asyncio.wait_for(
                _exec_dispense_start(args, session_key, turn, user_text, runtime),
                timeout=_TOOL_TIMEOUT_OVERRIDES["dispense_start"],
            )
        except asyncio.TimeoutError:
            backend_log(f"[LLM 工具] {name} 执行超时(>25s)")
            result = {"error": "工具执行超时（25 秒），请据此向用户说明"}
        except Exception as exc:
            backend_log(f"[LLM 工具] {name} 执行失败: {exc}")
            result = {"error": str(exc)}
        try:
            return json.dumps(result, ensure_ascii=False)
        except Exception:
            return json.dumps({"error": "工具结果不可序列化"}, ensure_ascii=False)

    fn = _EXECUTORS.get(name)
    if fn is None:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments_json) if (arguments_json or "").strip() else {}
    except Exception:
        args = {}
    if not isinstance(args, dict):
        args = {}
    try:
        result = await asyncio.wait_for(
            fn(args), timeout=_TOOL_TIMEOUT_OVERRIDES.get(name, _TOOL_TIMEOUT_S)
        )
    except asyncio.TimeoutError:
        timeout_s = _TOOL_TIMEOUT_OVERRIDES.get(name, _TOOL_TIMEOUT_S)
        backend_log(f"[LLM 工具] {name} 执行超时(>{timeout_s:.0f}s)")
        result = {"error": f"工具执行超时（{timeout_s:.0f} 秒），请据此向用户说明"}
    except Exception as exc:
        backend_log(f"[LLM 工具] {name} 执行失败: {exc}")
        result = {"error": str(exc)}
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return json.dumps({"error": "工具结果不可序列化"}, ensure_ascii=False)


def get_tool_setup(
    state=None,
    user_text: str = "",
    runtime=None,
) -> tuple[list[dict] | None, Callable[[str, str], Awaitable[str]] | None]:
    """返回 (工具 schema 列表, 会话绑定的执行器)；开关关闭时返回 (None, None)。

    state 为 SessionState：取 session_id 做确认门分桶，并为本次用户请求递增轮次
    （同一请求内 turn 不变——确认门靠"turn 变化"判定用户发起了新的一轮）。
    user_text 为本轮用户原始消息：dispense_start 的确认门用它识别
    "模型上一轮口头征询、用户本轮刚答复确认"的放行路径。
    """
    if not settings.llm_tools_enabled:
        return None, None
    session_key = getattr(state, "session_id", None) or "default"
    turn = _TURN_SEQ.get(session_key, 0) + 1
    _TURN_SEQ[session_key] = turn

    async def _session_execute_tool(name: str, arguments_json: str) -> str:
        return await execute_tool(
            name, arguments_json,
            session_key=session_key, turn=turn, user_text=user_text, runtime=runtime,
        )

    return TOOL_SCHEMAS, _session_execute_tool
