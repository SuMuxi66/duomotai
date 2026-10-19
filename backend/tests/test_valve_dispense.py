# -*- coding: utf-8 -*-
"""电磁阀控制器与下料双通道集成测试（全部 monkeypatch，绝不开真实串口）。

协议以实测可用的 电磁阀/valve_control.py 为准（2026-09-07 现场实测）：
- 指令字节流正确（单字节 'O'=开阀 / 'F'=关阀，无行尾、无应答校验）
- 探测：VALVE_PORT 直连写 'F' 自检；自动探测 STATUS 应答识别（OK/ON/OFF/ERR，秤数据行不误认）
- 自愈：写失败断开串口返回 False
- dispense 集成（valve 通道）：start_dispense -> _monitor_loop 达标路径调用 valve_open/valve_close
- dispense 连续发送失败 3 次中止路径
- legacy_relay 回退通道冒烟
"""
import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from app import valve as valve_mod
from app import dispense as dispense_mod
from app.config import settings as app_settings


@pytest.fixture(autouse=True)
def _reset_valve_globals(monkeypatch):
    """每个测试开始前重置 valve 模块全局串口与协议，避免测试间串扰。"""
    monkeypatch.setattr(valve_mod, "_ser", None)
    monkeypatch.setattr(valve_mod, "_port", None)
    monkeypatch.setattr(valve_mod, "_protocol", None)


# ==================== Fake Serial ====================


class FakeSerial:
    """模拟电磁阀串口：捕获 write 字节、按预设应答队列回行（探测用）。"""

    def __init__(self, responses=None, port="COM_FAKE"):
        self.port = port
        self.is_open = True
        self.writes = []          # 捕获所有 write 的原始字节
        self.responses = deque(responses or [])  # 应答行队列（每行不含 \r\n）
        self.write_fail = False   # 设 True 模拟写失败

    def write(self, data):
        if self.write_fail:
            raise OSError("write failed (simulated)")
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        if self.responses:
            line = self.responses.popleft()
            return (line + "\r\n").encode("ascii")
        return b""

    @property
    def in_waiting(self):
        if self.responses:
            return len((self.responses[0] + "\r\n").encode("ascii"))
        return 0

    def reset_input_buffer(self):
        self.responses.clear()

    def close(self):
        self.is_open = False


def _setup_valve_serial(monkeypatch, fake_serial):
    """将 valve_mod 的全局串口替换为 fake_serial，跳过 find_port。"""
    monkeypatch.setattr(valve_mod, "_ser", fake_serial)
    monkeypatch.setattr(valve_mod, "_port", fake_serial.port)
    # find_port 不应被调用（_ser 已非 None）；防止意外回退到真实探测
    monkeypatch.setattr(valve_mod, "find_port", lambda: False)


# ==================== 指令字节流（单字符协议） ====================


def test_valve_open_sends_single_byte_O(monkeypatch):
    """开阀 = 写单字节 'O'，无行尾、不依赖应答（与 valve_control.py 一致）。"""
    fake = FakeSerial()
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open() is True
    assert fake.writes[-1] == b"O"


def test_valve_close_sends_single_byte_F(monkeypatch):
    """关阀 = 写单字节 'F'，无行尾、不依赖应答。"""
    fake = FakeSerial()
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_close() is True
    assert fake.writes[-1] == b"F"


def test_valve_commands_never_send_full_word_or_newline(monkeypatch):
    """永不出全词 ON/OFF 或 \\r\\n 行尾——单字符固件只认裸 'O'/'F'。"""
    fake = FakeSerial()
    _setup_valve_serial(monkeypatch, fake)
    valve_mod.valve_open()
    valve_mod.valve_close()
    for w in fake.writes:
        assert w not in (b"ON", b"OFF", b"ON\r\n", b"OFF\r\n", b"O\r\n", b"F\r\n")


# ==================== 开阀确认（STATUS 查询 + 重发，2026-09-09） ====================


class FakeSerialAck(FakeSerial):
    """有应答固件模拟：reset_input_buffer 不清预设应答——应答字节在指令之后到达。"""

    def reset_input_buffer(self):
        pass


