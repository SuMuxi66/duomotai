from __future__ import annotations

from typing import Any

from .config import settings
from .logger import backend_log
from .providers.registry import GENERIC_OPENAI_SPEC, PROVIDER_REGISTRY, TTS_REGISTRY, get_vlm_spec
from .providers.unified import UnifiedOpenAIProvider
from .providers.vlm_mock import MockVLM
from .providers.local_cosyvoice_tts import LocalCosyVoiceTTS
from .providers.siliconflow_stt import SiliconFlowSTT
from .providers.siliconflow_tts import SiliconFlowTTS
from .providers.qwen3_asr_realtime import Qwen3ASRRealtime
from .providers.tts_failover import FailoverTTS
from .providers.tts_piper import DummyTTS
from .providers.xiaomi_tts import XiaomiTTS
from .session import SessionState


def create_dashscope_tts(api_key: str, model: str):
    from .providers.dashscope_tts import DashScopeTTS

    return DashScopeTTS(api_key=api_key, model=model)


def _get_custom_config(provider: str | None) -> dict | None:
    """从 settings 中提取自定义 provider 配置（如果 provider 是 custom:<id> 或 custom 格式）。"""
    if not provider or not provider.startswith("custom"):
        return None
    return getattr(settings, "custom_providers", {}).get(provider, {}) or {}


def _resolve_provider_format(
    provider: str | None,
    api_format: str | None,
) -> str:
    """将 custom / custom:<id> 等虚拟 provider 解析为实际 API 格式。"""
    if provider == "custom" or (provider and provider.startswith("custom:")):
        return api_format or "openai"
    return provider or "openai"


def build_vlm_runtime(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_format: str | None = None,
) -> Any:
    """按 provider 注册表构建统一 VLM runtime（文字与视觉同走 /chat/completions）。

    provider -> ProviderSpec（registry.py 数据）-> UnifiedOpenAIProvider。
    新增 OpenAI 兼容供应商只需在 registry 加一条记录，本函数与业务代码零改动。
    """
    effective_provider = provider or settings.vlm_provider
    effective_api_key = api_key
    effective_model = model
    effective_base_url = base_url
    effective_api_format = api_format

    custom_config = _get_custom_config(effective_provider)
    if custom_config:
        effective_api_key = effective_api_key or custom_config.get("api_key")
        effective_model = effective_model or custom_config.get("model")
        effective_base_url = effective_base_url or custom_config.get("base_url")
        effective_api_format = effective_api_format or custom_config.get("api_format", "openai")

    resolved_provider = _resolve_provider_format(effective_provider, effective_api_format)
    spec = get_vlm_spec(resolved_provider)
    if spec is None and resolved_provider == "openai":
        # custom provider 显式声明 openai 兼容格式
        spec = GENERIC_OPENAI_SPEC

    if spec is not None:
        effective_api_key = effective_api_key or getattr(settings, spec.key_env.lower(), None)
        if spec.model_env:
            effective_model = effective_model or getattr(settings, spec.model_env.lower(), None)
        effective_model = effective_model or spec.default_model
        effective_base_url = effective_base_url or spec.base_url
        if effective_api_key and effective_model:
            return UnifiedOpenAIProvider(
                spec,
                api_key=effective_api_key,
                model=effective_model,
                base_url=effective_base_url,
            )
        backend_log(f"[VLM] {spec.error_label or resolved_provider} provider 缺少 api_key 或 model，回退到 mock")
        return MockVLM()

    # 未注册格式（如 custom anthropic）：沿用历史落点，走 ModelScope 默认分支
    effective_api_key = effective_api_key or settings.modelscope_api_key
    effective_model = effective_model or settings.modelscope_vlm_model
    if effective_api_key:
        return UnifiedOpenAIProvider(
            PROVIDER_REGISTRY["modelscope"],
            api_key=effective_api_key,
            model=effective_model,
        )
    return MockVLM()


