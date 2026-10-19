# -*- coding: utf-8 -*-
"""语音模式（持续识别）远程控制。

"通过唤醒打开持续识别"原本只活在前端（useVoiceOrchestration 的语音指令拦截 +
「持续识别」手动按钮）。本模块把开启/关闭提升为后端可调用动作：向前端 WS 广播
`voice_mode` 指令，前端收到后调 setContinuousMode —— 与手动点按钮完全同路径
（reconcile 负责销毁唤醒词监听、起持续识别/通话会话）。

边界说明：持续识别的真实状态（麦克风、端侧 ASR 会话）在前端浏览器里，后端只保存
"最后一次下发的期望值"供 status 查询；前端不回执，delivered 表示有几个在线页面
收到指令。没有任何前端在线时指令仍会记录，但 applied=false，调用方应如实告知用户。
"""
from __future__ import annotations

import time

from .logger import backend_log

# 期望状态（全局单份：演示场景一个厨房一面屏；六前端共用 session 的现状见 Bug6）
_voice_state: dict = {
    "continuous": False,
    "source": "init",
    "updated_at": 0.0,
    "delivered": 0,
}

_VALID_SOURCES = {"api", "llm_tool", "mcp"}


async def _set_continuous(enabled: bool, source: str) -> dict:
    from .ws.connection import iter_session_connections
    from .ws import ws_send

    if source not in _VALID_SOURCES:
        source = "api"

    delivered = 0
    for session_id, (ws, _state) in iter_session_connections().items():
        try:
            await ws_send(ws, "voice_mode", {"continuous": bool(enabled), "source": source})
            delivered += 1
        except Exception as exc:
            backend_log(f"[语音模式] 推送失败 session={session_id}: {exc}")

    _voice_state.update({
        "continuous": bool(enabled),
        "source": source,
        "updated_at": time.time(),
        "delivered": delivered,
    })
    backend_log(f"[语音模式] {'开启' if enabled else '关闭'}持续识别 (source={source}, delivered={delivered})")

    result = {
        "success": True,
        "continuous": bool(enabled),
        "mode": "持续识别" if enabled else "唤醒词/待机",
        "source": source,
        "delivered": delivered,
        "applied": delivered > 0,
    }
    if delivered == 0:
        result["warning"] = "当前没有在线的前端页面，指令已记录但未送达；请先打开前端页面再试"
    return result


async def voice_continuous_start(source: str = "api") -> dict:
    """开启持续识别（免唤醒词持续收音，通话式对话）。"""
    return await _set_continuous(True, source)


async def voice_continuous_stop(source: str = "api") -> dict:
    """关闭持续识别/挂断通话。"""
    return await _set_continuous(False, source)


def voice_continuous_status() -> dict:
    """查询最后一次下发的语音模式与在线页面数。"""
    from .ws.connection import iter_session_connections

    online = len(iter_session_connections())
    return {
        "success": True,
        "continuous": _voice_state["continuous"],
        "mode": "持续识别" if _voice_state["continuous"] else "唤醒词/待机",
        "source": _voice_state["source"],
        "updated_at": _voice_state["updated_at"],
        "online_sessions": online,
        "note": "为最后一次下发的期望值；前端掉线重连后以本指令为准不自动恢复",
    }
