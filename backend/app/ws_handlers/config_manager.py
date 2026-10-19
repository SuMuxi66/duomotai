"""
统一配置管理接口模块

提供统一的 LLM 生成参数（temperature、max_tokens 等）的批量更新接口，
消除控制处理器中重复的 if 块。
"""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Any, Callable

from fastapi import WebSocket

from .. import ws as ws_core
from ..providers.registry import PROVIDER_REGISTRY
from ..session import SessionState
from .api_key_strategies import SETTINGS_KEY_FIELDS, persist_api_key


# 允许的推理服务域名白名单（防止 SSRF 探测内网；由 provider 注册表派生，与 /models 端点同一数据源）
ALLOWED_SERVICE_HOSTS: tuple[str, ...] = tuple(dict.fromkeys(
    spec.allowed_host for spec in PROVIDER_REGISTRY.values() if spec.allowed_host
))


def _is_safe_base_url(url: str) -> bool:
    """防止 SSRF：仅允许 https，且目标 host 非内网/环回地址或已知白名单域名"""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.hostname
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            if not any(host == d or host.endswith("." + d) for d in ALLOWED_SERVICE_HOSTS):
                return False
        return True
    except Exception:
        return False


# LLM 生成参数配置项类型
LLMConfigHandler = Callable[[SessionState, Any], None]


def _update_llm_temperature(state: SessionState, value: Any) -> None:
    state.llm_temperature = float(value)


def _update_llm_max_tokens(state: SessionState, value: Any) -> None:
    state.llm_max_tokens = int(value)


def _update_llm_top_p(state: SessionState, value: Any) -> None:
    state.llm_top_p = float(value)


def _update_llm_sample_count(state: SessionState, value: Any) -> None:
    state.llm_sample_count = max(1, int(value))


def _update_llm_frequency_penalty(state: SessionState, value: Any) -> None:
    state.llm_frequency_penalty = float(value)


def _update_llm_response_format(state: SessionState, value: Any) -> None:
    fmt = str(value).strip().lower()
    state.llm_response_format = fmt if fmt in {"md", "json"} else "md"


def _update_llm_enable_thinking(state: SessionState, value: Any) -> None:
    state.llm_enable_thinking = bool(value)


def _update_danger_alert_enabled(state: SessionState, value: Any) -> None:
    state.danger_alert_enabled = bool(value)


def _update_oil_smoke_alert_enabled(state: SessionState, value: Any) -> None:
    state.oil_smoke_alert_enabled = bool(value)


