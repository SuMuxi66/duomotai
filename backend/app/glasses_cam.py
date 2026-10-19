"""
Rokid 眼镜摄像头桥接器

作用：接收眼镜端 CameraStreamManager 通过 WebSocket 发来的 JPEG 帧，
转发给所有活跃的 PC 前端会话，注入主 vision pipeline。
"""

import asyncio
import base64
import time
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .logger import backend_log
from .session import SessionState
# 感知安全增强：多帧时序聚合（降误报）+ 设备控制闭环
from .safety_aggregator import TemporalAggregator
from .device_controller import DeviceController, get_default_controller
from .hazard_rules import get_action


class GlassesCameraBridge:
    """
    单例：管理眼镜摄像头连接，并将帧桥接到活跃的 PC 前端会话。
    """

    def __init__(self):
        self._glasses_ws: Optional[WebSocket] = None
        self._latest_jpeg_b64: Optional[str] = None
        self._latest_timestamp: int = 0
        self._frame_count: int = 0
        self._connected: bool = False
        self._active_sessions: Dict[str, SessionState] = {}
        self._active_ws_clients: Dict[str, WebSocket] = {}
        self._active_runtimes: Dict[str, object] = {}
        self._lock = asyncio.Lock()
        # 感知安全增强：多帧时序聚合器 + 设备控制器
        # window/threshold 默认 5/3，可由 config.settings 覆盖（见 config.py）
        try:
            from .config import settings
            _win = getattr(settings, "temporal_window", 5)
            _thr = getattr(settings, "temporal_threshold", 3)
        except Exception:
            _win, _thr = 5, 3
        self._safety_aggregator: TemporalAggregator = TemporalAggregator(window=_win, threshold=_thr)
        self._device_controller: DeviceController = get_default_controller(dry_run=True)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def latest_frame(self) -> Optional[str]:
        return self._latest_jpeg_b64

    def register_session(self, session_id: str, state: SessionState, ws: WebSocket, runtime=None) -> None:
        self._active_sessions[session_id] = state
        self._active_ws_clients[session_id] = ws
        if runtime is not None:
            self._active_runtimes[session_id] = runtime
        backend_log(f"[GlassesCam] 注册会话 {session_id}，活跃: {len(self._active_sessions)}")

    def unregister_session(self, session_id: str) -> None:
        self._active_sessions.pop(session_id, None)
        self._active_ws_clients.pop(session_id, None)
        self._active_runtimes.pop(session_id, None)
        backend_log(f"[GlassesCam] 注销会话 {session_id}，活跃: {len(self._active_sessions)}")

    # ====================== 感知安全增强：多帧聚合 + 紧急处置闭环 ======================
    # 设计说明：
    # - aggregator 负责多帧时序投票（默认 5 帧窗口/3 帧阈值），per hazard_type 独立计数；
    #   仅当达到阈值时返回 True，否则抑制单帧抖动 → 降低误报。
    # - 冷却仍由现有 SessionState.last_danger_alert_time（vision_handler 30s）协同处理，
    #   聚合器不持有时间态，便于单测。
    # - 设备控制走 DeviceController，缺真实设备时自动降级为 simulated，不影响主流程。
    # 接入点：VLM 单帧判定产出 hazard_type/detected 后，调用 feed_safety_hazard；
    #   达阈值且过冷却时调用 dispatch_safety_action 触发设备闭环。
    def feed_safety_hazard(self, hazard_type: str, detected: bool) -> bool:
        """写入一帧危险判定，返回是否达到触发阈值（True=应告警）。

        Args:
            hazard_type: 见 hazard_rules.HAZARD_RULES（smoke/overflow/oil_fire/...）。
            detected: 该帧 VLM 是否检出该类危险。
        """
        return self._safety_aggregator.feed(hazard_type, detected)

    def dispatch_safety_action(self, hazard_type: str, device_config: dict | None = None) -> dict:
        """按 hazard_rules 路由设备动作（open_fan/emergency_shutdown/...）。

        缺真实设备时返回 simulated=True 的成功结果，保证逻辑可测、不中断主流程。
        """
        action = get_action(hazard_type)
        result = self._device_controller.control(action, device_config)
        backend_log(
            f"[GlassesCam] 安全处置 hazard={hazard_type} action={action} "
            f"success={result.get('success')} executed={result.get('executed')}"
        )
        return result

    def reset_safety_state(self, hazard_type: str | None = None) -> None:
        """场景切换/误报复位：清空某类或全部聚合缓冲。"""
        self._safety_aggregator.reset(hazard_type)

    async def handle_glasses_connection(self, ws: WebSocket) -> None:
        self._glasses_ws = ws
        await ws.accept()
        self._connected = True
        backend_log("[GlassesCam] Rokid 眼镜摄像头已连接")

        try:
            while True:
                msg = await ws.receive_json()
                typ = msg.get("type", "")
                if typ == "frame":
                    jpeg_b64 = msg.get("jpeg_b64", "")
                    if jpeg_b64:
                        self._frame_count += 1
                        self._latest_jpeg_b64 = jpeg_b64
                        self._latest_timestamp = int(time.time() * 1000)
                        if self._frame_count % 10 == 0:
                            backend_log(f"[GlassesCam] 已收到 {self._frame_count} 帧, 大小: {len(jpeg_b64)//1024}KB, 会话: {len(self._active_sessions)}")
                        await self._broadcast_to_sessions(jpeg_b64)
                else:
                    backend_log(f"[GlassesCam] 未知事件: {typ}")
        except WebSocketDisconnect as exc:
            backend_log(f"[GlassesCam] Rokid 眼镜断开: code={exc.code}")
        except Exception as exc:
            backend_log(f"[GlassesCam] 异常: {repr(exc)}")
        finally:
            self._connected = False
            self._glasses_ws = None
            await self._notify_disconnect()

    async def _broadcast_to_sessions(self, jpeg_b64: str) -> None:
        frame_id = self._frame_count
        timestamp = self._latest_timestamp
        for session_id, state in list(self._active_sessions.items()):
            ws = self._active_ws_clients.get(session_id)
            if ws is None:
                continue
            try:
                await ws.send_json({
                    "type": "glasses_preview",
                    "payload": {"jpeg_b64": jpeg_b64, "frame_id": frame_id, "timestamp": timestamp}
                })
                if state.vision_enabled:
                    if state.video_frame_queue.full():
                        try:
                            state.video_frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    runtime = self._active_runtimes.get(session_id)
                    await state.video_frame_queue.put({
                        "runtime": runtime, "ws": ws, "state": state,
                        "jpeg_b64": jpeg_b64, "frame_id": frame_id, "timestamp": timestamp,
                    })
                    if not state.video_frame_processing:
                        state.video_frame_processing = True
                        from .ws.vision_handler import process_video_frame_queue
                        asyncio.create_task(process_video_frame_queue(state))
            except Exception as exc:
                backend_log(f"[GlassesCam] 推送到 {session_id} 失败: {exc}")
                self._active_sessions.pop(session_id, None)
                self._active_ws_clients.pop(session_id, None)
                self._active_runtimes.pop(session_id, None)

    async def _notify_disconnect(self) -> None:
        for session_id, ws in list(self._active_ws_clients.items()):
            try:
                await ws.send_json({"type": "glasses_status", "payload": {"connected": False, "message": "Rokid 眼镜摄像头已断开"}})
            except Exception:
                self._active_sessions.pop(session_id, None)
                self._active_ws_clients.pop(session_id, None)
                self._active_runtimes.pop(session_id, None)


glasses_bridge = GlassesCameraBridge()