def build_stt_runtime(provider: str, model: str | None = None, api_key: str | None = None) -> Any:
    if provider == "browser":
        backend_log("[STT] 切换到浏览器内置 STT（前端处理）")
        return None

    if provider == "edge":
        # 端侧语音（香橙派）：唤醒词 KWS(:8767) 与流式 ASR(:8768) 均在前端直连端侧设备 WebSocket，
        # 完全离线，后端不做云端转写（runtime.stt=None，与 browser 等价）。
        # 仅当端侧 ASR 不可用且前端回退发来 audio_stream 时才会走到这里，此时返回"未启用 STT"。
        backend_log("[STT] 端侧语音（香橙派 KWS/ASR）模式，后端不做云端转写")
        return None

    if provider == "siliconflow":
        effective_api_key = api_key or settings.siliconflow_api_key
        effective_model = model or settings.siliconflow_stt_model
        if effective_api_key:
            backend_log(f"[STT] 切换到 SiliconFlow STT: {effective_model}")
            return SiliconFlowSTT(api_key=effective_api_key, model=effective_model)
        backend_log("[STT] SiliconFlow 未配置 API Key，回退到浏览器 STT")
        return None

    if provider == "fun_asr_realtime":
        # Fun-ASR Realtime（fun-asr-realtime-*，北京地域）：DashScope 自有 WS 协议，
        # 走 dashscope SDK 的 Recognition；与 qwen3_asr_realtime（OpenAI Realtime 兼容）不同通道。
        effective_api_key = api_key or settings.dashscope_api_key
        effective_model = model or settings.fun_asr_model
        if effective_api_key:
            from .providers.fun_asr_realtime import FunASRRealtime

            backend_log(f"[STT] 切换到 Fun-ASR Realtime: {effective_model}")
            return FunASRRealtime(api_key=effective_api_key, model=effective_model)
        backend_log("[STT] Fun-ASR 未配置 API Key，回退到浏览器 STT")
        return None

    if provider == "qwen3_asr_realtime":
        effective_api_key = api_key or settings.dashscope_api_key
        effective_model = model or "qwen3-asr-flash-realtime-2026-02-10"
        if effective_api_key:
            backend_log(f"[STT] 切换到 Qwen3 ASR Realtime: {effective_model}")
            return Qwen3ASRRealtime(api_key=effective_api_key, model=effective_model)
        backend_log("[STT] Qwen3 ASR 未配置 API Key，回退到浏览器 STT")
        return None

    if provider == "xinference":
        backend_log("[STT] xinference_stt 模块缺失，回退到浏览器 STT")
        return None

    if provider == "local_sensevoice":
        backend_log("[STT] local_sensevoice_stt 模块缺失，回退到浏览器 STT")
        return None

    if provider == "fasterwhisper":
        try:
            from .providers.stt_fasterwhisper import FasterWhisperSTT as LocalSTT
        except Exception as exc:
            backend_log(f"[STT] 本地 FasterWhisper 不可用，回退到浏览器 STT: {exc}")
            return None
        backend_log("[STT] 切换到本地 FasterWhisper STT")
        return LocalSTT()

    backend_log(f"[STT] 未知 provider: {provider}，回退到浏览器 STT")
    return None


def build_tts_runtime(provider: str, model: str | None = None, api_key: str | None = None) -> Any:
    """按 TTS 注册表构建 runtime；browser 或缺 key 回退 DummyTTS（浏览器端自行合成）。"""
    if provider == "browser":
        backend_log("[TTS] 切换到浏览器内置 TTS")
        return DummyTTS()

    spec = TTS_REGISTRY.get(provider)
    if spec is None:
        backend_log(f"[TTS] 未知 provider: {provider}，回退到浏览器 TTS")
        return DummyTTS()

    effective_api_key = api_key or (getattr(settings, spec.key_env, None) if spec.key_env else None)
    effective_model = model or getattr(settings, spec.model_env, None) or spec.default_model

    if provider == "local_cosyvoice":
        backend_log(f"[TTS] 切换到本地 CosyVoice TTS @ {settings.local_cosyvoice_url}")
        return LocalCosyVoiceTTS(server_url=settings.local_cosyvoice_url, voice=resolve_tts_voice(provider))

    if effective_api_key:
        factory = _TTS_FACTORIES.get(provider)
        if factory is not None:
            backend_log(f"[TTS] 切换到 {provider} TTS: {effective_model}")
            tts = factory(api_key=effective_api_key, model=effective_model)
            return _maybe_wrap_failover(provider, tts)

    backend_log(f"[TTS] {provider} 未配置 API Key，回退到浏览器 TTS")
    return DummyTTS()


