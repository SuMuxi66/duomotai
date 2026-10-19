"""
配置管理 WebSocket 处理器模块

包含所有 WebSocket 控制事件中的配置管理处理器。
使用策略模式和统一配置管理器，消除重复代码。
"""
from __future__ import annotations

from typing import Any

from fastapi import WebSocket

from ..logger import backend_log

from .. import ws as ws_core
from ..config import persist_env_value, reload_settings
from ..runtime_providers import normalize_tts_voice
from ..session import SessionState
from .api_key_strategies import ApiKeyStrategyFactory, SETTINGS_KEY_FIELDS, persist_api_key
from .config_manager import config_manager


# ============= 视觉与模型配置 =============


async def control_set_vision(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置视觉配置"""
    del runtime
    enabled = bool(payload.get("enabled", True))
    fps = payload.get("fps")
    if fps is not None:
        await config_manager.update_vision(ws, state, enabled, float(fps))
    else:
        await config_manager.update_vision(ws, state, enabled)


async def control_set_model(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 VLM 模型"""
    await config_manager.update_model(
        runtime,
        ws,
        state,
        payload.get("vlm_model", ""),
    )


async def control_set_model_provider(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 VLM provider

    前端发送的字段名为 base_url / api_format（通用名），
    同时兼容 vlm_base_url / vlm_api_format。
    vlm_model 可选同帧携带：provider 与模型原子切换，避免"新 provider + 旧 provider
    的模型名"错位 runtime 被 0.2fps 常驻视觉分析撞上（DashScope 端点收到 sensenova
    模型名 → 404 model_not_found 的根因）。
    """
    # 兼容前端 base_url 和后端 vlm_base_url
    vlm_base_url = payload.get("vlm_base_url") or payload.get("base_url")
    vlm_api_format = payload.get("vlm_api_format") or payload.get("api_format")

    await config_manager.update_model_provider(
        runtime,
        ws,
        state,
        payload.get("vlm_provider", ""),
        vlm_base_url,
        vlm_api_format,
        payload.get("api_key"),
        payload.get("vlm_model"),
    )


# ============= 语音识别（STT）配置 =============


async def control_set_stt(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 STT 配置（provider + model + api_key）"""
    await config_manager.update_stt(
        runtime,
        ws,
        state,
        payload.get("stt_provider", "browser"),
        payload.get("stt_model", ""),
        payload.get("stt_api_key", ""),
    )


async def control_set_stt_provider(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 STT provider"""
    stt_provider = payload.get("stt_provider", "browser")
    state.stt_provider = stt_provider
    # 持久化到 .env.local（复用 VLM _persist_frontend_model_config 底层的 persist_env_value/reload_settings
    # 机制），否则进程重启或前端刷新拉取 GET /config 时会被 env 默认值（browser）覆盖回旧选择
    if persist_env_value("STT_PROVIDER", stt_provider):
        reload_settings()  # 原地更新运行中 settings 实例，GET /config 立即返回新值
    # 前端确认时同帧带来的 key：落盘 + 更新会话（原先 key 只能等 hello 回写，存在异步脱节）
    payload_stt_key = (payload.get("stt_api_key") or "").strip()
    if payload_stt_key:
        key_field = SETTINGS_KEY_FIELDS.get(stt_provider)
        if key_field:
            persist_api_key(key_field, payload_stt_key)
        state.stt_api_key = payload_stt_key
    stt_model = state.stt_model or ws_core.settings.siliconflow_stt_model
    stt_api_key = state.stt_api_key or ws_core.settings.siliconflow_api_key
    ws_core.apply_stt_update(runtime, state, stt_provider, stt_model, stt_api_key)
    await ws_core.ws_send(ws, "status", {"stt_provider": state.stt_provider})


async def control_set_stt_model(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 STT 模型"""
    stt_model = payload.get("stt_model", "")
    if stt_model:
        state.stt_model = stt_model
        # 原样持久化（含 edge 的 "FunASR 流式（香橙派端侧）" 描述、browser 的 "Web Speech API"）；
        # GET /config 的 stt_model 优先返回该持久化值，前端刷新后 sttModel 不丢
        if persist_env_value("STT_MODEL", stt_model):
            reload_settings()
        effective_provider = state.stt_provider or ws_core.settings.stt_provider
        effective_api_key = state.stt_api_key or ws_core.settings.siliconflow_api_key
        ws_core.apply_stt_update(runtime, state, effective_provider, stt_model, effective_api_key)
    await ws_core.ws_send(ws, "status", {"stt_model": state.stt_model or ""})


# ============= 语音合成（TTS）配置 =============


async def control_set_tts(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 TTS 配置（provider + model + api_key + voice）"""
    provider = payload.get("tts_provider", "browser")
    model = payload.get("tts_model", "")
    api_key = payload.get("tts_api_key", "")
    # 前端上送音色先按 provider 白名单清洗（如小米对非法 voice 直接 400、
    # 整个 session 播报全灭）；None 时 update_tts 内部落该 provider 默认
    voice = normalize_tts_voice(provider, payload.get("tts_voice"))
    await config_manager.update_tts(runtime, ws, state, provider, model, api_key, voice)


async def control_set_tts_provider(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 TTS provider"""
    tts_provider = payload.get("tts_provider", "browser")
    tts_api_key = payload.get("tts_api_key")
    # key 落盘：面板存过一次即永久有效（重启后端从 .env.local 恢复，不再要求二次输入）
    if tts_api_key:
        key_field = SETTINGS_KEY_FIELDS.get(tts_provider)
        if key_field:
            persist_api_key(key_field, tts_api_key)
    tts_voice = payload.get("tts_voice") or ws_core.get_tts_voice(tts_provider)
    ws_core.set_session_tts(
        state,
        tts_provider,
        ws_core.get_tts_model(tts_provider),
        tts_api_key or ws_core.get_tts_api_key(tts_provider),
        tts_voice,
    )
    ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)
    await ws_core.ws_send(
        ws, "status", {"tts_provider": state.tts_provider, "tts_voice": state.tts_voice}
    )


async def control_set_tts_model(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 TTS 模型"""
    tts_model = payload.get("tts_model", "")
    if tts_model:
        ws_core.set_session_tts(state, state.tts_provider, tts_model, state.tts_api_key, state.tts_voice)
        ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)
    await ws_core.ws_send(ws, "status", {"tts_model": state.tts_model})


async def control_set_tts_voice(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 TTS 语音"""
    del runtime
    # 先按当前 provider 白名单清洗再落 state：非法历史脏值（如 default_zh）
    # 不透传，清洗为 None 时落该 provider 默认音色
    tts_voice = normalize_tts_voice(state.tts_provider, payload.get("tts_voice")) or ws_core.get_tts_voice(state.tts_provider)
    ws_core.set_session_tts(state, state.tts_provider, state.tts_model, state.tts_api_key, tts_voice)
    await ws_core.ws_send(ws, "status", {"tts_voice": state.tts_voice})


# ============= API Key 配置（使用策略模式）=============


async def control_set_api_key(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """设置 API Key（使用策略模式消除重复 if-elif 分支）"""
    provider = payload.get("provider", "")
    api_key = payload.get("api_key", "")

    # 通过策略工厂获取对应 provider 的策略并应用
    strategy = ApiKeyStrategyFactory.get_strategy(provider)
    strategy.apply(runtime, ws, state, api_key)

    await ws_core.ws_send(ws, "status", {"api_key_updated": True, "provider": provider})


# ============= LLM 生成参数配置 =============


async def control_set_llm_generation(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """批量更新 LLM 生成参数（使用统一配置管理器）"""
    del runtime
    await config_manager.update_llm_generation(ws, state, payload)


# ============= 告警配置 =============


async def control_set_alert_settings(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """批量更新告警配置（使用统一配置管理器）"""
    del runtime
    await config_manager.update_alert_settings(ws, state, payload)


# ============= 摄像头源切换 =============


async def control_set_camera_source(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """切换摄像头源：USB 或 Rokid 眼镜"""
    from ..ws.glasses_handler import register_glasses_listener, unregister_glasses_listener

    source = payload.get("source", "usb")
    state.camera_source = source
    session_id = str(id(ws))

    if source == "glasses":
        register_glasses_listener(session_id, ws, state, runtime)
        # 立即发送状态，告知前端眼镜连接状态
        from ..ws.glasses_handler import has_glasses_connected
        await ws_core.ws_send(ws, "status", {
            "camera_source": "glasses",
            "glasses_connected": has_glasses_connected(),
            "message": "已切换到 Rokid 眼镜摄像头",
        })
    else:
        unregister_glasses_listener(session_id)
        await ws_core.ws_send(ws, "status", {
            "camera_source": "usb",
            "message": "已切换到 USB 摄像头",
        })


async def control_set_audio_source(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """切换音频输出源：local 或 glasses"""
    source = payload.get("source", "local")
    state.audio_source = source
    backend_log(f"[音频源] 切换到 {source}")
    await ws_core.ws_send(ws, "status", {
        "audio_source": source,
        "message": f"已切换到 {'眼镜扬声器' if source == 'glasses' else '本地扬声器'}",
    })


# 配置管理处理器映射表
CONFIG_HANDLERS = {
    "set_vision": control_set_vision,
    "set_model": control_set_model,
    "set_model_provider": control_set_model_provider,
    "set_stt": control_set_stt,
    "set_stt_provider": control_set_stt_provider,
    "set_stt_model": control_set_stt_model,
    "set_tts": control_set_tts,
    "set_tts_provider": control_set_tts_provider,
    "set_tts_model": control_set_tts_model,
    "set_tts_voice": control_set_tts_voice,
    "set_api_key": control_set_api_key,
    "set_llm_generation": control_set_llm_generation,
    "set_alert_settings": control_set_alert_settings,
    "set_camera_source": control_set_camera_source,
    "set_audio_source": control_set_audio_source,
}
