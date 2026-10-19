# -*- coding: utf-8 -*-
"""dispense_start/stop 经真实 MCP 链路路由的单元测试（mock mcp_client，不碰硬件）。

2026-09-20 用户要求下料走真 MCP（mcp_client → mcp_server.py → HTTP 回环 → dispense）。
这里只验证路由与回退决策，MCP 传输层由 tests 外的 E2E（dispense_status 只读）覆盖。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import dispense, llm_tools, mcp_client


@pytest.fixture(autouse=True)
def _reset_gates():
    llm_tools._DISPENSE_GATES.clear()
    llm_tools._DISPENSE_LAST_EXEC.clear()
    yield
    llm_tools._DISPENSE_GATES.clear()
    llm_tools._DISPENSE_LAST_EXEC.clear()


@pytest.fixture(autouse=True)
def force_mcp(monkeypatch):
    monkeypatch.setattr(llm_tools, "_MCP_DISPENSE_ENABLED", True)
    monkeypatch.setattr(llm_tools, "_MCP_DISPENSE_FALLBACK", True)


async def _confirm_dispense(session_key: str, payload: str = '{"pulse_seconds": 3}') -> dict:
    """用户说「确认」走口头确认快车道，绕开跨轮预约语义"""
    raw = await llm_tools.execute_tool(
        "dispense_start", payload, session_key=session_key, turn=1, user_text="确认"
    )
    return json.loads(raw)


async def test_dispense_start_routes_through_mcp(monkeypatch):
    """放行后应经 MCP 调 dispense_start，参数按模式映射，且不落回直调"""
    seen = {}

    async def fake_call_tool(name, arguments=None, timeout=20.0):
        seen.update(name=name, args=dict(arguments or {}), timeout=timeout)
        return True, {"success": True, "state": "ON"}

    async def no_direct(seconds):
        raise AssertionError("MCP 成功时不应直调 pulse_dispense")

    monkeypatch.setattr(mcp_client, "call_tool", fake_call_tool)
    monkeypatch.setattr(dispense, "pulse_dispense", no_direct)

    data = await _confirm_dispense("t1")
    assert seen == {"name": "dispense_start", "args": {"pulse_seconds": 3.0}, "timeout": 22.0}
    assert data["success"] is True


async def test_mcp_failure_falls_back_to_direct(monkeypatch):
    """MCP 链路失败 + 回退开 → 直调 pulse_dispense，结果照常返回"""
    async def fail_call(name, arguments=None, timeout=20.0):
        return False, {"error": "MCP down"}

    async def fake_pulse(seconds):
        assert seconds == 3.0
        return {"success": True, "state": "PULSE"}

    monkeypatch.setattr(mcp_client, "call_tool", fail_call)
    monkeypatch.setattr(dispense, "pulse_dispense", fake_pulse)

    data = await _confirm_dispense("t2")
    assert data["success"] is True


async def test_mcp_failure_without_fallback_returns_error(monkeypatch):
    """MCP 失败 + 回退关（严格 MCP）→ 返回 error，绝不直调"""
    async def fail_call(name, arguments=None, timeout=20.0):
        return False, {"error": "MCP down"}

    async def no_direct(seconds):
        raise AssertionError("FALLBACK=0 时不应直调")

    monkeypatch.setattr(mcp_client, "call_tool", fail_call)
    monkeypatch.setattr(llm_tools, "_MCP_DISPENSE_FALLBACK", False)
    monkeypatch.setattr(dispense, "pulse_dispense", no_direct)

    data = await _confirm_dispense("t3")
    assert "error" in data


async def test_target_g_mode_maps_to_mcp(monkeypatch):
    """称重闭环模式：target_g 原样映射给 MCP 工具"""
    seen = {}

    async def fake_call_tool(name, arguments=None, timeout=20.0):
        seen.update(args=dict(arguments or {}))
        return True, {"success": True}

    monkeypatch.setattr(mcp_client, "call_tool", fake_call_tool)
    monkeypatch.setattr(dispense, "start_dispense", lambda g: (_ for _ in ()).throw(AssertionError("不应直调")))

    data = await _confirm_dispense("t4", payload='{"target_g": 3}')
    assert seen["args"] == {"target_g": 3.0}
    assert data["success"] is True


async def test_dispense_stop_routes_through_mcp(monkeypatch):
    """dispense_stop 同样走 MCP"""
    seen = {}

    async def fake_call_tool(name, arguments=None, timeout=20.0):
        seen.update(name=name)
        return True, {"success": True, "message": "closed via mcp"}

    async def no_direct():
        raise AssertionError("MCP 成功时不应直调 stop_dispense")

    monkeypatch.setattr(mcp_client, "call_tool", fake_call_tool)
    monkeypatch.setattr(dispense, "stop_dispense", no_direct)

    data = json.loads(await llm_tools.execute_tool("dispense_stop", "{}"))
    assert seen["name"] == "dispense_stop"
    assert data["success"] is True


async def test_disabled_flag_bypasses_mcp(monkeypatch):
    """MCP_DISPENSE_ENABLED=0 → 整体旁路，不碰 mcp_client，行为与旧直调一致"""
    async def no_mcp(name, arguments=None, timeout=20.0):
        raise AssertionError("禁用时不应发起 MCP 调用")

    async def fake_pulse(seconds):
        return {"success": True, "state": "PULSE"}

    monkeypatch.setattr(llm_tools, "_MCP_DISPENSE_ENABLED", False)
    monkeypatch.setattr(mcp_client, "call_tool", no_mcp)
    monkeypatch.setattr(dispense, "pulse_dispense", fake_pulse)

    data = await _confirm_dispense("t5")
    assert data["success"] is True