def _maybe_wrap_failover(primary_provider: str, primary_tts: Any) -> Any:
    """给主 TTS 套一层备用线路（默认 xiaomi → dashscope，首块 5s 超时即切）。

    2026-09-16 用户要求「双线同时待命，小米挂了 5 秒内切 dash」。
    关闭方式：`TTS_FAILOVER_PROVIDER=` 设成空串。
    """
    backup = (getattr(settings, "tts_failover_provider", "") or "").strip().lower()
    if not backup or backup == primary_provider:
        return primary_tts
    spec = TTS_REGISTRY.get(backup)
    if spec is None or not spec.streaming:
        return primary_tts
    key = getattr(settings, spec.key_env, None) if spec.key_env else None
    if not key:
        backend_log(f"[TTS 双线] 备用 {backup} 未配置 API Key，保持单线路")
        return primary_tts
    fb_factory = _TTS_FACTORIES.get(backup)
    if fb_factory is None:
        return primary_tts
    fb_model = getattr(settings, spec.model_env, None) or spec.default_model
    try:
        fallback = fb_factory(api_key=key, model=fb_model)
    except Exception as e:
        backend_log(f"[TTS 双线] 备用 {backup} 构建失败，保持单线路: {type(e).__name__}: {e}")
        return primary_tts
    timeout = float(getattr(settings, "tts_failover_timeout", 5) or 5)
    backend_log(
        f"[TTS 双线] 已启用：主 {primary_provider}（首块 {timeout:.0f}s 未就绪即切）"
        f" → 备 {backup}:{fb_model}"
    )
    return FailoverTTS(
        primary_tts, fallback,
        primary_name=primary_provider, fallback_name=backup, first_chunk_timeout=timeout,
    )


# 各 TTS provider 的实现类工厂（签名统一 (api_key, model)；registry 保持纯数据，工厂放这里）
_TTS_FACTORIES: dict[str, Any] = {
    "siliconflow": lambda api_key, model: SiliconFlowTTS(api_key=api_key, model=model),
    "dashscope": lambda api_key, model: create_dashscope_tts(api_key, model),
    "xiaomi": lambda api_key, model: XiaomiTTS(api_key=api_key, model=model),
}


def can_stream_tts(provider: str | None, tts: Any) -> bool:
    """该 provider 的 TTS 是否可流式播报：注册表声明 streaming 且 runtime 实例具备 stream_pcm_chunks。

    业务侧原 `provider in {"dashscope","xiaomi"} and api_key and hasattr(runtime.tts, ...)`
    的散落硬编码判断由此收敛（新增流式 TTS 供应商只需在注册表标 streaming=True）。
    """
    spec = TTS_REGISTRY.get(provider or "")
    return bool(spec and spec.streaming and tts is not None and hasattr(tts, "stream_pcm_chunks"))


def resolve_tts_api_key(provider: str, api_key: str | None = None) -> str | None:
    if api_key:
        return api_key
    spec = TTS_REGISTRY.get(provider)
    if spec and spec.key_env:
        return getattr(settings, spec.key_env, None)
    return None


def resolve_tts_model(provider: str, model: str | None = None) -> str | None:
    if model:
        return model
    spec = TTS_REGISTRY.get(provider)
    if spec:
        return getattr(settings, spec.model_env, None) or spec.default_model
    return settings.tts_model


def resolve_tts_voice(provider: str) -> str:
    spec = TTS_REGISTRY.get(provider)
    if spec:
        return (getattr(settings, spec.voice_env, None) if spec.voice_env else None) or spec.default_voice
    return settings.siliconflow_tts_voice or "fnlp/MOSS-TTSD-v0.5:claire"


# 小米 MiMo TTS 官方音色表（2026-09 官方文档）。API 对非法 voice 直接 400，
# 而前端 localStorage 的历史脏值（default_zh/default_en 等旧占位名）会经
# hello / set_tts / tts_test 原样上送，导致该 session 播报全灭——入口统一清洗。
XIAOMI_TTS_VOICES = (
    "mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean",
)


