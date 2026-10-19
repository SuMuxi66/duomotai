"""
WebSocket 消息路由模块
负责事件分发和处理器注册
"""
from __future__ import annotations

import traceback
from typing import Any

from fastapi import WebSocket

from ..config import settings
from ..session import SessionState
from ..ws_handlers import control as control_handlers
from ..ws_handlers import cooking as cooking_handlers
from ..ws_handlers import media as media_handlers
from .llm_handler import _start_user_text_task, apply_hello_settings
from .tts_handler import generate_tts
from .. import runtime_providers as provider_runtime
from . import ws_send, backend_log, cooking_core


async def handle_hello_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理 hello 事件"""
    apply_hello_settings(runtime, state, payload)
    await ws_send(ws, "status", {
        "vision_enabled": state.vision_enabled,
        "vision_fps": state.vision_fps,
        "vlm_provider": state.vlm_provider or settings.vlm_provider,
        "llm_temperature": state.llm_temperature,
        "llm_max_tokens": state.llm_max_tokens,
        "llm_top_p": state.llm_top_p,
        "llm_sample_count": state.llm_sample_count,
        "llm_frequency_penalty": state.llm_frequency_penalty,
        "llm_response_format": state.llm_response_format,
        "llm_enable_thinking": state.llm_enable_thinking,
        "stt_provider": state.stt_provider or settings.stt_provider,
        "stt_model": state.stt_model or settings.stt_model,
        "tts_provider": state.tts_provider,
        "tts_model": state.tts_model,
    })
    if state.cooking_enabled:
        await cooking_core.send_cooking_status(ws, state)


async def handle_user_text_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理用户文本消息事件"""
    text = (payload.get("text") or "").strip()
    if not text:
        return
    vision_requested = bool(payload.get("vision_requested", False))
    has_vision = bool(payload.get("has_vision", bool(payload.get("jpeg_b64"))))
    custom_prompt = str(payload.get("custom_prompt") or "").strip()
    if custom_prompt:
        state.custom_prompt = custom_prompt
    _start_user_text_task(
        runtime, ws, state, text, payload.get("jpeg_b64", ""), vision_requested, has_vision,
        payload.get("temperature"), payload.get("weight"), payload.get("vlm_model"),
        payload.get("tts_model"), payload.get("tts_provider"), payload.get("tts_api_key"),
        payload.get("request_id"), payload.get("file_b64"),
    )


async def handle_tts_speak_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """前端请求播报任意文本（本地拦截指令等绕过 LLM 的场景）。

    前端本地指令（风扇开关等）为保零延迟直接执行并上屏，但绕过了 LLM 流式
    TTS 链路——小米等后端 TTS 供应商下会全程无声。前端执行完动作后发本事件，
    由后端按当前配置的 TTS 供应商与音色播报指定文本。
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        return
    voice = (
        provider_runtime.normalize_tts_voice(state.tts_provider, payload.get("voice"))
        or state.tts_voice
        or provider_runtime.resolve_tts_voice(state.tts_provider)
    )
    # 主动播报即新指令周期：清掉上一轮 barge-in 残留的打断标志。本地口令不经
    # user_text 上行、置位后无人复位 interrupted，不清会在 generate_tts 入口被静默跳过。
    state.interrupted = False
    try:
        await generate_tts(
            runtime, ws, state, text,
            provider=state.tts_provider, model=state.tts_model, api_key=state.tts_api_key,
            log_prefix="本地播报", voice_override=voice,
        )
    except Exception as exc:
        backend_log(f"[本地播报] 失败: {exc}\n{traceback.format_exc()}")


async def handle_tts_test_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理 TTS 测试事件"""
    text = payload.get("text", "你好，这是语音合成测试。")
    # 兜底音色不能硬编码硅基流动的（对 xiaomi 非法 → API 400、测试播报全灭）：
    # 前端上送 voice 先按 provider 白名单清洗，为空再逐级落 state / 该 provider 默认
    voice = (
        provider_runtime.normalize_tts_voice(state.tts_provider, payload.get("voice"))
        or state.tts_voice
        or provider_runtime.resolve_tts_voice(state.tts_provider)
    )
    # 同 tts_speak：主动播报即新指令周期，清掉上一轮 barge-in 残留的打断标志，
    # 否则设置面板的测试播报会被上一轮打断残留静默吞掉。
    state.interrupted = False
    try:
        await generate_tts(runtime, ws, state, text, state.tts_provider, state.tts_model, state.tts_api_key, "TTS测试", voice)
    except Exception as exc:
        backend_log(f"[TTS测试] 失败: {exc}\n{traceback.format_exc()}")


async def handle_timer_tts_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理倒计时 TTS 播报请求"""
    text = payload.get("text", "倒计时结束")
    # voice 同样先按 provider 白名单清洗（前端上送历史脏值会让小米 API 400）
    voice = (
        provider_runtime.normalize_tts_voice(state.tts_provider, payload.get("voice"))
        or state.tts_voice
        or provider_runtime.resolve_tts_voice(state.tts_provider)
    )
    # 同 tts_speak：主动播报即新指令周期，清掉上一轮 barge-in 残留的打断标志，
    # 否则倒计时到点播报会被上一轮打断残留静默吞掉。
    state.interrupted = False
    try:
        await generate_tts(
            runtime, ws, state, text,
            provider=state.tts_provider, model=state.tts_model, api_key=state.tts_api_key,
            log_prefix="倒计时TTS", voice_override=voice,
        )
    except Exception as exc:
        backend_log(f"[倒计时TTS] 失败: {exc}\n{traceback.format_exc()}")


async def handle_interrupt_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理打断事件"""
    from .audio_handler import handle_interrupt
    await handle_interrupt(ws, state)


async def handle_unknown_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """处理未知事件类型"""
    del runtime, state
    await ws_send(ws, "error", {"message": f"Unknown event type: {payload.get('__event_type__', '')}"})


# 事件处理器映射表
EVENT_HANDLERS = {
    "hello": handle_hello_event,
    "user_text": handle_user_text_event,
    "audio_chunk": media_handlers.handle_audio_chunk_event,
    "audio_end": media_handlers.handle_audio_end_event,
    "audio_stream": media_handlers.handle_audio_stream_event,
    "audio_stream_end": media_handlers.handle_audio_stream_end_event,
    "interrupt": handle_interrupt_event,
    "control": control_handlers.handle_control_event,
    "tts_test": handle_tts_test_event,
    "tts_speak": handle_tts_speak_event,
    "timer_tts": handle_timer_tts_event,
    "video_frame": media_handlers.handle_video_frame_event,
    "cooking": cooking_handlers.handle_cooking_event,
}
