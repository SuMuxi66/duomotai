"""
API Key 配置策略模块

使用策略模式消除 control_set_api_key 函数中的重复 if-elif 分支。
每个 provider 拥有独立的策略类，封装该 provider 的 API Key 设置逻辑。

key 生命周期（2026-09-06 起）：apply 时同时落盘 .env.local（幂等，值变化才写并
reload_settings）——面板里存过一次即永久有效，重启后端不丢、多台前端经
GET /config 回读自动同步，用户无需二次输入。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fastapi import WebSocket

from .. import ws as ws_core
from ..session import SessionState


def persist_api_key(settings_field: str, api_key: str) -> None:
    """把 API Key 持久化到 .env.local（幂等），值变化时 reload 内存 settings。

    settings_field 是 settings 字段名（如 "xiaomi_tts_api_key"），
    .env.local 键名即其大写（XIAOMI_TTS_API_KEY）。
    空值不落盘（persist_env_value 拒绝空串）——清除 key 暂不支持，需手改文件。
    """
    from ..config import persist_env_value, reload_settings

    if api_key and persist_env_value(settings_field.upper(), api_key):
        reload_settings()


# provider -> key 在 settings/.env.local 中的字段名。
# xiaomi 是 TTS 专用家（xiaomi_tts_api_key，不是 xiaomi_api_key）；
# dashscope 的 TTS 复用 DASHSCOPE_API_KEY 同一 env 值
# （settings.dashscope_tts_api_key 的 env 来源即 DASHSCOPE_API_KEY）。
SETTINGS_KEY_FIELDS: dict[str, str] = {
    "dashscope": "dashscope_api_key",
    "modelscope": "modelscope_api_key",
    "siliconflow": "siliconflow_api_key",
    "xiaomi": "xiaomi_tts_api_key",
    "qwen3_asr_realtime": "dashscope_api_key",  # STT 实时识别复用 DashScope key
}


class ApiKeyStrategy(ABC):
    """API Key 配置策略接口"""

    def __init__(self, settings_field: str):
        # 该 provider 的 key 在 settings/.env.local 中的字段名
        self.settings_field = settings_field

    @abstractmethod
    def apply(self, runtime: Any, ws: WebSocket, state: SessionState, api_key: str) -> None:
        """应用 API Key 配置

        Args:
            runtime: 应用运行时
            ws: WebSocket 连接
            state: 会话状态
            api_key: 要设置的 API Key
        """
        raise NotImplementedError


class DashScopeApiKeyStrategy(ApiKeyStrategy):
    """DashScope / ModelScope provider 的 API Key 策略

    同时影响 LLM 和 TTS（dashscope 复用 LLM 的 key）。
    """

    def apply(self, runtime: Any, ws: WebSocket, state: SessionState, api_key: str) -> None:
        state.llm_api_key = api_key
        persist_api_key(self.settings_field, api_key)
        ws_core.refresh_model_runtime(runtime, state)
        if state.tts_provider == "dashscope":
            ws_core.set_session_tts(
                state,
                "dashscope" if api_key else "browser",
                state.tts_model,
                api_key or None,
                state.tts_voice,
            )
            ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)


class SiliconFlowApiKeyStrategy(ApiKeyStrategy):
    """SiliconFlow provider 的 API Key 策略

    同时影响 STT 和 TTS（共用同一把 key）。
    """

    def apply(self, runtime: Any, ws: WebSocket, state: SessionState, api_key: str) -> None:
        state.stt_api_key = api_key
        persist_api_key(self.settings_field, api_key)
        effective_stt_provider = state.stt_provider or ws_core.settings.stt_provider
        if effective_stt_provider == "siliconflow":
            ws_core.apply_stt_update(runtime, state, "siliconflow", state.stt_model, api_key)
        if state.tts_provider == "siliconflow":
            ws_core.set_session_tts(
                state,
                "siliconflow" if api_key else "browser",
                state.tts_model,
                api_key or None,
                state.tts_voice,
            )
            ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)


class XiaomiApiKeyStrategy(ApiKeyStrategy):
    """Xiaomi provider 的 API Key 策略

    仅影响 TTS。
    """

    def apply(self, runtime: Any, ws: WebSocket, state: SessionState, api_key: str) -> None:
        ws_core.settings.xiaomi_tts_api_key = api_key
        persist_api_key(self.settings_field, api_key)
        if state.tts_provider == "xiaomi":
            ws_core.set_session_tts(
                state,
                "xiaomi" if api_key else "browser",
                state.tts_model,
                api_key or None,
                state.tts_voice,
            )
            ws_core.apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)


class DefaultApiKeyStrategy(ApiKeyStrategy):
    """默认策略：设置 API Key 到全局 settings 并落盘

    适用于仅需要在 settings 中存储 API Key 的简单 provider
    （deepseek、zhipu、sensenova 等）。
    """

    def apply(self, runtime: Any, ws: WebSocket, state: SessionState, api_key: str) -> None:
        setattr(ws_core.settings, self.settings_field, api_key)
        persist_api_key(self.settings_field, api_key)


class ApiKeyStrategyFactory:
    """API Key 策略工厂

    根据 provider 名称返回对应的策略实例（每次实例化并注入该家的
    settings 字段名）。未注册的 provider 返回 DefaultApiKeyStrategy。
    """

    _registry: dict[str, type[ApiKeyStrategy]] = {
        "dashscope": DashScopeApiKeyStrategy,
        "modelscope": DashScopeApiKeyStrategy,  # 复用同一策略类
        "siliconflow": SiliconFlowApiKeyStrategy,
        "xiaomi": XiaomiApiKeyStrategy,
    }

    @classmethod
    def get_strategy(cls, provider: str) -> ApiKeyStrategy:
        """根据 provider 名称获取对应策略实例（注入 settings 字段名）"""
        strategy_cls = cls._registry.get(provider)
        if strategy_cls is None:
            return DefaultApiKeyStrategy(f"{provider}_api_key")
        settings_field = SETTINGS_KEY_FIELDS.get(provider) or f"{provider}_api_key"
        return strategy_cls(settings_field)

    @classmethod
    def register(cls, provider: str, strategy_cls: type[ApiKeyStrategy]) -> None:
        """注册自定义 provider 策略类（运行时扩展用）"""
        cls._registry[provider] = strategy_cls