def normalize_tts_voice(provider: str | None, voice: str | None) -> str | None:
    """按 provider 校验/清洗 voice：非法值回退该 provider 默认，空值取默认。

    只对有官方音色白名单的 provider（当前 xiaomi）强校验，其余 provider 的
    合法集合不在此维护、原样放行，避免误杀。
    """
    voice = (voice or "").strip()
    if provider == "xiaomi":
        if voice in XIAOMI_TTS_VOICES:
            return voice
        if voice:
            backend_log(f"[TTS] 非法音色 {voice!r}（provider=xiaomi），回退默认音色")
        return resolve_tts_voice("xiaomi")
    return voice or None


def resolve_stt_runtime_key(provider: str, model: str | None, api_key: str | None) -> tuple[str, str | None, str | None]:
    return provider, model, api_key


def set_session_tts(
    state: SessionState,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    voice: str | None = None,
) -> None:
    next_provider = provider or state.tts_provider or settings.tts_provider
    state.tts_provider = next_provider
    state.tts_model = resolve_tts_model(next_provider, model)
    state.tts_api_key = resolve_tts_api_key(next_provider, api_key)
    # voice 三级清洗：显式传入 > 旧值按新 provider 校验（换 provider 后旧音色
    # 可能非法，不能跨 provider 残留）> 都没有时取新 provider 默认
    if voice:
        state.tts_voice = normalize_tts_voice(next_provider, voice)
    else:
        state.tts_voice = normalize_tts_voice(next_provider, state.tts_voice) or resolve_tts_voice(next_provider)


def refresh_model_runtime(runtime: Any, state: SessionState | None = None) -> None:
    if state is not None:
        # 模型 id 与 key 采用相同的存储/解析模式：标准（注册表内）供应商的
        # provider/model 一律以 settings（.env.local 持久化值）为最高权威——
        # 前端 localStorage 里的旧/脏值（经 hello 写入 state）不再遮蔽 env 里
        # 更新的模型名。前端面板选择模型后经 set_model / set_model_provider
        # 落盘 env + reload_settings，刷新页面/重启后端继续使用该值。key 轮换
        # 同理（改 env 文件即全端生效）。custom provider 的 key/model 无 env 键位、
        # 仅存前端会话与 settings.custom_providers，仍从 state 取（与 key 一致）。
        vlm_provider = state.vlm_provider
        is_custom = bool(vlm_provider and str(vlm_provider).startswith("custom"))
        if is_custom:
            # custom：model/key/base_url/api_format 无 env 键位，保留 state 取值
            runtime.vlm = build_vlm_runtime(
                provider=vlm_provider,
                api_key=state.llm_api_key,
                model=state.vlm_model,
                base_url=getattr(state, "vlm_base_url", None),
                api_format=getattr(state, "vlm_api_format", None),
            )
        else:
            # 标准供应商：provider/model 不从 state 显式传入，让 build_vlm_runtime
            # 走 settings.<provider_spec> > settings.<model_env> > spec.default_model
            # 权威链。base_url/api_format 对标准供应商通常为 None（面板不发送），
            # 透传给 build 在为空时回退 spec.base_url。
            runtime.vlm = build_vlm_runtime(
                provider=None,
                api_key=None,
                model=None,
                base_url=getattr(state, "vlm_base_url", None),
                api_format=getattr(state, "vlm_api_format", None),
            )
    else:
        runtime.vlm = build_vlm_runtime()
    # LLM 已统一到 VLM 端点：runtime.llm 作为别名指向同一实例，保持向后兼容
    runtime.llm = runtime.vlm


def apply_stt_update(runtime: Any, state: SessionState, provider: str, model: str | None, api_key: str | None) -> None:
    runtime.stt = build_stt_runtime(provider, model, api_key)
    state.stt_runtime_key = resolve_stt_runtime_key(provider, model, api_key)


def ensure_stt_runtime(runtime: Any, state: SessionState, provider: str, model: str | None, api_key: str | None) -> None:
    runtime_key = resolve_stt_runtime_key(provider, model, api_key)
    if state.stt_runtime_key == runtime_key:
        return
    runtime.stt = build_stt_runtime(provider, model, api_key)
    state.stt_runtime_key = runtime_key


def apply_tts_update(runtime: Any, provider: str, model: str | None, api_key: str | None) -> None:
    runtime.tts = build_tts_runtime(provider, model, api_key)
