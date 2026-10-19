"""
Cooking 工具调用协议（结构化 markdown 标记版）

在不改造各 LLM Provider 的情况下，让 LLM 通过输出约定的工具标记
`[COOKING:ACTION:PARAM]` 来"调用"步骤式做饭引导工具，
后端解析后驱动 cooking_core / ws_handlers.cooking 的预设状态机，
由预设生成并推送每一步引导文本，避免 LLM 自由发挥而脱离预设。

约定：
  - [COOKING:START]                 开始引导 / 询问菜名
  - [COOKING:SELECT:菜名]           选定菜谱，进入份数确认
  - [COOKING:CONFIRM_STEP]          确认当前步骤完成，进入下一步
  - [COOKING:SKIP_STEP]             跳过当前步骤
  - [COOKING:EXIT]                  退出做饭引导

实现沿用了现有 `[FAN_ON]` 的"文本协议"先例（见 ws/llm_handler.py）。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from .logger import backend_log
from . import cooking_core

# LLM 输出中的工具标记（可能带前缀/后缀空格）
_TOOL_RE = re.compile(r"\[COOKING:([A-Z_]+)(?::([^\]]*))?\]", re.IGNORECASE)


def strip_cooking_tools(text: str) -> str:
    """移除文本中的所有 cooking 工具标记（用于前端展示与 TTS）"""
    if not text:
        return ""
    return _TOOL_RE.sub("", text)


def parse_cooking_tool(text: str) -> Optional[dict]:
    """解析文本中最后一个 cooking 工具标记。

    返回 {"action": "SELECT", "param": "番茄炒鸡蛋"} 或 None。
    约定 LLM 只在末尾输出一个标记，因此取最后一个解析结果。
    """
    if not text:
        return None
    matches = list(_TOOL_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    action = match.group(1).upper()
    param = (match.group(2) or "").strip()
    return {"action": action, "param": param}


# 追加到做饭场景 system prompt 末尾的工具协议说明
SYSTEM_GUIDE_EXT = (
    "\n\n【步骤式引导工具】当用户要求逐步做某道菜、或你判定信息已足够进入做饭引导、"
    "或用户确认完成当前步骤/想进入下一步/想跳过/想退出时，你必须通过工具标记让系统接管，"
    "**不要自行展开完整菜谱或烹饪步骤**（步骤内容由系统预设生成）。用法：\n"
    "- 开始引导 / 询问菜名：[COOKING:START]\n"
    "- 选定菜谱并进入引导（把菜名填入）：[COOKING:SELECT:菜名]\n"
    "- 用户已完成当前步骤 [COOKING:CONFIRM_STEP]\n"
    "- 跳过当前步骤 [COOKING:SKIP_STEP]\n"
    "- 退出做饭引导 [COOKING:EXIT]\n"
    "- 盯油温/油温到了提醒下食材 [COOKING:OIL_WATCH:目标温度]（温度不确定用 auto；停止用 [COOKING:OIL_WATCH:STOP]）\n"
    "- 讲到需要投放调料且给出了明确克数时，启动自动下料 [COOKING:DISPENSE:克数]（如 [COOKING:DISPENSE:3]；停止用 [COOKING:DISPENSE:STOP]）\n"
    "规则：工具标记必须放在回复的最开头（标记前不允许有任何文字）；"
    "标记输出后立即结束回复——引导语、询问菜名/食材/份数等全部由系统预设推送，"
    "你不要再提问，也不要输出任何其他内容，更不要把整道菜步骤一次讲完。"
)


async def dispatch_cooking_tool(runtime: Any, ws, state: Any, tool: dict, request_id: str | None = None) -> bool:
    """根据工具标记调用 cooking 预设状态机对应控制函数。

    由状态机通过 send_cooking_text_with_tts 推送预设引导文本。
    """
    from .ws_handlers import cooking as cooking_handlers

    action = (tool.get("action") or "").upper()
    param = tool.get("param") or ""

    if action == "START":
        await cooking_handlers.control_start_cooking(runtime, ws, state, {})
        return True

    if action == "SELECT":
        recipe_name = param.strip()
        if not recipe_name:
            await cooking_handlers.control_start_cooking(runtime, ws, state, {})
            return True
        from .services.recipe_service import get_recipe_service

        matches = get_recipe_service().search_by_name(recipe_name, limit=1)
        if matches:
            await cooking_handlers.control_select_recipe(
                runtime, ws, state, {"recipe_name": matches[0].name}
            )
        else:
            # 修复：此前误调用不存在的 cooking_handlers.send_cooking_text_with_tts
            # （ws_handlers.cooking 无此属性）→ AttributeError 被上游 try/except 静默吞掉，
            # 用户既看不到提示，工具也静默失效。改用 cooking_core 的正式出口。
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state,
                f"我暂时没找到「{recipe_name}」这道菜的菜谱，麻烦换个菜名，或者告诉我你手头有什么食材。",
                request_id=request_id,
            )
        return True

    if action == "CONFIRM_STEP":
        await cooking_handlers.control_confirm_step(runtime, ws, state, {})
        return True

    if action == "SKIP_STEP":
        await cooking_handlers.control_skip_step(runtime, ws, state, {})
        return True

    if action == "EXIT":
        await cooking_handlers.control_stop_cooking(runtime, ws, state, {})
        return True

    if action == "OIL_WATCH":
        from .oil_watch import resolve_target, start_oil_watch, stop_oil_watch
        if (param or "").strip().upper() in {"STOP", "取消", "停止"}:
            stop_oil_watch(state)
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state, "好的，已停止油温监测。", request_id=request_id,
            )
            return True
        # 先确认再启动：避免"已达标"的即时播报抢在确认语之前
        target = resolve_target(param)
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"好的，正在监测油温，达到 {target}°C 我会主动提醒你下食材。",
            request_id=request_id,
        )
        await start_oil_watch(runtime, ws, state, target)
        return True

    if action == "DISPENSE":
        from . import dispense as dispense_mod
        if (param or "").strip().upper() in {"STOP", "取消", "停止"}:
            await dispense_mod.stop_dispense()
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state, "好的，已停止下料。", request_id=request_id,
            )
            return True
        try:
            grams = float(re.sub(r"[^\d.]", "", param or ""))
        except ValueError:
            grams = 0.0
        if grams <= 0:
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state, "下料克数没听清，请再说一次要放多少克。",
                request_id=request_id,
            )
            return True
        try:
            # start_dispense 内部等串口就绪最长 15s——包一层超时，避免卡住回复链
            result = await asyncio.wait_for(dispense_mod.start_dispense(grams), timeout=6.0)
        except Exception as exc:
            backend_log(f"[cooking_tools] 下料启动失败: {exc}")
            result = {"success": False, "error": "下料机响应超时"}
        if result.get("success"):
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state,
                f"好的，开始自动下料 {grams:g} 克，够了我自动停。",
                request_id=request_id,
            )
        else:
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state,
                f"下料机没有就绪：{result.get('error') or '未知原因'}，请检查下料机连接。",
                request_id=request_id,
            )
        return True

    backend_log(f"[cooking_tools] 未知工具动作: {action}")
    return False