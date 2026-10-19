"""
音频处理模块
负责 STT、音频流处理（唤醒词由香橙派端侧 KWS 承担，后端不参与）
"""
from __future__ import annotations

import asyncio
import base64
import time
import traceback
from typing import Any

from fastapi import WebSocket

from ..logger import backend_log
from ..session import SessionState
try:
    from ..stt_corrector import correct_stt_text
except ImportError:  # stt_corrector 为队友本地文件（尚未提交到仓库），缺失时 STT 纠错降级为原样返回
    def correct_stt_text(text: str) -> str:
        return text
from . import (
    ws_send,
    ensure_stt_runtime,
)


async def handle_audio_end(runtime: Any, ws: WebSocket, state: SessionState, last_seq: int) -> str | None:
    """处理音频结束事件（非流式）"""
    del last_seq
    if runtime.stt is None:
        await ws_send(ws, "stt_final", {"text": "（未启用 STT）"})
        return None

    pcm = b"".join(state.audio_chunks[key] for key in sorted(state.audio_chunks.keys()))
    state.audio_chunks.clear()
    try:
        text = await runtime.stt.transcribe_pcm16(pcm, 16000)
        text = correct_stt_text(text.strip()) or "（未识别到有效语音）"
        await ws_send(ws, "stt_final", {"text": text})
        return text
    except Exception as exc:
        # 不再下发致命 error 消息：通话/持续识别场景下，单次识别失败不应打断通话，
        # 改为在服务端记录完整错误，并下发一段“可被前端过滤”的良性 stt_final，
        # 让通话/持续识别继续等待用户下一次说话。
        backend_log(f"[STT 非流式] 语音识别失败: {exc}\n{traceback.format_exc()}")
        await ws_send(ws, "stt_final", {"text": "（未识别到有效语音）"})
        return None


def _stt_supports_streaming(runtime: Any) -> bool:
    checker = getattr(runtime.stt, "supports_streaming", None)
    return callable(checker) and bool(checker())


async def _feed_stream_session(runtime: Any, ws: WebSocket, state: SessionState, pcm_data: bytes) -> None:
    """把流式帧喂进 Fun-ASR 会话；首个帧负责建会话，partial 经回调实时下发。"""
    session = state.stt_stream
    if session is None:
        async def on_partial(text: str) -> None:
            await ws_send(ws, "stt_partial", {"text": correct_stt_text(text)})

        session = runtime.stt.create_stream(sample_rate=16000)
        try:
            await session.start(on_partial)
        except Exception as exc:
            backend_log(f"[STT 流式] 会话启动失败，本轮放弃云端流式: {exc}\n{traceback.format_exc()}")
            state.stt_stream = None
            return
        state.stt_stream = session
        asyncio.create_task(_stream_idle_watchdog(state, session))
    try:
        await session.feed(pcm_data)
    except Exception as exc:
        backend_log(f"[STT 流式] 喂帧失败，丢弃会话: {exc}")
        state.stt_stream = None


async def _stream_idle_watchdog(state: SessionState, session: Any) -> None:
    """前端异常退出导致永远等不到 audio_stream_end 时，按空闲时长回收会话。"""
    try:
        while state.stt_stream is session:
            await asyncio.sleep(3.0)
            if time.monotonic() - session.last_feed_monotonic > 15.0:
                if state.stt_stream is session:
                    state.stt_stream = None
                try:
                    await session.stop_and_collect(timeout=3.0)
                except Exception:
                    pass
                backend_log("[STT 流式] 会话空闲超时，已回收")
                return
    except asyncio.CancelledError:
        pass


