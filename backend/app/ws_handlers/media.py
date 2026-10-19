from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import WebSocket

from .. import ws as ws_core
from ..session import SessionState
from ..ws import ws_send
try:
    from ..stt_corrector import correct_stt_text
except ImportError:  # stt_corrector 为队友本地文件（尚未提交到仓库），缺失时 STT 纠错降级为原样返回
    def correct_stt_text(text: str) -> str:
        return text


async def handle_audio_chunk_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    seq = int(payload.get("seq", 0))
    pcm_b64 = payload.get("pcm16_b64", "")
    if pcm_b64:
        state.audio_chunks[seq] = base64.b64decode(pcm_b64)

    # 中间结果异步转录：seq % 5 == 0 时发 partial，与流式路径同节奏
    if seq > 0 and seq % 5 == 0 and not state.stt_partial_busy:
        state.stt_partial_busy = True
        asyncio.create_task(_stt_partial_async(runtime, ws, state))


async def _stt_partial_async(runtime: Any, ws: WebSocket, state: SessionState) -> None:
    """异步转录当前累计音频并发 stt_partial，绝不阻塞音频块接收。"""
    try:
        if runtime.stt is None:
            return
        if not state.audio_chunks:
            return  # 音频已结束（handle_audio_end 清空了），防陈旧 partial
        pcm = b"".join(state.audio_chunks[key] for key in sorted(state.audio_chunks.keys()))
        text = await runtime.stt.transcribe_pcm16(pcm, 16000)
        if text.strip():
            await ws_send(ws, "stt_partial", {"text": correct_stt_text(text.strip())})
    except Exception:
        pass  # 与 handle_audio_stream 同策略，静默容错
    finally:
        state.stt_partial_busy = False


async def handle_audio_end_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    stt_text = await ws_core.handle_audio_end(runtime, ws, state, int(payload.get("last_seq", -1)))
    if stt_text:
        ws_core._start_user_text_task(
            runtime,
            ws,
            state,
            stt_text,
            "",
            False,
            False,
            None,
            None,
            None,
            state.tts_model,
            state.tts_provider,
            state.tts_api_key,
        )


async def handle_audio_stream_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    await ws_core.handle_audio_stream(runtime, ws, state, payload)


async def handle_audio_stream_end_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    stt_text = await ws_core.handle_audio_stream_end(runtime, ws, state, payload)
    # stt_only 模式：仅做语音识别返回文本，不自动提交到 LLM（用于唤醒词检测等场景）
    stt_only = payload.get("stt_only", False)
    if stt_text and not stt_only:
        ws_core._start_user_text_task(
            runtime,
            ws,
            state,
            stt_text,
            "",
            False,
            False,
            None,
            None,
            None,
            state.tts_model,
            state.tts_provider,
            state.tts_api_key,
        )


async def handle_interrupt_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    del runtime, payload
    await ws_core.handle_interrupt(ws, state)


async def handle_video_frame_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    from ..ws import backend_log
    jpeg_b64 = payload.get("jpeg_b64", "")
    frame_id = payload.get("frame_id", 0)
    timestamp = payload.get("timestamp", 0)
    backend_log(
        f"[危险检测] 收到帧 frame_id={frame_id}, vision_enabled={state.vision_enabled}, "
        f"danger_alert={state.danger_alert_enabled}, oil_smoke_alert={state.oil_smoke_alert_enabled}"
    )
    if jpeg_b64 and state.vision_enabled:
        asyncio.create_task(ws_core.handle_video_frame(runtime, ws, state, jpeg_b64, frame_id, timestamp))
    else:
        backend_log(f"[危险检测] 帧被跳过: jpeg_b64存在={bool(jpeg_b64)}, vision_enabled={state.vision_enabled}")
