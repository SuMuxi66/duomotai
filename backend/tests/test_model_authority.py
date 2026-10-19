# -*- coding: utf-8 -*-
"""模型 id 权威反转测试：标准供应商 settings 优先于 state 残值；custom 保留 state；
显式参数（/models/validate、/proxy/vlm）仍最高优先；apply_hello_settings 不再写 env。"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import runtime_providers as rp
from app.config import settings
from app.session import SessionState


# ---------- 辅助：构建一个记录入参的 fake build_vlm_runtime ----------

class _CallRecorder:
    """记录 build_vlm_runtime 的最近一次调用入参，返回一个非 None 占位对象。"""
    def __init__(self):
        self.last_kwargs = None

    def __call__(self, provider=None, api_key=None, model=None,
                 base_url=None, api_format=None):
        self.last_kwargs = dict(
            provider=provider, api_key=api_key, model=model,
            base_url=base_url, api_format=api_format,
        )
        return SimpleNamespace(_built=True)


@pytest.fixture
def recorder(monkeypatch):
    rec = _CallRecorder()
    monkeypatch.setattr(rp, "build_vlm_runtime", rec)
    return rec


# ---------- 1. 标准供应商：state.vlm_model 残值存在时仍用 settings ----------

def test_standard_provider_state_model_ignored_settings_used(recorder, monkeypatch):
    """标准供应商（dashscope）state.vlm_model 存在残值时，refresh_model_runtime
    不把 state.vlm_model 作为显式参数传入——build 走 settings.dashscope_vlm_model。"""
    monkeypatch.setattr(settings, "vlm_provider", "dashscope", raising=False)
    monkeypatch.setattr(settings, "dashscope_vlm_model", "qwen3.8-27b", raising=False)

    state = SessionState()
    state.vlm_provider = "dashscope"
    state.vlm_model = "STALE_Qwen/Qwen3-VL-8B-Instruct"  # 前端 localStorage 残值
    state.llm_api_key = "stale-key"

    runtime = SimpleNamespace(vlm=None, llm=None)
    rp.refresh_model_runtime(runtime, state)

    kw = recorder.last_kwargs
    assert kw["provider"] is None, "标准供应商 provider 应传 None 让 build 走 settings"
    assert kw["model"] is None, "标准供应商 model 应传 None 让 build 走 settings"
    assert kw["api_key"] is None, "标准供应商 key 应传 None 让 build 走 settings"
    assert runtime.vlm is not None
    assert runtime.llm is runtime.vlm


def test_standard_provider_modelscope_settings_used(recorder, monkeypatch):
    """modelscope 标准供应商同样走 settings 权威链。"""
    monkeypatch.setattr(settings, "vlm_provider", "modelscope", raising=False)
    monkeypatch.setattr(settings, "modelscope_vlm_model", "Qwen/Qwen3.8-27B", raising=False)

    state = SessionState()
    state.vlm_provider = "modelscope"
    state.vlm_model = "dirty-old-model"

    runtime = SimpleNamespace(vlm=None, llm=None)
    rp.refresh_model_runtime(runtime, state)

    kw = recorder.last_kwargs
    assert kw["model"] is None
    assert kw["provider"] is None


# ---------- 2. custom 供应商：保留 state 取值 ----------

def test_custom_provider_state_model_preserved(recorder, monkeypatch):
    """custom 供应商的 model/key 无 env 键位，refresh_model_runtime 保留 state 取值。"""
    state = SessionState()
    state.vlm_provider = "custom:myendpoint"
    state.vlm_model = "my-custom-model"
    state.llm_api_key = "custom-key"
    state.vlm_base_url = "https://example.com/v1"
    state.vlm_api_format = "openai"

    runtime = SimpleNamespace(vlm=None, llm=None)
    rp.refresh_model_runtime(runtime, state)

    kw = recorder.last_kwargs
    assert kw["provider"] == "custom:myendpoint"
    assert kw["model"] == "my-custom-model"
    assert kw["api_key"] == "custom-key"
    assert kw["base_url"] == "https://example.com/v1"
    assert kw["api_format"] == "openai"


def test_custom_prefix_provider_state_preserved(recorder):
    """provider=='custom'（无冒号）同样走 custom 分支，保留 state。"""
    state = SessionState()
    state.vlm_provider = "custom"
    state.vlm_model = "generic-model"
    state.llm_api_key = "gk"

    runtime = SimpleNamespace(vlm=None, llm=None)
    rp.refresh_model_runtime(runtime, state)

    kw = recorder.last_kwargs
    assert kw["provider"] == "custom"
    assert kw["model"] == "generic-model"
    assert kw["api_key"] == "gk"


# ---------- 3. 显式参数（validate/proxy 语义）仍最高优先 ----------

def test_build_vlm_runtime_explicit_model_highest_priority(monkeypatch):
    """build_vlm_runtime 直接传显式 model 时，优先级 > settings > default（原语义不变）。
    用真实 build（不 mock），只 monkeypatch settings 的 key 让它返回 MockVLM 或真实实例。"""
    # 只要 build 不抛异常且 model 被采用即可；用 modelscope 缺 key 会回退 MockVLM，
    # 但 MockVLM 不记录 model。改用 dashscope + 假 key 让它走 UnifiedOpenAIProvider。
    monkeypatch.setattr(settings, "dashscope_api_key", "fake-key-for-test", raising=False)
    monkeypatch.setattr(settings, "dashscope_vlm_model", "settings-model", raising=False)

    vlm = rp.build_vlm_runtime(provider="dashscope", api_key="explicit-key", model="EXPLICIT_MODEL")
    # UnifiedOpenAIProvider 会保存 model 属性
    assert getattr(vlm, "model", None) == "EXPLICIT_MODEL"


def test_build_vlm_runtime_settings_fallback_when_no_explicit(monkeypatch):
    """无显式 model 时走 settings.<model_env>（标准供应商权威链）。"""
    monkeypatch.setattr(settings, "dashscope_api_key", "fake-key-for-test", raising=False)
    monkeypatch.setattr(settings, "dashscope_vlm_model", "SETTINGS_MODEL_VALUE", raising=False)

    vlm = rp.build_vlm_runtime(provider="dashscope", api_key="fake-key-for-test")
    assert getattr(vlm, "model", None) == "SETTINGS_MODEL_VALUE"


# ---------- 4. apply_hello_settings 不再写 env ----------

def test_apply_hello_settings_does_not_persist_env(monkeypatch, tmp_path):
    """apply_hello_settings 不应调用 _persist_frontend_model_config（即不写 .env.local）。
    用 monkeypatch 拦截 persist_env_value，断言未被调用。"""
    from app.ws import llm_handler as lh

    # 拦截 persist_env_value 与 reload_settings，确保未被 hello 路径触发
    persist_calls = []

    def _fake_persist(key, value):
        persist_calls.append((key, value))
        return False

    monkeypatch.setattr("app.config.persist_env_value", _fake_persist)
    # llm_handler 里 _persist_frontend_model_config 是函数级 import，拦截模块属性
    monkeypatch.setattr(lh, "_persist_frontend_model_config",
                        lambda *a, **kw: persist_calls.append(("via_persist", a)))

    # 拦截 refresh_model_runtime / apply_stt_update / apply_tts_update 避免真实构建
    monkeypatch.setattr(rp, "refresh_model_runtime", lambda *a, **kw: None)
    # ws 包里的别名
    import app.ws as ws_pkg
    monkeypatch.setattr(ws_pkg, "refresh_model_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "apply_stt_update", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "apply_tts_update", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "get_tts_voice", lambda *a, **kw: "v")
    monkeypatch.setattr(ws_pkg, "set_session_tts", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "get_stt_runtime_key", lambda *a, **kw: ("browser", None, None))

    state = SessionState()
    runtime = SimpleNamespace(vlm=None, llm=None, stt=None, tts=None)

    payload = {
        "vlm_provider": "dashscope",
        "vlm_model": "some-frontend-model",
        "tts_provider": "browser",
        "stt_provider": "browser",
    }
    lh.apply_hello_settings(runtime, state, payload)

    # hello 不应触发任何 env 写入
    assert persist_calls == [], f"hello 不应写 env，但调用了: {persist_calls}"
    # state 赋值仍保留（custom 回退与上下文记录用）
    assert state.vlm_provider == "dashscope"
    assert state.vlm_model == "some-frontend-model"


def test_apply_hello_settings_stale_model_not_written_to_env(monkeypatch):
    """更精确：前端发来的残值模型名不应经 persist_env_value 落盘。"""
    from app.config import persist_env_value as real_persist
    import app.config as cfg

    persist_calls = []
    def _spy(key, value):
        persist_calls.append((key, value))
        return False
    monkeypatch.setattr(cfg, "persist_env_value", _spy)
    # llm_handler 在函数内 from ..config import persist_env_value，所以拦 cfg 模块属性即可
    monkeypatch.setattr("app.config.persist_env_value", _spy)

    import app.ws as ws_pkg
    monkeypatch.setattr(ws_pkg, "refresh_model_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "apply_stt_update", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "apply_tts_update", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "get_tts_voice", lambda *a, **kw: "v")
    monkeypatch.setattr(ws_pkg, "set_session_tts", lambda *a, **kw: None)
    monkeypatch.setattr(ws_pkg, "get_stt_runtime_key", lambda *a, **kw: ("browser", None, None))

    from app.ws import llm_handler as lh
    state = SessionState()
    runtime = SimpleNamespace(vlm=None, llm=None, stt=None, tts=None)
    payload = {
        "vlm_provider": "modelscope",
        "vlm_model": "STALE_MODEL_FROM_LOCALSTORAGE",
        "tts_provider": "browser",
        "stt_provider": "browser",
    }
    lh.apply_hello_settings(runtime, state, payload)

    vlm_writes = [c for c in persist_calls if "MODEL" in c[0] or "PROVIDER" in c[0]]
    assert vlm_writes == [], f"hello 不应写 VLM env: {vlm_writes}"