async def handle_audio_stream(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理音频流数据块"""
    seq = int(payload.get("seq", 0))
    pcm_b64 = payload.get("pcm16_b64", "")
    stt_provider = payload.get("stt_provider", "browser")
    stt_model = payload.get("stt_model") or None
    if stt_model and ("Web Speech" in stt_model or "Speech API" in stt_model):
        stt_model = None
    stt_api_key = payload.get("stt_api_key")
    ensure_stt_runtime(runtime, state, stt_provider, stt_model, stt_api_key)

    if not pcm_b64:
        return

    try:
        pcm_data = base64.b64decode(pcm_b64)

        # 云端流式会话（2026-09-14 输入框云端优先显示）：stream=true 的帧实时喂
        # Fun-ASR 流式会话，partial 经回调实时下发，audio_stream_end 收口出 final；
        # 不走下方整段缓冲/伪 partial 路径。
        if payload.get("stream") and _stt_supports_streaming(runtime):
            await _feed_stream_session(runtime, ws, state, pcm_data)
            return

        # 后端降噪：noisereduce (谱门控) + 谱减法
        from ..audio_denoise import get_denoiser
        denoiser = get_denoiser(16000)
        if denoiser:
            pcm_data = denoiser.denoise(pcm_data)

        # 限制stream_audio_chunks大小，防止客户端不发送audio_stream_end导致无限增长
        MAX_STREAM_AUDIO_CHUNKS = 200
        if len(state.stream_audio_chunks) > MAX_STREAM_AUDIO_CHUNKS:
            oldest_keys = sorted(state.stream_audio_chunks.keys())[:len(state.stream_audio_chunks) - MAX_STREAM_AUDIO_CHUNKS + 1]
            for k in oldest_keys:
                del state.stream_audio_chunks[k]
            backend_log(f"[音频流] 缓冲区超过{MAX_STREAM_AUDIO_CHUNKS}，已清理旧数据")
        state.stream_audio_chunks[seq] = pcm_data
        state.stream_audio_seq = max(state.stream_audio_seq, seq)

        if runtime.stt is not None and seq > 0 and seq % 5 == 0:
            sorted_keys = sorted(state.stream_audio_chunks.keys())
            pcm = b"".join(state.stream_audio_chunks[key] for key in sorted_keys)
            try:
                text = await runtime.stt.transcribe_pcm16(pcm, 16000)
                if text.strip():
                    await ws_send(ws, "stt_partial", {"text": correct_stt_text(text.strip())})
            except Exception:
                pass
    except Exception as exc:
        await ws_send(ws, "error", {"message": f"音频解码失败: {exc}\n{traceback.format_exc()}"})


async def handle_audio_stream_end(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> str | None:
    """处理音频流结束事件"""
    stt_provider = payload.get("stt_provider", "browser")
    stt_model = payload.get("stt_model") or None
    if stt_model and ("Web Speech" in stt_model or "Speech API" in stt_model):
        stt_model = None
    stt_api_key = payload.get("stt_api_key")
    ensure_stt_runtime(runtime, state, stt_provider, stt_model, stt_api_key)

    # 云端流式会话收口：音频已在说话期间实时上行，这里只等会话尾部结果出 final
    session = state.stt_stream
    if session is not None:
        state.stt_stream = None
        try:
            text = await session.stop_and_collect(timeout=6.0)
        except Exception as exc:
            backend_log(f"[STT 流式] 收口异常: {exc}\n{traceback.format_exc()}")
            text = ""
        text = correct_stt_text((text or "").strip())
        if text:
            await ws_send(ws, "stt_final", {"text": text})
            return text
        await ws_send(ws, "stt_final", {"text": "（未识别到有效语音）"})
        return None

    if runtime.stt is None:
        await ws_send(ws, "stt_final", {"text": "（未启用 STT）"})
        state.stream_audio_chunks.clear()
        return None

    if not state.stream_audio_chunks:
        await ws_send(ws, "stt_final", {"text": "（没有接收到音频数据）"})
        return None

    sorted_keys = sorted(state.stream_audio_chunks.keys())
    pcm = b"".join(state.stream_audio_chunks[key] for key in sorted_keys)
    state.stream_audio_chunks.clear()
    state.stream_audio_seq = 0

    try:
        await ws_send(ws, "stt_partial", {"text": "正在识别..."})
        text = await runtime.stt.transcribe_pcm16(pcm, 16000)
        text = correct_stt_text(text.strip()) or "（未识别到有效语音）"
        await ws_send(ws, "stt_final", {"text": text})
        return text
    except Exception as exc:
        # 单句识别失败不再发送致命 error（避免前端刷"错误"系统消息 / 误挂断通话），
        # 改为服务端记录 + 返回可被前端过滤的占位结果，让通话/持续识别会话存活。
        backend_log(f"[STT] 单句识别失败（已容错，不中断会话）: {exc}")
        await ws_send(ws, "stt_final", {"text": "（未识别到有效语音）"})
        return None


async def handle_interrupt(ws: WebSocket, state: SessionState) -> None:
    """处理打断事件"""
    state.interrupted = True
    if state.current_task and not state.current_task.done():
        state.current_task.cancel()
    await ws_send(ws, "interrupt_ack", {"message": "打断成功"})
    await ws_send(ws, "interrupted", {"message": "AI 生成已被打断"})