def test_valve_query_state_parses_responses(monkeypatch):
    """STATUS 查询：ON/OFF 正确解析；ERR/静默返回 None（无法确认，不代表失败）。"""
    monkeypatch.setattr(valve_mod, "_STATUS_WAIT_S", 0.01)
    cases = [(["ON"], "ON"), (["OFF"], "OFF"), (["ERR"], None), ([], None)]
    for responses, expected in cases:
        fake = FakeSerialAck(responses=list(responses))
        _setup_valve_serial(monkeypatch, fake)
        assert valve_mod.valve_query_state() == expected
        assert b"STATUS\r\n" in fake.writes


def test_valve_open_verified_confirms_on_first_ack(monkeypatch):
    """固件应答 ON：单次发送即确认开启，立即返回。"""
    fake = FakeSerialAck(responses=["ON"])
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is True
    assert fake.writes.count(b"O") == 1


def test_valve_open_verified_retries_until_on(monkeypatch):
    """前两次应答 OFF、第三次 ON：重发直到状态为 ON。"""
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerialAck(responses=["OFF", "OFF", "ON"])
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is True
    assert fake.writes.count(b"O") == 3


def test_valve_open_verified_silent_firmware_best_effort(monkeypatch):
    """静默固件（当前实机）：无应答时重发 3 次，达上限后按写成功放行（兼容旧语义）。"""
    monkeypatch.setattr(valve_mod, "_STATUS_WAIT_S", 0.01)
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerial()
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is True
    assert fake.writes.count(b"O") == 3
    assert fake.writes.count(b"STATUS\r\n") == 3


def test_valve_open_verified_fails_when_always_off(monkeypatch):
    """有应答但始终 OFF（板卡明确没开）：达重发上限后如实失败。"""
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerialAck(responses=["OFF", "OFF", "OFF"])
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is False
    assert fake.writes.count(b"O") == 3


def test_valve_open_verified_write_failure(monkeypatch):
    """串口写入失败：立即失败。"""
    fake = FakeSerial()
    fake.write_fail = True
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is False


def test_dispense_pulse_fails_when_verified_open_fails(monkeypatch, valve_channel):
    """pulse_dispense：开阀确认失败时如实返回失败（不再谎报已打开）。"""
    monkeypatch.setattr(valve_mod, "valve_is_ready", lambda: True)
    monkeypatch.setattr(valve_mod, "valve_open_verified", lambda: False)
    monkeypatch.setattr(valve_mod, "valve_close", lambda: True)
    result = asyncio.run(dispense_mod.pulse_dispense(0.01))
    assert result["success"] is False


def test_dispense_start_fails_when_verified_open_fails(monkeypatch, valve_channel):
    """start_dispense：开阀确认失败时不创建监测任务、返回失败。"""
    monkeypatch.setattr(valve_mod, "valve_is_ready", lambda: True)
    monkeypatch.setattr(valve_mod, "valve_open_verified", lambda: False)
    monkeypatch.setattr(valve_mod, "valve_close", lambda: True)
    monkeypatch.setattr(dispense_mod, "_now_weight", lambda: 0.0)

    async def _run():
        return await dispense_mod.start_dispense(3.0)

    result = asyncio.run(_run())
    assert result["success"] is False
    assert dispense_mod._task is None
    with dispense_mod._state_lock:
        assert dispense_mod._state["active"] is False
        assert dispense_mod._state["reason"] == "error"


def test_stop_dispense_closes_valve_even_without_task(monkeypatch, valve_channel):
    """stop_dispense 无进行中任务时也强制关阀（防 reload 遗留开阀态烧阀，2026-09-09）。"""
    closed = []

    def fake_valve_close():
        closed.append(1)
        return True

    monkeypatch.setattr(valve_mod, "valve_close", fake_valve_close)
    monkeypatch.setattr(dispense_mod, "_task", None)
    result = asyncio.run(dispense_mod.stop_dispense())
    assert result["success"] is True
    assert closed == [1]  # 没有任务也发了关阀指令


def test_stop_dispense_reports_close_failure(monkeypatch, valve_channel):
    """关阀指令发送失败时如实返回失败（板卡连接问题不能静默）。"""
    monkeypatch.setattr(valve_mod, "valve_close", lambda: False)
    monkeypatch.setattr(dispense_mod, "_task", None)
    result = asyncio.run(dispense_mod.stop_dispense())
    assert result["success"] is False
    assert "关阀指令发送失败" in result["error"]


# ==================== 全词协议（重烧 .ino 固件后连接时自动启用） ====================


