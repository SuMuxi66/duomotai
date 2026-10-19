# -*- coding: utf-8 -*-
"""/models/validate 端点测试：模型需通过带图实测才返回 ok"""
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class FakeVLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def analyze_image(self, jpeg_bytes: bytes, prompt: str, **kw):
        self.calls += 1
        assert jpeg_bytes and len(jpeg_bytes) > 10  # 测试图非空
        return self.reply


@pytest.fixture
def fake_runtime(monkeypatch):
    holder = {"vlm": None}

    def _set(reply):
        holder["vlm"] = FakeVLM(reply)

    holder["set"] = _set

    def _build(provider=None, api_key=None, model=None, base_url=None, **kw):
        vlm = holder["vlm"]
        assert model, "model 必须传入"
        return vlm

    import app.runtime_providers as rp
    monkeypatch.setattr(rp, "build_vlm_runtime", _build)
    return holder


@pytest.mark.asyncio
async def test_validate_ok(client, fake_runtime, monkeypatch):
    fake_runtime["set"]("OK")
    res = await client.post("/models/validate", json={"provider": "modelscope", "api_key": "k", "model": "Qwen/Qwen3.5-27B"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "OK" in data["reply"]


@pytest.mark.asyncio
async def test_validate_provider_error_rejected(client, fake_runtime):
    """provider 层错误（全角括号包裹）→ ok=false"""
    fake_runtime["set"]("（视觉分析失败：model_not_found）")
    res = await client.post("/models/validate", json={"provider": "modelscope", "api_key": "k", "model": "X"})
    assert res.status_code == 200
    assert res.json()["ok"] is False


@pytest.mark.asyncio
async def test_validate_missing_params(client):
    res = await client.post("/models/validate", json={"provider": "modelscope"})
    assert res.status_code == 200
    assert res.json()["ok"] is False
