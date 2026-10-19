"""下料状态机达标逻辑：到达「目标 − 断电提前量」即断电（提前量补偿断电后管路余料，
实测 ~0.8g；历史上曾误用 2.0g 大容差导致设 3g 实际 1g 出头就提前停）。"""
import asyncio

import app.dispense as dispense


def _run_loop_until_finish(monkeypatch, weight_sequence):
    """以给定读数序列驱动 _monitor_loop，直到其达标退出，返回继电器指令序列。

    2026-09-07：dispense 默认执行器改为 valve 通道，_send_relay 不再被直接调用。
    这里测试的是闭环算法（达标/超时/提前量），需强制走 legacy_relay 通道才能
    用 _send_relay mock 捕获指令序列。
    """
    calls: list[str] = []
    monkeypatch.setattr(dispense, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(dispense, "_is_valve_channel", lambda: False)
    monkeypatch.setattr(dispense, "_send_relay", lambda action: calls.append(action) or True)
    weights = iter(weight_sequence)
    monkeypatch.setattr(dispense, "_now_weight", lambda: next(weights))

    dispense._state.update({
        "active": True, "target": 3.0, "target_g": 3.0,
        "started_weight": 0.0, "started_at": 0.0,
        "finished": False, "finished_weight": 0.0, "reason": "",
    })
    asyncio.run(dispense._monitor_loop())
    return calls, dict(dispense._state)


def test_stops_at_lead_compensated_target(monkeypatch):
    # 达标线 = 3.0 - 0.8 = 2.2：2.1g 须继续下料，2.3g 达到提前量线即断电
    calls, state = _run_loop_until_finish(monkeypatch, [2.1, 2.3])
    assert calls == ["off", "off", "on"], f"继电器指令序列异常: {calls}"
    assert state["reason"] == "completed"
    assert state["active"] is False
    assert state["finished"] is True


def test_below_lead_line_keeps_dispensing(monkeypatch):
    # 回归：1.0g / 1.9g / 2.0g 均低于达标线 2.2g，必须继续下料（旧 2g 容差 bug 在 1g 出头就停）
    calls, state = _run_loop_until_finish(monkeypatch, [1.0, 1.9, 2.0, 3.0])
    assert calls == ["off", "off", "off", "off", "on"]
    assert state["reason"] == "completed"


def test_timeout_forces_stop(monkeypatch):
    # 读数一直不达标：超时后强制断电（TIMEOUT_SECONDS 调小加速测试）
    calls: list[str] = []
    monkeypatch.setattr(dispense, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(dispense, "TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(dispense, "_is_valve_channel", lambda: False)
    monkeypatch.setattr(dispense, "_send_relay", lambda action: calls.append(action) or True)
    monkeypatch.setattr(dispense, "_now_weight", lambda: 0.5)
    dispense._state.update({
        "active": True, "target": 3.0, "target_g": 3.0,
        "started_weight": 0.0, "started_at": 0.0,
        "finished": False, "finished_weight": 0.0, "reason": "",
    })
    asyncio.run(dispense._monitor_loop())
    assert calls[-1] == "on", "超时后必须断电"
    assert dispense._state["reason"] == "timeout"