class ConfigManager:
    """统一的配置管理器

    通过配置项 → handler 的映射，集中管理状态字段的更新逻辑。
    支持批量更新和单一字段更新。
    """

    def __init__(self) -> None:
        # LLM 生成参数
        self._llm_handlers: dict[str, LLMConfigHandler] = {
            "llm_temperature": _update_llm_temperature,
            "llm_max_tokens": _update_llm_max_tokens,
            "llm_top_p": _update_llm_top_p,
            "llm_sample_count": _update_llm_sample_count,
            "llm_frequency_penalty": _update_llm_frequency_penalty,
            "llm_response_format": _update_llm_response_format,
            "llm_enable_thinking": _update_llm_enable_thinking,
        }
        # 告警配置
        self._alert_handlers: dict[str, LLMConfigHandler] = {
            "danger_alert_enabled": _update_danger_alert_enabled,
            "oil_smoke_alert_enabled": _update_oil_smoke_alert_enabled,
        }

    async def update_llm_generation(
        self, ws: WebSocket, state: SessionState, payload: dict
    ) -> None:
        """批量更新 LLM 生成参数

        Args:
            ws: WebSocket 连接
            state: 会话状态
            payload: 包含要更新的配置项及其值
        """
        updates: dict[str, Any] = {}
        for key, value in payload.items():
            handler = self._llm_handlers.get(key)
            if handler is not None:
                handler(state, value)
                updates[key] = self._get_state_value(state, key)
        if updates:
            await ws_core.ws_send(ws, "status", updates)

    async def update_alert_settings(
        self, ws: WebSocket, state: SessionState, payload: dict
    ) -> None:
        """批量更新告警配置

        Args:
            ws: WebSocket 连接
            state: 会话状态
            payload: 包含要更新的告警配置项
        """
        updates: dict[str, Any] = {}
        for key, value in payload.items():
            handler = self._alert_handlers.get(key)
            if handler is not None:
                handler(state, value)
                updates[key] = self._get_state_value(state, key)
        if updates:
            await ws_core.ws_send(ws, "status", updates)

    async def update_vision(
        self, ws: WebSocket, state: SessionState, enabled: bool, fps: float | None = None
    ) -> None:
        """更新视觉配置

        Args:
            ws: WebSocket 连接
            state: 会话状态
            enabled: 是否启用视觉
            fps: 帧率（可选）
        """
        state.vision_enabled = bool(enabled)
        if fps is not None:
            state.vision_fps = max(0.2, min(5.0, float(fps)))
        await ws_core.ws_send(
            ws,
            "status",
            {
                "vision_enabled": state.vision_enabled,
                "vision_fps": state.vision_fps,
            },
        )

    async def update_model(
        self,
        runtime: Any,
        ws: WebSocket,
        state: SessionState,
        vlm_model: str,
    ) -> None:
        """更新 VLM 模型

        Args:
            runtime: 应用运行时
            ws: WebSocket 连接
            state: 会话状态
            vlm_model: VLM 模型名（空字符串表示不修改）
        """
        if vlm_model:
            state.vlm_model = vlm_model

        # 前端 set_model 与 hello 路径一致：把模型选择落盘 .env.local，
        # 否则 /config 始终返回环境变量里的旧模型，刷新页面后选择会被覆盖回退。
        # key 不经此通道落盘（显式保存走 set_model_provider / 策略层 persist_api_key）
        if vlm_model:
            from ..ws.llm_handler import _persist_frontend_model_config
            _persist_frontend_model_config(
                state.vlm_provider or ws_core.settings.vlm_provider,
                vlm_model, "vlm",
            )

        ws_core.refresh_model_runtime(runtime, state)
        await ws_core.ws_send(
            ws,
            "status",
            {
                "vlm_model": state.vlm_model or "",
            },
        )

    async def update_stt(
        self,
        runtime: Any,
        ws: WebSocket,
        state: SessionState,
        provider: str,
        model: str,
        api_key: str,
    ) -> None:
        """更新 STT 配置

        Args:
            runtime: 应用运行时
            ws: WebSocket 连接
            state: 会话状态
            provider: STT provider
            model: STT 模型
            api_key: API Key
        """
        state.stt_provider = provider
        if model:
            state.stt_model = model
        if api_key:
            state.stt_api_key = api_key
            # key 落盘：存过一次即永久有效，重启后端从 .env.local 恢复
            key_field = SETTINGS_KEY_FIELDS.get(provider)
            if key_field:
                persist_api_key(key_field, api_key)
        ws_core.apply_stt_update(runtime, state, provider, state.stt_model, state.stt_api_key)
        await ws_core.ws_send(
            ws,
            "status",
            {
                "stt_provider": state.stt_provider,
                "stt_model": state.stt_model or "",
            },
        )

    async def update_tts(
        self,
        runtime: Any,
        ws: WebSocket,
        state: SessionState,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
        voice: str | None = None,
    ) -> None:
        """更新 TTS 配置

        Args:
            runtime: 应用运行时
            ws: WebSocket 连接
            state: 会话状态
            provider: TTS provider
            model: TTS 模型（可选，默认根据 provider 解析）
            api_key: API Key（可选）
            voice: 语音（可选）
        """
        resolved_voice = voice or ws_core.get_tts_voice(provider)
        # key 落盘：存过一次即永久有效，重启后端从 .env.local 恢复
        if api_key:
            key_field = SETTINGS_KEY_FIELDS.get(provider)
            if key_field:
                persist_api_key(key_field, api_key)
        ws_core.set_session_tts(
            state,
            provider,
            model or ws_core.get_tts_model(provider),
            api_key or ws_core.get_tts_api_key(provider),
            resolved_voice,
        )
        ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)
        await ws_core.ws_send(
            ws,
            "status",
            {
                "tts_provider": state.tts_provider,
                "tts_voice": state.tts_voice,
            },
        )

    async def update_model_provider(
        self,
        runtime: Any,
        ws: WebSocket,
        state: SessionState,
        vlm_provider: str,
        vlm_base_url: str | None = None,
        vlm_api_format: str | None = None,
        api_key: str | None = None,
        vlm_model: str | None = None,
    ) -> None:
        """更新 VLM provider

        Args:
            runtime: 应用运行时
            ws: WebSocket 连接
            state: 会话状态
            vlm_provider: VLM provider
            vlm_base_url: 自定义 VLM base_url（可选）
            vlm_api_format: 自定义 VLM API 格式（可选）
            api_key: 自定义 provider API Key（可选）
            vlm_model: 同帧切换的模型名（可选）——provider 与 model 原子更新，
                避免错位窗口内"新 provider + 旧模型"被常驻视觉分析调用
        """
        state.vlm_provider = vlm_provider or state.vlm_provider or ws_core.settings.vlm_provider
        if vlm_model:
            state.vlm_model = vlm_model
        # 先完成全部校验再落盘，避免非法 base_url 抛错时 provider 已写入 env
        if vlm_base_url is not None:
            if not _is_safe_base_url(vlm_base_url):
                raise ValueError(f"非法的 base_url（仅允许 https 且为已知服务域名）: {vlm_base_url}")
            state.vlm_base_url = vlm_base_url
        if vlm_api_format is not None:
            state.vlm_api_format = vlm_api_format
        # 持久化选中的 provider/模型与 API Key，刷新/重启后继续使用；值变化才写并重载
        if state.vlm_provider or api_key:
            from ..config import PROVIDER_ENV_PREFIX, persist_env_value, reload_settings
            prefix = PROVIDER_ENV_PREFIX.get(state.vlm_provider)
            if state.vlm_provider and persist_env_value("VLM_PROVIDER", state.vlm_provider):
                reload_settings()
            if vlm_model and prefix and persist_env_value(f"{prefix}_VLM_MODEL", vlm_model):
                reload_settings()
            if api_key:
                state.llm_api_key = api_key
                if prefix and persist_env_value(f"{prefix}_API_KEY", api_key):
                    reload_settings()

        ws_core.refresh_model_runtime(runtime, state)
        await ws_core.ws_send(
            ws,
            "status",
            {
                "vlm_provider": state.vlm_provider,
                "vlm_base_url": state.vlm_base_url,
                "vlm_api_format": state.vlm_api_format,
            },
        )

    @staticmethod
    def _get_state_value(state: SessionState, key: str) -> Any:
        """从 state 中获取当前值（用于回写到 ws 状态消息）"""
        return getattr(state, key, None)


# 全局配置管理器实例
config_manager = ConfigManager()
