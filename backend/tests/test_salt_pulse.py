# -*- coding: utf-8 -*-
"""
盐量脉冲链路测试（2026-09-05）：
问"需要放多少盐"→按秤上食材克重静默计算→提示放锅铲→"好了"→通电 1 秒脉冲→断电播报"已关闭"。
参考 tests/test_dispense_auto.py 的 monkeypatch 风格，不触真实硬件/LLM。
"""
import asyncio
import pytest
from types import SimpleNamespace

from app import cooking_core, dispense as dispense_module
from app.session import SessionState
import app.ws_handlers.cooking as cooking_module
import app.ws.llm_handler as llm_handler_module


# ---------- looks_like_salt_calc_request：触发判定 ----------

def test_salt_calc_trigger_hits():
    assert cooking_module.looks_like_salt_calc_request("需要放多少盐") is True
    assert cooking_module.looks_like_salt_calc_request("请帮我计算需要多少克盐巴") is True
    assert cooking_module.looks_like_salt_calc_request("盐巴要放几克") is True
    assert cooking_module.looks_like_salt_calc_request("帮我算一下食盐的用量") is True
    # 2026-09-05 用户场景：备调料触发
    assert cooking_module.looks_like_salt_calc_request("小云小云我准备放调料了请帮我准备一下调料") is True
    assert cooking_module.looks_like_salt_calc_request("请帮我准备一下调料") is True
    assert cooking_module.looks_like_salt_calc_request("我准备放调料了") is True


def test_salt_calc_trigger_miss():
    """非盐调料/非用量语义/显式克数指令/知识问句不触发（显式指令由 parse_dispense_command 上游处理）。"""
    assert cooking_module.looks_like_salt_calc_request("糖放多少") is False  # 非盐
    assert cooking_module.looks_like_salt_calc_request("盐放多了怎么办") is False  # 无用量 cue
    assert cooking_module.looks_like_salt_calc_request("为什么要放盐") is False
    assert cooking_module.looks_like_salt_calc_request("下3克盐") is False  # 显式指令无用量 cue
    assert cooking_module.looks_like_salt_calc_request("为什么要放调料") is False  # 知识问句
    assert cooking_module.looks_like_salt_calc_request("怎么准备调料") is False  # 求教问句
    assert cooking_module.looks_like_salt_calc_request("调料放多了怎么办") is False
    assert cooking_module.looks_like_salt_calc_request("调料要放多少") is False  # 问调料用量非盐、非备料意图
    assert cooking_module.looks_like_salt_calc_request("") is False
    assert cooking_module.looks_like_salt_calc_request(None) is False


# ---------- handle_salt_calc_request：静默计算 + 提示锅铲 ----------

def test_salt_calc_reads_scale_and_prompts(monkeypatch):
    """读秤 300g → 计算 1.2g（0.4% 中值比例）→ 按脚本播报（含食材克重与配比盐量），标志置位。"""
    captured = {"sent": [], "weight_called": 0}

    def fake_get_weight():
        captured["weight_called"] += 1
        return {"weight": 300.0, "raw": 300.0}

    async def fake_send_text(runtime, ws, state, text, **kw):
        captured["sent"].append(text)

    monkeypatch.setattr("app.weight.get_weight", fake_get_weight)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = SessionState()
    asyncio.run(cooking_module.handle_salt_calc_request(None, None, state, request_id="lat-x"))
    assert captured["weight_called"] == 1
    assert state.awaiting_salt_pulse_confirmation is True
    assert state.salt_pulse_grams == 1.2
    text = captured["sent"][0]
    assert "锅铲放到出料口" in text and "准备放盐巴" in text
    assert "300 克" in text and "1.2 克" in text  # 播报含食材克重与配比盐量


def test_salt_calc_clamps_range(monkeypatch):
    """比例计算按 0.5~100g 截断：1kg 食材→4g 盐；25kg→100g 封顶；空秤→0.5g 保底。"""
    for scale_weight, expected in ((1000.0, 4.0), (25000.0, 100.0), (0.0, 0.5)):
        captured = {}

        def fake_get_weight(w=scale_weight):
            return {"weight": w, "raw": w}

        async def fake_send_text(runtime, ws, state, text, **kw):
            captured["ok"] = True

        monkeypatch.setattr("app.weight.get_weight", fake_get_weight)
        monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
        state = SessionState()
        asyncio.run(cooking_module.handle_salt_calc_request(None, None, state))
        assert state.salt_pulse_grams == expected, f"weight={scale_weight}"


# ---------- handle_salt_pulse_confirm："好了"→ 1s 脉冲 → 两段播报 ----------

