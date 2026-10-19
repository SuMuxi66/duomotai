"""
设备控制 WebSocket 处理器模块

仅包含设备控制相关的 WebSocket 处理器（油烟检测、风扇等）。
配置管理处理器已拆分到 config_handlers.py 模块。
"""
from __future__ import annotations

from typing import Any

from fastapi import WebSocket

from .. import ws as ws_core
from ..session import SessionState
from .config_handlers import CONFIG_HANDLERS


async def control_oil_smoke_response(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理油烟检测用户响应"""
    from ..ws import backend_log
    from ..fan import fan_control
    
    response = payload.get("response", "")
    backend_log(f"[油烟检测] 收到用户响应: {response}")
    
    # 取消超时定时器
    if state.oil_smoke_timeout_timer and not state.oil_smoke_timeout_timer.done():
        state.oil_smoke_timeout_timer.cancel()
    
    state.oil_smoke_pending_response = False
    
    if response == "yes":
        # 用户同意打开排气扇
        try:
            fan_control("on")
            await ws_core.ws_send(ws, "fan_control", {"action": "on", "reason": "oil_smoke_user_confirmed"})
            await ws_core.ws_send(ws, "assistant_delta", {"delta": "已为您打开排气扇", "request_id": "oil_smoke_response"})
            await ws_core.ws_send(ws, "assistant_final", {"text": "已为您打开排气扇", "request_id": "oil_smoke_response"})
            from ..ws.tts_handler import generate_tts
            await generate_tts(runtime, ws, state, "已为您打开排气扇", log_prefix="油烟风扇播报")
            backend_log("[油烟检测] 用户同意，已打开排气扇")
        except Exception as e:
            backend_log(f"[油烟检测] 打开排气扇失败: {e}")
    else:
        # 用户拒绝或取消
        await ws_core.ws_send(ws, "assistant_delta", {"delta": "已取消打开排气扇", "request_id": "oil_smoke_response"})
        await ws_core.ws_send(ws, "assistant_final", {"text": "已取消打开排气扇", "request_id": "oil_smoke_response"})
        from ..ws.tts_handler import generate_tts
        await generate_tts(runtime, ws, state, "已取消打开排气扇", log_prefix="油烟风扇播报")
        backend_log("[油烟检测] 用户拒绝打开排气扇")


async def control_smoke_fan_response(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理烟雾警报风扇询问用户响应（与油烟检测同构）"""
    from ..ws import backend_log
    from ..fan import fan_control

    response = payload.get("response", "")
    backend_log(f"[烟雾警报] 收到风扇询问响应: {response}")

    # 取消超时定时器
    if state.smoke_timeout_timer and not state.smoke_timeout_timer.done():
        state.smoke_timeout_timer.cancel()

    state.smoke_pending_fan_response = False

    if response == "yes":
        try:
            fan_control("on")
            await ws_core.ws_send(ws, "fan_control", {"action": "on", "reason": "smoke_user_confirmed"})
            await ws_core.ws_send(ws, "assistant_delta", {"delta": "好的，已为您打开排气扇", "request_id": "smoke_fan_response"})
            await ws_core.ws_send(ws, "assistant_final", {"text": "好的，已为您打开排气扇", "request_id": "smoke_fan_response"})
            from ..ws.tts_handler import generate_tts
            await generate_tts(runtime, ws, state, "好的，已为您打开排气扇", log_prefix="烟雾风扇播报")
            backend_log("[烟雾警报] 用户同意，已打开排气扇")
        except Exception as e:
            backend_log(f"[烟雾警报] 打开排气扇失败: {e}")
    else:
        await ws_core.ws_send(ws, "assistant_delta", {"delta": "好的，暂不开启排气扇", "request_id": "smoke_fan_response"})
        await ws_core.ws_send(ws, "assistant_final", {"text": "好的，暂不开启排气扇", "request_id": "smoke_fan_response"})
        from ..ws.tts_handler import generate_tts
        await generate_tts(runtime, ws, state, "好的，暂不开启排气扇", log_prefix="烟雾风扇播报")
        backend_log("[烟雾警报] 用户拒绝打开排气扇")


# 设备控制处理器映射表
DEVICE_CONTROL_HANDLERS = {
    "oil_smoke_response": control_oil_smoke_response,
    "smoke_fan_response": control_smoke_fan_response,
}


# 合并后的所有控制事件处理器（设备控制 + 配置管理）
CONTROL_HANDLERS = {**CONFIG_HANDLERS, **DEVICE_CONTROL_HANDLERS}


async def handle_control_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """统一的控制事件分发入口"""
    action = payload.get("action", "")
    handler = CONTROL_HANDLERS.get(action)
    if handler is None:
        await ws_core.ws_send(ws, "error", {"message": f"Unknown control action: {action}"})
        return
    await handler(runtime, ws, state, payload)
