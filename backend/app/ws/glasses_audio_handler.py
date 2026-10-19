"""
眼镜音频流处理模块

接收 Rokid 眼镜端的 PCM 音频流，执行 STT → LLM → TTS pipeline，
将流式 TTS PCM 推送回眼镜端播放，同时把对话事件转发给前端监听者。

协议：
眼镜→后端:
  {"type":"audio_chunk","pcm_b64":"...","seq":1}
  {"type":"audio_end"}
  {"type":"interrupt"}
  {"type":"ping"}

后端→眼镜（同时转发给前端监听者）:
  {"type":"stt_partial","payload":{"text":"正在识别..."}}
  {"type":"stt_final","payload":{"text":"..."}}
  {"type":"assistant_delta","payload":{"delta":"...","request_id":"..."}}
  {"type":"assistant_final","payload":{"text":"...","request_id":"..."}}
  {"type":"tts_pcm","payload":{"pcm_b64":"...","sample_rate":24000,"chunk_index":0,...}}
  {"type":"tts_pcm_end","payload":{"total_chunks":N,"sample_rate":24000,...}}
  {"type":"error","payload":{"message":"..."}}
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import traceback
from typing import Any, Dict, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from ..logger import backend_log
from ..session import SessionState
try:
    from ..stt_corrector import correct_stt_text
except ImportError:  # stt_corrector 为队友本地文件（尚未提交到仓库），缺失时 STT 纠错降级为原样返回
    def correct_stt_text(text: str) -> str:
        return text
from . import (
    AppRuntime,
    ws_send,
    apply_tts_update,
    get_tts_model,
    get_tts_voice,
    get_tts_api_key,
)

# 全局状态
_glasses_audio_ws: Optional[WebSocket] = None
_glasses_audio_connected: bool = False
_glasses_audio_state: Optional[SessionState] = None
_glasses_audio_runtime: Optional[Any] = None

# 前端监听者：接收眼镜端对话事件（让前端能显示眼镜端对话）
_audio_listeners: Dict[str, WebSocket] = {}


def register_audio_listener(session_id: str, ws: WebSocket) -> None:
    """注册一个前端会话以接收眼镜音频对话事件"""
    _audio_listeners[session_id] = ws
    backend_log(f"[眼镜音频] 注册前端监听者 {session_id}，当前: {len(_audio_listeners)}")


def unregister_audio_listener(session_id: str) -> None:
    _audio_listeners.pop(session_id, None)
    backend_log(f"[眼镜音频] 注销前端监听者 {session_id}，当前: {len(_audio_listeners)}")


def is_glasses_audio_connected() -> bool:
    return _glasses_audio_connected


def get_glasses_audio_ws() -> Optional[WebSocket]:
    """获取当前眼镜音频 WebSocket（供 TTS 转发使用）"""
    return _glasses_audio_ws if _glasses_audio_connected else None


async def _notify_listeners(event: dict) -> None:
    """转发事件给所有前端监听者"""
    dead = []
    for sid, ws in _audio_listeners.items():
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(sid)
                continue
            await ws.send_json(event)
        except Exception:
            dead.append(sid)
    for sid in dead:
        _audio_listeners.pop(sid, None)


class _ForwardingWebSocket:
    """
    包装眼镜端 WebSocket：LLM/TTS handler 发给本对象的消息
    会同时下发给眼镜端和转发给所有前端监听者。
    """

    def __init__(self, glasses_ws: WebSocket):
        self._glasses_ws = glasses_ws

    @property
    def client_state(self) -> WebSocketState:
        return self._glasses_ws.client_state

    async def send_json(self, data: dict) -> None:
        # 发给眼镜端
        try:
            if self._glasses_ws.client_state == WebSocketState.CONNECTED:
                await self._glasses_ws.send_json(data)
        except Exception as e:
            backend_log(f"[眼镜音频] 发送给眼镜端失败: {e}")
        # 转发给前端监听者
        await _notify_listeners(data)

    def __getattr__(self, name):
        return getattr(self._glasses_ws, name)


def _ensure_runtime_and_state() -> tuple[Any, SessionState]:
    """懒加载 runtime 和 state（首次调用时初始化）"""
    global _glasses_audio_runtime, _glasses_audio_state
    if _glasses_audio_runtime is None:
        _glasses_audio_runtime = AppRuntime()
    if _glasses_audio_state is None:
        from ..config import settings
        state = SessionState()
        state.tts_provider = settings.tts_provider
        state.tts_model = get_tts_model(settings.tts_provider)
        state.tts_voice = get_tts_voice(settings.tts_provider)
        state.tts_api_key = get_tts_api_key(settings.tts_provider)
        state.vision_enabled = False  # 眼镜音频模式不附带视觉
        state.camera_source = "glasses"
        _glasses_audio_state = state
    return _glasses_audio_runtime, _glasses_audio_state


async def handle_glasses_audio_ws(ws: WebSocket) -> None:
    """处理眼镜端音频 WebSocket 连接"""
    global _glasses_audio_ws, _glasses_audio_connected

    await ws.accept()
    _glasses_audio_ws = ws
    _glasses_audio_connected = True

    # 懒加载 runtime + state
    runtime, state = _ensure_runtime_and_state()

    # 用转发 ws 让 LLM/TTS 事件同时推送给眼镜端和前端
    forwarding_ws = _ForwardingWebSocket(ws)

    backend_log("[眼镜音频] 眼镜音频已连接")
    await _notify_listeners({
        "type": "glasses_audio_status",
        "payload": {"connected": True, "message": "眼镜音频已连接"}
    })

    # 音频累积缓冲（按 seq 拼接）
    audio_chunks: Dict[int, bytes] = {}

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "audio_chunk":
                    pcm_b64 = msg.get("pcm_b64", "")
                    seq = int(msg.get("seq", 0))
                    if pcm_b64 and seq > 0:
                        try:
                            pcm = base64.b64decode(pcm_b64)
                            audio_chunks[seq] = pcm
                        except Exception as exc:
                            backend_log(f"[眼镜音频] PCM 解码失败 seq={seq}: {exc}")

                elif msg_type == "audio_end":
                    await _process_audio_end(audio_chunks, runtime, state, forwarding_ws)
                    audio_chunks.clear()

                elif msg_type == "interrupt":
                    state.interrupted = True
                    if state.current_task and not state.current_task.done():
                        state.current_task.cancel()
                    await ws_send(forwarding_ws, "interrupted", {"message": "已打断"})

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

                else:
                    backend_log(f"[眼镜音频] 未知事件: {msg_type}")

            except json.JSONDecodeError:
                backend_log("[眼镜音频] 收到无效 JSON")

    except Exception as e:
        msg = str(e).lower()
        if "disconnect" not in msg and "close" not in msg:
            backend_log(f"[眼镜音频] 连接异常: {e}\n{traceback.format_exc()}")
    finally:
        _glasses_audio_ws = None
        _glasses_audio_connected = False
        backend_log("[眼镜音频] 眼镜音频已断开")
        await _notify_listeners({
            "type": "glasses_audio_status",
            "payload": {"connected": False, "message": "眼镜音频已断开"}
        })


async def _process_audio_end(
    audio_chunks: Dict[int, bytes],
    runtime: Any,
    state: SessionState,
    forwarding_ws: _ForwardingWebSocket,
) -> None:
    """处理 audio_end：STT → LLM → TTS pipeline"""
    if not audio_chunks:
        await ws_send(forwarding_ws, "stt_final", {"text": "（没有接收到音频数据）"})
        return

    sorted_keys = sorted(audio_chunks.keys())
    pcm = b"".join(audio_chunks[k] for k in sorted_keys)
    backend_log(f"[眼镜音频] 收到 audio_end，PCM 总长: {len(pcm)} bytes, chunks: {len(audio_chunks)}")

    # ── STT ──────────────────────────────────────────────
    if runtime.stt is None:
        await ws_send(forwarding_ws, "stt_final", {"text": "（未启用 STT）"})
        return

    state.interrupted = False
    await ws_send(forwarding_ws, "stt_partial", {"text": "正在识别..."})

    try:
        text = await runtime.stt.transcribe_pcm16(pcm, 16000)
        text = correct_stt_text((text or "").strip())
    except Exception as exc:
        backend_log(f"[眼镜音频] STT 失败: {exc}\n{traceback.format_exc()}")
        await ws_send(forwarding_ws, "error", {"message": f"STT 失败: {exc}"})
        return

    if not text:
        await ws_send(forwarding_ws, "stt_final", {"text": "（未识别到有效语音）"})
        return

    await ws_send(forwarding_ws, "stt_final", {"text": text})
    backend_log(f"[眼镜音频] STT 结果: {text}")

    # ── LLM + TTS pipeline ───────────────────────────────
    request_id = f"glasses-audio-{int(time.time() * 1000)}"
    try:
        from .llm_handler import handle_user_text
        await handle_user_text(
            runtime=runtime,
            ws=forwarding_ws,
            state=state,
            text=text,
            jpeg_b64="",  # 视觉走摄像头通道，音频通道不附带图像
            vision_requested=False,
            has_vision=False,
            request_id=request_id,
        )
    except asyncio.CancelledError:
        backend_log(f"[眼镜音频] LLM/TTS 被打断: {request_id}")
    except Exception as exc:
        backend_log(f"[眼镜音频] LLM/TTS 失败: {exc}\n{traceback.format_exc()}")
        await ws_send(forwarding_ws, "error", {"message": f"LLM/TTS 失败: {exc}"})