def test_confirm_runs_pulse_and_announces(monkeypatch):
    """确认后：回复"调料出口已开启"→脉冲→回复"调料出口已关闭"；标志清除。"""
    captured = {"sent": [], "calls": []}

    async def fake_pulse(duration_s):
        captured["calls"].append(duration_s)
        return {"success": True, "duration_s": duration_s}

    async def fake_send_text(runtime, ws, state, text, **kw):
        captured["sent"].append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "pulse_dispense", fake_pulse)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = SessionState()
    state.awaiting_salt_pulse_confirmation = True
    state.salt_pulse_grams = 3.0
    asyncio.run(cooking_module.handle_salt_pulse_confirm(None, None, state, request_id="lat-y"))
    assert captured["calls"] == [cooking_module.SALT_PULSE_SECONDS]
    assert captured["sent"][0] == "调料出口已打开"  # 开启先播报（2026-09-05 用户脚本措辞）
    assert captured["sent"][-1] == "调料出口已关闭"  # 关闭后播报
    assert state.awaiting_salt_pulse_confirmation is False


def test_confirm_pulse_failure_announces_error(monkeypatch):
    """串口未就绪：播报失败提示而非"已关闭"。"""
    captured = {"sent": []}

    async def fake_pulse(duration_s):
        return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}

    async def fake_send_text(runtime, ws, state, text, **kw):
        captured["sent"].append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "pulse_dispense", fake_pulse)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = SessionState()
    state.awaiting_salt_pulse_confirmation = True
    asyncio.run(cooking_module.handle_salt_pulse_confirm(None, None, state))
    assert state.awaiting_salt_pulse_confirmation is False
    assert all("已关闭" not in t for t in captured["sent"])
    assert any("打开失败" in t for t in captured["sent"])


# ---------- pulse_dispense：定时通电 + finally 断电（真函数，mock relay） ----------

def test_pulse_dispense_relay_sequence(monkeypatch):
    """relay 序列：先 'off'（通电出料）→ 等待 → 'on'（断电）；与连续下料互斥先停旧任务。

    2026-09-07：dispense 默认执行器改为 valve 通道，需强制走 legacy_relay 通道
    才能用 relay_control mock 捕获指令序列。
    """
    seq = []

    def fake_relay(action):
        seq.append(action)
        return {"status": "ok"}

    async def fake_stop():
        seq.append("stop_old")
        return {"success": True}

    async def fake_wait_ready(timeout=15.0):
        return True

    monkeypatch.setattr(dispense_module, "_is_valve_channel", lambda: False)
    monkeypatch.setattr(dispense_module, "relay_control", fake_relay)
    monkeypatch.setattr(dispense_module, "stop_dispense", fake_stop)
    monkeypatch.setattr(dispense_module, "_wait_serial_ready", fake_wait_ready)
    result = asyncio.run(dispense_module.pulse_dispense(0.05))
    assert result["success"] is True
    assert "stop_old" not in seq  # 2026-09-16 起 pulse_dispense 不再先停旧任务（改为互斥拒绝）
    assert seq[0] == "off"       # '0' = 通电出料
    assert seq[-1] == "on"       # '1' = 断电
    assert result["duration_s"] == 0.05


def test_pulse_dispense_serial_not_ready(monkeypatch):
    """串口未就绪：返回失败且不发任何 relay 指令。"""
    seq = []

    def fake_relay(action):
        seq.append(action)
        return {"status": "ok"}

    async def fake_wait_ready(timeout=15.0):
        return False

    monkeypatch.setattr(dispense_module, "relay_control", fake_relay)
    monkeypatch.setattr(dispense_module, "_wait_serial_ready", fake_wait_ready)
    result = asyncio.run(dispense_module.pulse_dispense(0.05))
    assert result["success"] is False
    assert seq == []


# ---------- llm_handler 确认门：好了/取消/其他内容 ----------

class _GateHarness:
    """handle_user_text 夹具：mock 读秤/TTS/脉冲，捕获播报。"""

    def __init__(self, monkeypatch, scale_weight=300.0):
        self.sent = []
        self.pulse_calls = []

        def fake_get_weight():
            return {"weight": scale_weight, "raw": scale_weight}

        async def fake_send_text(runtime, ws, state, text, **kw):
            self.sent.append(text)

        async def fake_pulse(duration_s):
            self.pulse_calls.append(duration_s)
            return {"success": True}

        monkeypatch.setattr("app.weight.get_weight", fake_get_weight)
        monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
        monkeypatch.setattr(cooking_module.dispense_module, "pulse_dispense", fake_pulse)

    @property
    def runtime(self):
        return SimpleNamespace(llm=None, tts=None)


