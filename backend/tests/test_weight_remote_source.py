# -*- coding: utf-8 -*-
"""称重双源 + 远程 Pi 通道（2026-09-12 硬件迁移：称重/电磁阀移到香橙派）。

- 称重：opi 源（remote_sensors 上行板卡文本行）fresh 优先，断流回落 local 槽；
  远程行走与本机串口完全相同的解析管线（去皮/增益/EMA）。
- 电磁阀：Pi 通道在线时开/关阀命令转发远程（fire-and-forget），未连接回落本机串口。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import remote_sensors, valve
from app.weight import (
    LOCAL_SOURCE,
    REMOTE_FRESH_SECONDS,
    REMOTE_SOURCE,
    WeightScaleReader,
)


@pytest.fixture
def reader():
    """独立 reader 实例（固定增益 1.0，不触串口）"""
    return WeightScaleReader(gain_factor=1.0)


def _weight_line(g: float) -> str:
    return f"Weight={g:.2f}g RAW=1000"


def test_remote_line_writes_opi_slot(reader):
    """远程板卡行 → opi 源槽，读数带 source 标记"""
    reader.process_serial_line(_weight_line(12.3), source=REMOTE_SOURCE)
    data = reader.get_weight_data()
    assert data["weight"] == 12
    assert data["raw"] == pytest.approx(12.3, abs=0.1)
    assert data["source"] == REMOTE_SOURCE


def test_remote_fresh_preferred(reader):
    """双源都在线：优先返回远程 opi 数据"""
    reader.process_serial_line(_weight_line(30.0), source=LOCAL_SOURCE)
    reader.process_serial_line(_weight_line(50.0), source=REMOTE_SOURCE)
    data = reader.get_weight_data()
    assert data["raw"] == pytest.approx(50.0, abs=0.1)
    assert data["source"] == REMOTE_SOURCE


def test_remote_stale_falls_back_local(reader):
    """远程断流超时：读取自动回落本机 local"""
    reader.process_serial_line(_weight_line(30.0), source=LOCAL_SOURCE)
    reader.process_serial_line(_weight_line(50.0), source=REMOTE_SOURCE)
    with reader.lock:
        reader.source_mono[REMOTE_SOURCE] -= REMOTE_FRESH_SECONDS + 1
    data = reader.get_weight_data()
    assert data["raw"] == pytest.approx(30.0, abs=0.1)
    assert data["source"] == LOCAL_SOURCE


def test_tare_capture_works_over_remote_line(reader):
    """去皮采样吃的是远程行的原始读数（管线与本地一致）"""
    reader.process_serial_line(_weight_line(10.0), source=REMOTE_SOURCE)
    reader.request_tare()
    # 去皮窗口内继续来远程行（同值）→ 窗口结束基准=10g，读数归零
    for _ in range(8):
        reader.process_serial_line(_weight_line(10.0), source=REMOTE_SOURCE)
    data = reader.get_weight_data()
    assert data["weight"] == 0


def test_remote_no_data_returns_zero_defaults(reader):
    """无任何源：初始零值，timestamp None，下游新鲜度判定判无效"""
    data = reader.get_weight_data()
    assert data["weight"] == 0.0
    assert data["timestamp"] is None


def test_remote_first_connection_zeroes_baseline(reader, monkeypatch):
    """远程首连自动去皮（2026-09-21 起带空秤守卫）：
    小偏移（空秤温漂级，≤守卫 50g）被采样窗口吃掉；秤上有物（>50g）拒绝去皮——
    后者正是"后端重启首连恰逢载荷被记成零点，放 300g 显示 1g"事故的防线。"""
    import app.weight as weight_mod

    monkeypatch.setattr(weight_mod, "_get_reader", lambda: reader)
    result = weight_mod.ensure_remote_zero_baseline()
    assert result["triggered"] is True

    # 首批远程行（30g 固有偏移，守卫内）→ 窗口结算后基准=30，读数归零
    for _ in range(8):
        reader.process_serial_line(_weight_line(30.0), source=REMOTE_SOURCE)
    data = reader.get_weight_data()
    assert data["weight"] == 0
    assert data["raw"] == pytest.approx(0.0, abs=0.1)

    # 幂等：已有数据后不再触发（断流重连不能把秤上载荷误当零点）
    again = weight_mod.ensure_remote_zero_baseline()
    assert again["triggered"] is False


def test_remote_first_connection_rejects_load(reader, monkeypatch):
    """首连恰逢秤上有大载荷（300g）：自动去皮被守卫拒绝，基准保持 0——
    显示带偏移（用户看得见/可手动去皮），绝不静默吞载荷（2026-09-21 事故修复）。"""
    import app.weight as weight_mod

    monkeypatch.setattr(weight_mod, "_get_reader", lambda: reader)
    assert weight_mod.ensure_remote_zero_baseline()["triggered"] is True
    for _ in range(8):
        reader.process_serial_line(_weight_line(300.0), source=REMOTE_SOURCE)
    # 去皮被拒绝：读数如实显示 300g，基准未被污染
    assert reader.get_weight_data()["weight"] == 300
    assert reader._soft_zero_offset == 0.0


def test_remote_reconnect_preserves_baseline(reader, monkeypatch):
    """断流重连（已有数据）：不重复去皮，基准保留"""
    import app.weight as weight_mod

    monkeypatch.setattr(weight_mod, "_get_reader", lambda: reader)
    weight_mod.ensure_remote_zero_baseline()
    for _ in range(8):
        reader.process_serial_line(_weight_line(20.0), source=REMOTE_SOURCE)
    assert reader.get_weight_data()["weight"] == 0
    # 模拟 Pi 重连（ensure 再次调用）→ 基准不变，放上 50g 物体读数 ≈ 50
    weight_mod.ensure_remote_zero_baseline()
    for _ in range(3):
        reader.process_serial_line(_weight_line(70.0), source=REMOTE_SOURCE)
    assert reader.get_weight_data()["weight"] == 50


def test_valve_remote_routing(monkeypatch):
    """Pi 通道在线：开/关阀命令转发远程（fire-and-forget），不碰本机串口"""
    sent = []

    def fake_send(action, channel=0, keepalive=False):
        sent.append((action, channel))
        return {"ok": True, "action": action, "channel": channel}

    monkeypatch.setattr(remote_sensors, "is_connected", lambda: True)
    monkeypatch.setattr(remote_sensors, "send_valve_command", fake_send)

    assert valve.valve_open() is True
    assert valve.valve_close() is True
    assert valve.valve_is_ready() is True
    # 开阀默认通道 1（原下料阀）；关阀默认 0 = 全通道广播（多阀时防漏关遗留通电）
    assert sent == [("open", 1), ("close", 0)]


def test_valve_channel_routing(monkeypatch):
    """双阀（2026-09-15）：显式 channel 透传到远程通道"""
    sent = []
    monkeypatch.setattr(remote_sensors, "is_connected", lambda: True)
    monkeypatch.setattr(
        remote_sensors, "send_valve_command",
        lambda action, channel=0, keepalive=False: sent.append((action, channel)) or {"ok": True},
    )
    assert valve.valve_open(channel=2) is True
    assert valve.valve_close(channel=2) is True
    assert valve.valve_query_state(channel=2) is None   # 远程状态查询异步回报，一律 None
    assert sent == [("open", 2), ("close", 2), ("status", 2)]


def test_valve_remote_dispatch_failure(monkeypatch):
    """远程调度失败（如 loop 未就绪）：开阀如实返回 False，不静默假成功"""
    monkeypatch.setattr(remote_sensors, "is_connected", lambda: True)
    monkeypatch.setattr(remote_sensors, "send_valve_command",
                        lambda a, channel=0, keepalive=False: {"ok": False, "error": "loop_not_ready"})
    assert valve.valve_open() is False


def test_valve_remote_not_connected_falls_back(monkeypatch):
    """Pi 未连接：回落本机串口路径（此测试只验证路由判断，不真开串口）"""
    monkeypatch.setattr(remote_sensors, "is_connected", lambda: False)
    # 打开本机路径的串口桩：_current_protocol 返回 single，_send 桩记录并成功
    monkeypatch.setattr(valve, "_current_protocol", lambda: "single")
    sent = []
    monkeypatch.setattr(valve, "_send", lambda ch: sent.append(ch) or True)
    assert valve.valve_open() is True
    assert valve.valve_close() is True
    assert sent == ["O", "F"]