def _setup_full_word(monkeypatch, fake):
    """单字符测试夹具 + 强制全词协议。"""
    _setup_valve_serial(monkeypatch, fake)
    monkeypatch.setattr(valve_mod, "_protocol", "full")


def test_valve_open_full_word_sends_ON_with_crlf(monkeypatch):
    """全词模式：开阀发 ON\\r\\n 并校验 OK 应答。"""
    fake = FakeSerialAck(responses=["OK"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_open() is True
    assert b"ON\r\n" in fake.writes


def test_valve_close_full_word_sends_OFF(monkeypatch):
    """全词模式：关阀发 OFF\\r\\n 并校验 OK 应答。"""
    fake = FakeSerialAck(responses=["OK"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_close() is True
    assert b"OFF\r\n" in fake.writes


def test_valve_open_full_word_err_ack_fails(monkeypatch):
    """全词模式：应答 ERR → 开阀失败（绝不回退盲发裸 'O'）。"""
    fake = FakeSerialAck(responses=["ERR"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_open() is False
    assert b"O" not in [w for w in fake.writes if len(w) == 1]  # 没有裸单字符指令


def test_valve_open_verified_full_word_double_confirm(monkeypatch):
    """全词模式开阀确认：ON 的 OK 应答 + STATUS 查询 ON 双重确认，单次即成功。"""
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerialAck(responses=["OK", "ON"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is True
    assert fake.writes.count(b"ON\r\n") == 1


def test_valve_open_verified_full_word_retry_then_confirm(monkeypatch):
    """全词模式：首次 ERR，重发后 OK+ON → 确认成功。"""
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerialAck(responses=["ERR", "OK", "ON"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is True
    assert fake.writes.count(b"ON\r\n") == 2


def test_valve_open_verified_full_word_all_err_fails(monkeypatch):
    """全词模式：始终 ERR → 最终状态查询也非 ON → 如实失败。"""
    monkeypatch.setattr(valve_mod, "_OPEN_RETRY_DELAY_S", 0)
    fake = FakeSerialAck(responses=["ERR", "ERR", "ERR", "ERR"])
    _setup_full_word(monkeypatch, fake)
    assert valve_mod.valve_open_verified() is False


def test_find_port_direct_detects_full_protocol(monkeypatch):
    """直连时 STATUS 有应答 → 识别为全词协议；且不做 'F' 自检（会污染全词行缓冲）。"""
    fake = FakeSerialAck(responses=["ON"])
    monkeypatch.setattr(app_settings, "valve_port", "COM_FAKE")
    monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
    monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud: fake)
    monkeypatch.setattr(valve_mod, "_PROTOCOL_WAIT_S", 0.01)
    assert valve_mod.find_port() is True
    assert valve_mod._protocol == "full"
    assert b"STATUS\r\n" in fake.writes
    assert b"F" not in fake.writes


def test_find_port_direct_silent_detects_char_protocol(monkeypatch):
    """直连时 STATUS 静默 → 识别为单字符协议，保留 'F' 自检。"""
    fake = FakeSerial()
    monkeypatch.setattr(app_settings, "valve_port", "COM_FAKE")
    monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
    monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud: fake)
    monkeypatch.setattr(valve_mod, "_PROTOCOL_WAIT_S", 0.01)
    assert valve_mod.find_port() is True
    assert valve_mod._protocol == "char"
    assert b"F" in fake.writes


# ==================== 探测 ====================


def test_find_port_direct_writes_F_selfcheck(monkeypatch):
    """VALVE_PORT 指定端口：直连 + 写 'F' 安全自检（F 不会开启任何设备）。"""
    fake = FakeSerial(port="COM_FAKE")
    monkeypatch.setattr(app_settings, "valve_port", "COM_FAKE")
    monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
    monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud: fake)
    assert valve_mod.find_port() is True
    assert valve_mod._ser is fake
    assert b"F" in fake.writes


def test_probe_accepts_OK_and_ERR(monkeypatch):
    """自动探测：STATUS 应答命中 OK/ON/OFF/ERR 任一即认定（ERR=单字符固件回未知指令）。"""
    for resp in ("OK", "ON", "OFF", "ERR"):
        fake = FakeSerial(responses=[resp])
        monkeypatch.setattr(app_settings, "valve_port", "")
        monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
        monkeypatch.setattr(
            valve_mod.serial.tools.list_ports, "comports",
            lambda: [SimpleNamespace(device="COM_FAKE")],
        )
        monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud, _f=fake: _f)
        assert valve_mod.find_port() is True, f"应答 {resp} 应被认定为电磁阀"
        assert fake.writes[-1] == b"STATUS\r\n"


def test_probe_skips_weight_line(monkeypatch):
    """自动探测：秤的数据行（wt: ...）不命中探测 token -> 不误认 -> find_port False。"""
    fake = FakeSerial(responses=["wt: 123.4 g"])
    monkeypatch.setattr(app_settings, "valve_port", "")
    monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
    monkeypatch.setattr(
        valve_mod.serial.tools.list_ports, "comports",
        lambda: [SimpleNamespace(device="COM_FAKE")],
    )
    monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud: fake)
    monkeypatch.setattr(valve_mod, "_DEFAULT_TIMEOUT", 0.05)  # 加速：无更多应答行轮询到超时
    assert valve_mod.find_port() is False
    assert valve_mod._ser is None


# ==================== 探测：静默固件（单字符协议不回 STATUS 应答） ====================


def _patch_probe_env(monkeypatch, fakes):
    """探测环境：VALVE_PORT 为空，comports 依次返回 fakes 对应的口。"""
    monkeypatch.setattr(app_settings, "valve_port", "")
    monkeypatch.setattr(app_settings, "valve_baudrate", 9600)
    monkeypatch.setattr(valve_mod, "_DEFAULT_TIMEOUT", 0.05)
    devices = [SimpleNamespace(device=f.port) for f in fakes]
    monkeypatch.setattr(
        valve_mod.serial.tools.list_ports, "comports", lambda: devices,
    )
    it = iter(fakes)
    monkeypatch.setattr(valve_mod, "_open_serial", lambda port, baud: next(it))


def test_probe_silent_single_candidate_inferred(monkeypatch):
    """唯一可开候选对 STATUS 静默 -> 推断为电磁阀（另一口通常被秤占用打不开）。"""
    fake = FakeSerial(responses=[], port="COM_VALVE")  # 完全静默
    _patch_probe_env(monkeypatch, [fake])
    assert valve_mod.find_port() is True
    assert valve_mod._ser is fake
    assert valve_mod._port == "COM_VALVE"


def test_probe_silent_two_candidates_not_inferred(monkeypatch):
    """两个口都静默 -> 无法区分，不乱认，全部关闭。"""
    fake1 = FakeSerial(responses=[], port="COM_A")
    fake2 = FakeSerial(responses=[], port="COM_B")
    _patch_probe_env(monkeypatch, [fake1, fake2])
    assert valve_mod.find_port() is False
    assert valve_mod._ser is None
    assert fake1.is_open is False and fake2.is_open is False


def test_probe_weight_line_plus_silent_infer_silent_one(monkeypatch):
    """现场典型布局：秤口吐 Weight 行（排除），剩唯一静默口 -> 推断为阀。"""
    scale = FakeSerial(responses=["Weight=-7.6g  RAW=8226270"], port="COM_SCALE")
    valve = FakeSerial(responses=[], port="COM_VALVE")
    _patch_probe_env(monkeypatch, [scale, valve])
    assert valve_mod.find_port() is True
    assert valve_mod._ser is valve
    assert valve_mod._port == "COM_VALVE"
    assert scale.is_open is False  # 秤口已排除并关闭


def test_valve_is_ready_with_connection(monkeypatch):
    """valve_is_ready：串口已连接即就绪（不发探测指令，O/F 都不能当探针乱发）。"""
    fake = FakeSerial()
    _setup_valve_serial(monkeypatch, fake)
    assert valve_mod.valve_is_ready() is True
    assert fake.writes == []  # 就绪检查不发任何指令


def test_valve_is_ready_reconnects(monkeypatch):
    """valve_is_ready：串口为 None 时尝试重连。"""
    monkeypatch.setattr(valve_mod, "_ser", None)
    monkeypatch.setattr(valve_mod, "find_port", lambda: True)
    assert valve_mod.valve_is_ready() is True


def test_write_failure_self_heals(monkeypatch):
    """写失败 -> 关闭串口 -> 重连失败 -> 返回 False（自愈路径）。"""
    fake = FakeSerial()
    fake.write_fail = True
    _setup_valve_serial(monkeypatch, fake)
    monkeypatch.setattr(valve_mod, "find_port", lambda: False)  # 重连失败
    assert valve_mod.valve_open() is False
    assert fake.is_open is False  # 串口已被关闭等待重连


# ==================== dispense 集成（valve 通道） ====================


@pytest.fixture
def valve_channel(monkeypatch):
    """将 dispense 切到 valve 通道并重置状态。"""
    monkeypatch.setattr(dispense_mod.settings, "dispense_executor", "valve")
    # 重置 dispense 内部状态
    monkeypatch.setattr(dispense_mod, "_task", None)
    with dispense_mod._state_lock:
        dispense_mod._state.update({
            "active": False, "target": 0.0, "target_g": 0.0,
            "started_weight": 0.0, "started_at": 0.0,
            "finished": False, "finished_weight": 0.0, "reason": "",
        })


def test_dispense_valve_start_and_complete(monkeypatch, valve_channel):
    """valve 通道：start_dispense -> _monitor_loop 达标路径调用 valve_open/valve_close。"""
    call_log = []

    def fake_valve_is_ready():
        return True

    def fake_valve_open(keepalive=False):
        call_log.append("open")
        return True

    def fake_valve_close():
        call_log.append("close")
        return True

    monkeypatch.setattr(valve_mod, "valve_is_ready", fake_valve_is_ready)
    monkeypatch.setattr(valve_mod, "valve_open", fake_valve_open)
    monkeypatch.setattr(valve_mod, "valve_open_verified", fake_valve_open)
    monkeypatch.setattr(valve_mod, "valve_close", fake_valve_close)

    # 模拟重量：首次读 0（started_weight），第二次读达标（target=5g -> 5.0 >= 5.0-0.8=4.2 且 > 0）
    weights = iter([0.0, 5.0])
    monkeypatch.setattr(dispense_mod, "_now_weight", lambda: next(weights))

    async def _run():
        result = await dispense_mod.start_dispense(5.0)
        assert result["success"] is True
        # 等待 monitor_loop 完成（在同一事件循环内）
        if dispense_mod._task and not dispense_mod._task.done():
            await asyncio.wait_for(dispense_mod._task, timeout=5)

    asyncio.run(_run())

    assert "open" in call_log   # 启动下料
    assert "close" in call_log  # 达标后关阀


def test_dispense_valve_fail_3_aborts(monkeypatch, valve_channel):
    """valve 通道：连续发送失败 3 次中止下料。"""
    call_count = {"n": 0}

    def fake_valve_is_ready():
        return True

    def fake_valve_open(keepalive=False):
        call_count["n"] += 1
        return False  # 始终失败

    def fake_valve_close():
        return True

    monkeypatch.setattr(valve_mod, "valve_is_ready", fake_valve_is_ready)
    monkeypatch.setattr(valve_mod, "valve_open", fake_valve_open)
    monkeypatch.setattr(valve_mod, "valve_open_verified", lambda: True)  # 首次开阀确认成功，失败交给 monitor 循环
    monkeypatch.setattr(valve_mod, "valve_close", fake_valve_close)
    monkeypatch.setattr(dispense_mod, "_now_weight", lambda: 0.0)

    async def _run():
        result = await dispense_mod.start_dispense(3.0)
        assert result["success"] is True  # start_dispense 本身成功（任务已创建）
        # 等待 monitor_loop 完成（在同一事件循环内）
        if dispense_mod._task and not dispense_mod._task.done():
            await asyncio.wait_for(dispense_mod._task, timeout=5)

    asyncio.run(_run())

    assert call_count["n"] >= 3
    with dispense_mod._state_lock:
        assert dispense_mod._state["finished"] is True
        assert dispense_mod._state["reason"] == "error"


def test_dispense_valve_pulse(monkeypatch, valve_channel):
    """valve 通道：pulse_dispense 调用 valve_open/valve_close（软件脉冲，固件无 PULSE 指令）。"""
    call_log = []

    def fake_valve_is_ready():
        return True

    def fake_valve_open(keepalive=False):
        call_log.append("open")
        return True

    def fake_valve_close():
        call_log.append("close")
        return True

    monkeypatch.setattr(valve_mod, "valve_is_ready", fake_valve_is_ready)
    monkeypatch.setattr(valve_mod, "valve_open", fake_valve_open)
    monkeypatch.setattr(valve_mod, "valve_open_verified", fake_valve_open)
    monkeypatch.setattr(valve_mod, "valve_close", fake_valve_close)
    # 缩短 sleep 以加速测试
    monkeypatch.setattr(dispense_mod, "POLL_INTERVAL", 0.01)
    # pulse_dispense 内部 asyncio.sleep(duration_s)——用 0.01
    result = asyncio.run(dispense_mod.pulse_dispense(0.01))
    assert result["success"] is True
    assert "open" in call_log
    assert "close" in call_log


# ==================== legacy_relay 回退通道冒烟 ====================


@pytest.fixture
def legacy_channel(monkeypatch):
    """将 dispense 切到 legacy_relay 通道并重置状态。"""
    monkeypatch.setattr(dispense_mod.settings, "dispense_executor", "legacy_relay")
    monkeypatch.setattr(dispense_mod, "_task", None)
    with dispense_mod._state_lock:
        dispense_mod._state.update({
            "active": False, "target": 0.0, "target_g": 0.0,
            "started_weight": 0.0, "started_at": 0.0,
            "finished": False, "finished_weight": 0.0, "reason": "",
        })


def test_dispense_legacy_relay_smoke(monkeypatch, legacy_channel):
    """legacy_relay 通道：start_dispense 走 relay_control('off'/'on')。"""
    relay_calls = []

    def fake_relay_control(action):
        relay_calls.append(action)
        return {"status": "ok", "action": action}

    async def fake_wait_ready(timeout=15.0):
        return True

    monkeypatch.setattr(dispense_mod, "relay_control", fake_relay_control)
    monkeypatch.setattr(dispense_mod, "_wait_serial_ready", fake_wait_ready)

    # 模拟重量：首次读 0（started_weight），第二次读达标
    weights = iter([0.0, 4.0])
    monkeypatch.setattr(dispense_mod, "_now_weight", lambda: next(weights))
    monkeypatch.setattr(dispense_mod, "POLL_INTERVAL", 0.01)

    async def _run():
        result = await dispense_mod.start_dispense(3.0)
        assert result["success"] is True
        if dispense_mod._task and not dispense_mod._task.done():
            await asyncio.wait_for(dispense_mod._task, timeout=5)

    asyncio.run(_run())

    # 旧通道：下料='off'（='0'=NC导通=通电），停止='on'（='1'=吸合=断电）
    assert "off" in relay_calls
    assert "on" in relay_calls


def test_dispense_legacy_relay_pulse(monkeypatch, legacy_channel):
    """legacy_relay 通道：pulse_dispense 走 relay_control('off'/'on')。"""
    relay_calls = []

    def fake_relay_control(action):
        relay_calls.append(action)
        return {"status": "ok", "action": action}

    async def fake_wait_ready(timeout=15.0):
        return True

    monkeypatch.setattr(dispense_mod, "relay_control", fake_relay_control)
    monkeypatch.setattr(dispense_mod, "_wait_serial_ready", fake_wait_ready)

    result = asyncio.run(dispense_mod.pulse_dispense(0.01))
    assert result["success"] is True
    # 2026-09-16 起 pulse_dispense 不再先 stop_dispense（改为互斥拒绝），故只有 off/on
    assert relay_calls == ["off", "on"]


# ==================== _wait_serial_ready 通道分流 ====================


def test_wait_serial_ready_valve_channel(monkeypatch, valve_channel):
    """valve 通道 _wait_serial_ready 调用 valve_is_ready。"""
    called = {"n": 0}

    def fake_valve_is_ready():
        called["n"] += 1
        return True

    monkeypatch.setattr(valve_mod, "valve_is_ready", fake_valve_is_ready)
    result = asyncio.run(dispense_mod._wait_serial_ready(timeout=2.0))
    assert result is True
    assert called["n"] >= 1


def test_wait_serial_ready_legacy_channel(monkeypatch, legacy_channel):
    """legacy_relay 通道 _wait_serial_ready 调用 relay_control('on')。"""
    called = {"n": 0}

    def fake_relay_control(action):
        called["n"] += 1
        assert action == "on"
        return {"status": "ok"}

    monkeypatch.setattr(dispense_mod, "relay_control", fake_relay_control)
    result = asyncio.run(dispense_mod._wait_serial_ready(timeout=2.0))
    assert result is True
    assert called["n"] >= 1
