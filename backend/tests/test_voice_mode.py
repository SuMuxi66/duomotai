# -*- coding: utf-8 -*-
"""语音模式（持续识别）远程控制：状态记录、WS 广播、无在线页面的降级。"""
import pytest

import app.voice_mode as voice_mode
import app.ws as ws_pkg
import app.ws.connection as ws_connection


@pytest.fixture(autouse=True)
def _reset_state():
    voice_mode._voice_state.update({
        "continuous": False, "source": "init", "updated_at": 0.0, "delivered": 0,
    })
    yield


class _FakeWs:
    pass


def _install_connections(monkeypatch, sessions):
    """把 iter_session_connections 替换为固定会话表。"""
    monkeypatch.setattr(ws_connection, "iter_session_connections", lambda: sessions)


def test_start_without_online_page_records_state(monkeypatch):
    """没有前端在线：指令仍记录（status 可查），但 applied=False 并带 warning。"""
    _install_connections(monkeypatch, {})
    sent = []
    async def fake_ws_send(ws, typ, payload):
        sent.append((typ, payload))

    monkeypatch.setattr(ws_pkg, "ws_send", fake_ws_send)

    import asyncio
    result = asyncio.run(voice_mode.voice_continuous_start())

    assert result["success"] is True
    assert result["continuous"] is True
    assert result["delivered"] == 0
    assert result["applied"] is False
    assert "warning" in result

    status = voice_mode.voice_continuous_status()
    assert status["continuous"] is True
    assert status["mode"] == "持续识别"
    assert status["source"] == "api"


def test_start_pushes_voice_mode_to_online_sessions(monkeypatch):
    """在线会话收到 voice_mode 指令，payload 带 continuous 与 source。"""
    fake_ws = _FakeWs()
    _install_connections(monkeypatch, {"default": (fake_ws, None)})
    sent = []
    async def fake_ws_send(ws, typ, payload):
        sent.append((ws, typ, payload))

    monkeypatch.setattr(ws_pkg, "ws_send", fake_ws_send)

    import asyncio
    result = asyncio.run(voice_mode.voice_continuous_start(source="llm_tool"))

    assert result["applied"] is True
    assert result["delivered"] == 1
    assert len(sent) == 1
    ws, typ, payload = sent[0]
    assert ws is fake_ws
    assert typ == "voice_mode"
    assert payload["continuous"] is True
    assert payload["source"] == "llm_tool"


def test_stop_restores_standby(monkeypatch):
    _install_connections(monkeypatch, {})
    async def fake_ws_send(ws, typ, payload):
        pass

    monkeypatch.setattr(ws_pkg, "ws_send", fake_ws_send)

    import asyncio
    asyncio.run(voice_mode.voice_continuous_start())
    result = asyncio.run(voice_mode.voice_continuous_stop())

    assert result["continuous"] is False
    assert result["mode"] == "唤醒词/待机"
    assert voice_mode.voice_continuous_status()["continuous"] is False


def test_invalid_source_falls_back_to_api(monkeypatch):
    _install_connections(monkeypatch, {})
    async def fake_ws_send(ws, typ, payload):
        pass

    monkeypatch.setattr(ws_pkg, "ws_send", fake_ws_send)

    import asyncio
    result = asyncio.run(voice_mode.voice_continuous_start(source="hacker"))

    assert result["source"] == "api"


def test_llm_tool_executor_wired():
    """llm_tools 三件套执行器已接通 voice_mode。"""
    import asyncio
    import json

    import app.llm_tools as llm_tools

    result = json.loads(asyncio.run(llm_tools.execute_tool("voice_continuous_status", "{}")))
    assert result["success"] is True
    assert "continuous" in result

    names = {s["function"]["name"] for s in llm_tools.TOOL_SCHEMAS}
    assert {"voice_continuous_start", "voice_continuous_stop", "voice_continuous_status"} <= names