@pytest.mark.skip(reason="盐量脉冲链路已于 2026-09-16 移除（放盐改由大模型调 MCP 工具完成），本测试暂挂")
def test_gate_full_flow_via_handle_user_text(monkeypatch):
    """端到端：问盐→播报脚本；"好了"→打开+关闭播报；标志清除。"""
    h = _GateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-salt-gate"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要放多少盐", request_id="r1"))
    assert state.awaiting_salt_pulse_confirmation is True
    assert any("锅铲" in t for t in h.sent)

    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "好了", request_id="r2"))
    assert h.pulse_calls == [cooking_module.SALT_PULSE_SECONDS]
    assert any(t == "调料出口已打开" for t in h.sent)
    assert h.sent[-1] == "调料出口已关闭"
    assert state.awaiting_salt_pulse_confirmation is False


@pytest.mark.skip(reason="盐量脉冲链路已于 2026-09-16 移除（放盐改由大模型调 MCP 工具完成），本测试暂挂")
def test_gate_user_script_flow(monkeypatch):
    """2026-09-05 用户脚本端到端："准备调料"触发 → "准备好了，给我放盐巴"确认。"""
    h = _GateHarness(monkeypatch, scale_weight=250.0)
    state = SessionState()
    state.session_id = "unit-salt-script"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "小云小云我准备放调料了请帮我准备一下调料", request_id="r1"))
    assert state.awaiting_salt_pulse_confirmation is True
    assert any("锅铲放到出料口" in t and "250 克" in t and "1 克" in t for t in h.sent)

    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "准备好了，给我放盐巴", request_id="r2"))
    assert h.pulse_calls == [cooking_module.SALT_PULSE_SECONDS]
    assert h.sent[-1] == "调料出口已关闭"
    assert any(t == "调料出口已打开" for t in h.sent)
    assert state.awaiting_salt_pulse_confirmation is False


@pytest.mark.skip(reason="盐量脉冲链路已于 2026-09-16 移除（放盐改由大模型调 MCP 工具完成），本测试暂挂")
def test_gate_wait_phrase_keeps_flag(monkeypatch):
    """"还没准备好"：播报缓一缓提示，标志保持，不脉冲；随后确认仍可触发。"""
    h = _GateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-salt-wait"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要放多少盐", request_id="r1"))
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "还没准备好，等一下", request_id="r2"))
    assert h.pulse_calls == []
    assert state.awaiting_salt_pulse_confirmation is True
    assert any("锅铲放好后告诉我" in t for t in h.sent)

    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "好了", request_id="r3"))
    assert h.pulse_calls == [cooking_module.SALT_PULSE_SECONDS]
    assert state.awaiting_salt_pulse_confirmation is False


@pytest.mark.skip(reason="盐量脉冲链路已于 2026-09-16 移除（放盐改由大模型调 MCP 工具完成），本测试暂挂")
def test_gate_cancel(monkeypatch):
    """确认门内"算了"→取消播报，不触发脉冲。"""
    h = _GateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-salt-cancel"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要放多少盐", request_id="r1"))
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "算了", request_id="r2"))
    assert h.pulse_calls == []
    assert state.awaiting_salt_pulse_confirmation is False
    assert any("取消" in t for t in h.sent)


@pytest.mark.skip(reason="盐量脉冲链路已于 2026-09-16 移除（放盐改由大模型调 MCP 工具完成），本测试暂挂")
def test_gate_other_text_clears_flag_and_routes(monkeypatch):
    """确认门内收到其他内容（"这步完事了"）：清标志放行，不脉冲、不重复提示。"""
    h = _GateHarness(monkeypatch)

    async def fake_llm_response(*a, **k):
        return "（mock）"

    from app.ws import llm_handler as _llm
    monkeypatch.setattr(_llm, "handle_llm_response", fake_llm_response)
    monkeypatch.setattr(llm_handler_module, "handle_scene_response", fake_llm_response)
    state = SessionState()
    state.session_id = "unit-salt-other"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要放多少盐", request_id="r1"))
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "这步完事了", request_id="r2"))
    assert h.pulse_calls == []
    assert state.awaiting_salt_pulse_confirmation is False
    assert any("锅铲" in t for t in h.sent)  # 只有第一轮的提示，无第二轮


def test_gate_expired_clears(monkeypatch):
    """确认门 10 分钟过期：即使回复"好了"也不触发脉冲。"""
    h = _GateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-salt-expired"

    async def fake_llm_response(*a, **k):
        return "（mock）"

    from app.ws import llm_handler as _llm
    monkeypatch.setattr(_llm, "handle_llm_response", fake_llm_response)
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要放多少盐", request_id="r1"))
    state.salt_pulse_requested_at -= cooking_module.SALT_PULSE_CONFIRM_EXPIRE_S + 1
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "好了", request_id="r2"))
    assert h.pulse_calls == []
    assert state.awaiting_salt_pulse_confirmation is False
