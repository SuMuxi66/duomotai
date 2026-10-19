# -*- coding: utf-8 -*-
"""
油温超温报警测试（2026-09-05，上限 200°C 用户指定）：
锅面温度连续 3 次超 200°C → 向在线会话推送 danger_detected 弹窗 + TTS 语音；
防毛刺（单次超温不报）、冷却 60s 不重报、温度回落重置计数、尊重 danger_alert_enabled。
"""
import asyncio
import time
from types import SimpleNamespace

from app import oil_watch
from app.session import SessionState
import app.ws.connection as connection_module


def _fake_registry(monkeypatch, sessions):
    """注册表快照 + ws_send/generate_tts 捕获。返回 (sent_messages, tts_calls)。"""
    sent_messages = []
    tts_calls = []

    async def fake_ws_send(ws, msg_type, payload):
        sent_messages.append((ws, msg_type, payload))

    async def fake_generate_tts(runtime, ws, state, text, **kw):
        tts_calls.append((ws, text))

    monkeypatch.setattr(connection_module, "SESSION_CONNECTIONS", dict(sessions))
    monkeypatch.setattr("app.ws.ws_send", fake_ws_send)  # ws_send 直接定义在 ws/__init__
    monkeypatch.setattr("app.ws.tts_handler.generate_tts", fake_generate_tts)  # generate_tts 走 __getattr__ 惰性解析
    return sent_messages, tts_calls


def _mk_state(enabled=True, last_alert=0.0):
    state = SessionState()
    state.danger_alert_enabled = enabled
    state.last_oil_overtemp_alert_time = last_alert
    return state


def _patch_temp(monkeypatch, temps):
    """get_valid_oil_temp 按次序返回，耗尽后停留最后值。"""
    seq = list(temps)
    monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: seq.pop(0) if seq else seq[-1] if seq else None)


def _reset_counter():
    oil_watch._overtemp_consecutive = 0


def test_consecutive_overtemp_then_alert(monkeypatch):
    """连续 3 次超温才报警：前两次不报，第三次推弹窗+语音。"""
    _reset_counter()
    ws = SimpleNamespace()
    state = _mk_state()
    sent, tts = _fake_registry(monkeypatch, {"s1": (ws, state)})
    _patch_temp(monkeypatch, [215.0, 216.0, 218.0])

    async def run():
        n1 = await oil_watch._overtemp_check_once(None)
        n2 = await oil_watch._overtemp_check_once(None)
        n3 = await oil_watch._overtemp_check_once(None)
        return n1, n2, n3

    n1, n2, n3 = asyncio.run(run())
    assert (n1, n2, n3) == (0, 0, 1)
    assert len(sent) == 1
    ws_, msg_type, payload = sent[0]
    assert msg_type == "danger_detected"
    assert payload["danger_types"] == ["油温过高"]
    assert "油温过高" in payload["message"]
    assert payload["is_high_energy"] is False
    assert tts and "油温过高" in tts[0][1]
    assert state.last_oil_overtemp_alert_time > 0


def test_single_spike_no_alert(monkeypatch):
    """单次毛刺超温后回落：不报警且计数重置。"""
    _reset_counter()
    sent, tts = _fake_registry(monkeypatch, {"s1": (SimpleNamespace(), _mk_state())})
    _patch_temp(monkeypatch, [230.0, 100.0, 100.0])

    async def run():
        return [await oil_watch._overtemp_check_once(None) for _ in range(3)]

    assert asyncio.run(run()) == [0, 0, 0]
    assert sent == [] and tts == []


def test_temp_drop_resets_consecutive(monkeypatch):
    """两次超温后回落再超温：需重新累计 3 次。"""
    _reset_counter()
    sent, _ = _fake_registry(monkeypatch, {"s1": (SimpleNamespace(), _mk_state())})
    _patch_temp(monkeypatch, [210.0, 210.0, 100.0, 210.0, 210.0])

    async def run():
        return [await oil_watch._overtemp_check_once(None) for _ in range(5)]

    assert asyncio.run(run()) == [0, 0, 0, 0, 0]
    assert sent == []


def test_cooldown_blocks_repeat_alert(monkeypatch):
    """报警后 60s 冷却：持续超温不重报；冷却过期后再次报警。"""
    _reset_counter()
    ws = SimpleNamespace()
    state = _mk_state()
    sent, _ = _fake_registry(monkeypatch, {"s1": (ws, state)})
    _patch_temp(monkeypatch, [220.0] * 9)

    async def run():
        results = [await oil_watch._overtemp_check_once(None) for _ in range(3)]
        first_alert_time = state.last_oil_overtemp_alert_time
        # 冷却期内：重新累计 3 次超温（计数已重置）→ 仍不报
        results += [await oil_watch._overtemp_check_once(None) for _ in range(3)]
        assert state.last_oil_overtemp_alert_time == first_alert_time
        # 冷却过期：模拟时间流逝后再超温 → 报警
        state.last_oil_overtemp_alert_time = time.time() - oil_watch.OVERTEMP_ALERT_COOLDOWN_S - 1
        results.append(await oil_watch._overtemp_check_once(None))
        results.append(await oil_watch._overtemp_check_once(None))
        results.append(await oil_watch._overtemp_check_once(None))
        return results

    results = asyncio.run(run())
    assert results == [0, 0, 1, 0, 0, 0, 0, 0, 1]
    assert len(sent) == 2


def test_danger_alert_disabled_skips(monkeypatch):
    """danger_alert_enabled=False：不推送（用户关了危险报警总开关）。"""
    _reset_counter()
    sent, tts = _fake_registry(monkeypatch, {"s1": (SimpleNamespace(), _mk_state(enabled=False))})
    _patch_temp(monkeypatch, [220.0] * 3)

    async def run():
        return [await oil_watch._overtemp_check_once(None) for _ in range(3)]

    assert asyncio.run(run()) == [0, 0, 0]
    assert sent == [] and tts == []


def test_registry_register_unregister():
    """连接注册表：注册/注销/快照隔离。"""
    ws, state = SimpleNamespace(), SessionState()
    connection_module.register_session_connection("t1", ws, state)
    assert "t1" in connection_module.iter_session_connections()
    snapshot = connection_module.iter_session_connections()
    connection_module.unregister_session_connection("t1")
    assert "t1" not in connection_module.iter_session_connections()
    assert "t1" in snapshot  # 快照不受后续注销影响


def test_threshold_constant_is_user_specified():
    """上限 200°C（用户指定），防抖 3 次，冷却 60s。"""
    assert oil_watch.OIL_TEMP_MAX_C == 200.0
    assert oil_watch.OVERTEMP_HIT_REQUIRED == 3
    assert oil_watch.OVERTEMP_ALERT_COOLDOWN_S == 60.0
