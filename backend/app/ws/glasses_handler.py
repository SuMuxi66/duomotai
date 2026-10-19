"""
眼镜摄像头流处理模块
接收 Rokid 眼镜端摄像头画面并转发到前端视觉分析管道
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any, Dict, Set

from fastapi import WebSocket

from ..logger import backend_log
from ..session import SessionState

# 全局变量：存储最新的眼镜摄像头帧
latest_glasses_frame: str | None = None
latest_glasses_frame_time: float = 0
_glasses_ws: WebSocket | None = None
_frame_counter: int = 0

# 活跃的 glasses 模式会话（用于广播预览帧）
_glasses_listeners: Dict[str, WebSocket] = {}
_glasses_listener_states: Dict[str, SessionState] = {}
_glasses_listener_runtimes: Dict[str, Any] = {}


def register_glasses_listener(session_id: str, ws: WebSocket, state: SessionState, runtime: Any = None) -> None:
    """注册一个前端会话以接收眼镜预览帧"""
    _glasses_listeners[session_id] = ws
    _glasses_listener_states[session_id] = state
    if runtime is not None:
        _glasses_listener_runtimes[session_id] = runtime
    backend_log(f"[眼镜摄像头] 注册监听者 {session_id}，当前监听者数: {len(_glasses_listeners)}")


def unregister_glasses_listener(session_id: str) -> None:
    """注销一个前端会话"""
    _glasses_listeners.pop(session_id, None)
    _glasses_listener_states.pop(session_id, None)
    _glasses_listener_runtimes.pop(session_id, None)
    backend_log(f"[眼镜摄像头] 注销监听者 {session_id}，当前监听者数: {len(_glasses_listeners)}")


async def _broadcast_glasses_frame(jpeg_b64: str) -> None:
    """将眼镜帧广播给所有注册的前端会话"""
    global _frame_counter
    frame_id = _frame_counter
    timestamp = int(time.time() * 1000)

    for session_id, ws in list(_glasses_listeners.items()):
        state = _glasses_listener_states.get(session_id)
        try:
            # 发送预览帧到前端
            await ws.send_json({
                "type": "glasses_preview",
                "payload": {"jpeg_b64": jpeg_b64, "frame_id": frame_id, "timestamp": timestamp}
            })
            # 注入视觉分析管道
            if state and state.vision_enabled and state.camera_source == "glasses":
                from .vision_handler import handle_video_frame
                listener_runtime = _glasses_listener_runtimes.get(session_id)
                asyncio.create_task(
                    handle_video_frame(listener_runtime, ws, state, jpeg_b64, frame_id, timestamp)
                )
        except Exception:
            _glasses_listeners.pop(session_id, None)
            _glasses_listener_states.pop(session_id, None)


async def _notify_glasses_status(connected: bool) -> None:
    """通知所有监听者眼镜连接状态变化"""
    for session_id, ws in list(_glasses_listeners.items()):
        try:
            await ws.send_json({
                "type": "glasses_status",
                "payload": {"connected": connected}
            })
        except Exception:
            _glasses_listeners.pop(session_id, None)
            _glasses_listener_states.pop(session_id, None)


async def handle_glasses_ws(ws: WebSocket) -> None:
    """处理眼镜端 WebSocket 连接，接收摄像头帧"""
    global _glasses_ws, latest_glasses_frame, latest_glasses_frame_time, _frame_counter

    await ws.accept()
    _glasses_ws = ws
    backend_log("[眼镜摄像头] 眼镜已连接")
    await _notify_glasses_status(True)

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")
                
                if msg_type == "frame":
                    jpeg_b64 = msg.get("jpeg_b64", "")
                    if jpeg_b64:
                        _frame_counter += 1
                        latest_glasses_frame = jpeg_b64
                        latest_glasses_frame_time = time.time()
                        
                        await _broadcast_glasses_frame(jpeg_b64)
                        
                        if _frame_counter % 30 == 0:
                            backend_log(f"[眼镜摄像头] 已接收 {_frame_counter} 帧")
                            
                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})
                    
            except json.JSONDecodeError:
                backend_log("[眼镜摄像头] 收到无效 JSON")
                
    except Exception as e:
        if "disconnect" not in str(e).lower() and "close" not in str(e).lower():
            backend_log(f"[眼镜摄像头] 连接异常: {e}")
    finally:
        _glasses_ws = None
        backend_log("[眼镜摄像头] 眼镜已断开")
        await _notify_glasses_status(False)


async def feed_latest_frame_to_pipeline(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
) -> None:
    """将最新的眼镜摄像头帧注入前端视觉分析管道"""
    global latest_glasses_frame, latest_glasses_frame_time

    jpeg_b64 = latest_glasses_frame
    if not jpeg_b64:
        backend_log("[眼镜摄像头] 无可用帧")
        return

    if not state.vision_enabled:
        backend_log("[眼镜摄像头] 视觉分析未启用")
        return

    from .vision_handler import handle_video_frame

    frame_id = int(time.time() * 1000)
    timestamp = int(time.time() * 1000)

    asyncio.create_task(
        handle_video_frame(runtime, ws, state, jpeg_b64, frame_id, timestamp)
    )


def get_latest_frame() -> str | None:
    """获取最新的眼镜摄像头帧"""
    return latest_glasses_frame


def has_glasses_connected() -> bool:
    """眼镜是否已连接"""
    return _glasses_ws is not None
