# -*- coding: utf-8 -*-
"""确认门标志持久化（B2）：盐量脉冲/油温盯守确认门按 session_id 断开快照、重连恢复。"""
import time

from app.session import SessionState
import app.ws.connection as connection_module


def _mk_state(session_id="gate_test"):
    state = SessionState()
    state.session_id = session_id
    return state


def test_gate_roundtrip(monkeypatch, tmp_path):
    """置位状态保存后，新连接同 session_id 能恢复标志与关联字段。"""
    gate_file = tmp_path / "session_gates.json"
    monkeypatch.setattr(connection_module, "_SESSION_GATES_FILE", gate_file)

    state = _mk_state()
    state.awaiting_salt_pulse_confirmation = True
    state.salt_pulse_grams = 24.0
    state.salt_pulse_requested_at = time.time()
    connection_module._save_session_gates(state)

    fresh = _mk_state()
    assert fresh.awaiting_salt_pulse_confirmation is False
    connection_module._load_session_gates(fresh)
    assert fresh.awaiting_salt_pulse_confirmation is True
    assert fresh.salt_pulse_grams == 24.0
    assert fresh.salt_pulse_requested_at == state.salt_pulse_requested_at


def test_gate_cleared_when_all_closed(monkeypatch, tmp_path):
    """两个门都关闭时保存，应清掉该 session 的桶（不残留陈旧标志）。"""
    gate_file = tmp_path / "session_gates.json"
    monkeypatch.setattr(connection_module, "_SESSION_GATES_FILE", gate_file)

    state = _mk_state()
    state.awaiting_oil_watch_confirm = True
    state.oil_watch_proposed_target = 180
    connection_module._save_session_gates(state)
    assert gate_file.exists()

    closed = _mk_state()
    connection_module._save_session_gates(closed)
    fresh = _mk_state()
    connection_module._load_session_gates(fresh)
    assert fresh.awaiting_oil_watch_confirm is False


def test_gate_load_missing_or_corrupt_file(monkeypatch, tmp_path):
    """文件不存在/损坏时安全返回，不抛异常、不改默认值。"""
    monkeypatch.setattr(connection_module, "_SESSION_GATES_FILE", tmp_path / "nope.json")
    state = _mk_state()
    connection_module._load_session_gates(state)
    assert state.awaiting_salt_pulse_confirmation is False

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(connection_module, "_SESSION_GATES_FILE", corrupt)
    state2 = _mk_state()
    connection_module._load_session_gates(state2)
    assert state2.awaiting_oil_watch_confirm is False
