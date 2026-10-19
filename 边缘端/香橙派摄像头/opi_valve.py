"""opi_valve.py — 电磁阀串口执行器（多通道 + 开阀故障切换）（2026-09-21 从 opi_sensors_client.py 拆出）。

纯搬运：类与函数体和拆分前完全一致（含全部事故注释）。
跨模块引用 classify 的私有名（_port_acks_close）与 common 的私有常量（_OPEN_ACK_WAIT_S 等）
刻意保持原名，避免重命名引入行为风险。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

import serial

from opi_serial_common import (
    FAILOVER_SPEC,
    OPEN_CONFIRM_TIMEOUT_S,
    OPEN_MAX_ATTEMPTS,
    OPEN_RETRY_DELAY_S,
    STATUS_TIMEOUT_S,
    VALVE_BAUD,
    VALVE_PROTOCOL,
    _OPEN_ACK_WAIT_S,
    _RECLASSIFY_MIN_INTERVAL_S,
    _SILENT_STREAK_LIMIT,
    log_opi_sensors as log,
)
from opi_serial_classify import (
    _port_acks_close,
    assign_channels,
    candidate_ports,
    classify_ports,
    invalidate_role_archive,
    spec_matches,
    usb_topology,
)


def env_valve_device(channel: int) -> str:
    """应急钉死某通道的口：通道1 用 OPI_VALVE_DEVICE，通道N 用 OPI_VALVE{N}_DEVICE"""
    if channel <= 1:
        return os.getenv("OPI_VALVE_DEVICE", "")
    return os.getenv(f"OPI_VALVE{channel}_DEVICE", "")


def valve_name(channel: int) -> str:
    """对外的中文名（用户要求「备注电磁阀2」）"""
    return f"电磁阀{channel}"


class Valve:
    """单个电磁阀串口执行器（一个通道一个实例，通道号见 assign_channels）。

    口按应答特征分类（'F' → OK OFF 才认定），开阀带 ACK 校验 + 重发
    （逻辑与后端 valve.py 一致，搬到串口所在侧执行）。"""

    def __init__(self, channel: int):
        self.channel = channel
        self.name = valve_name(channel)           # 「电磁阀1」「电磁阀2」…（对用户/日志）
        self.ser: Optional[serial.Serial] = None
        self.protocol: str = VALVE_PROTOCOL if VALVE_PROTOCOL in ("single", "word") else "single"
        self._known_port: Optional[str] = None   # 最近一次验证通过的口，重连优先试它
        self.silent_streak = 0                    # 连续无 ACK 次数（达到阈值丢弃已知口强制重分类）
        self._last_reclassify_at = 0.0            # 上次"因无应答而重分类"的时间（冷却用）
        self._probe_fail_streak = 0               # 连续"连口验证失败"次数（档案口上不是阀）

    def _resolve_port(self) -> Optional[str]:
        """本通道该用哪个口：应急钉死 > 通道绑定表（按 USB 拓扑，编号洗牌不影响）"""
        spec = env_valve_device(self.channel)
        if spec:
            for dev in candidate_ports():
                if spec_matches(dev, spec):
                    return dev
            return spec          # 匹配不到就按原样试，失败由 _probe 兜底
        valve_ports, _ = classify_ports(force=self.silent_streak >= 2)
        return assign_channels(valve_ports).get(self.channel)

    def _probe(self, device: str) -> bool:
        """打开口并做阀身份验证：发 'F'（幂等关阀）→ 应答 OK OFF 才认定。
        旧逻辑"能打开就放行"在断电重排号后会锁错口——2026-09-14 全天 SILENT 事故根因。"""
        try:
            ser = serial.Serial(port=device, baudrate=VALVE_BAUD,
                                bytesize=8, parity="N", stopbits=1, timeout=0.5)
        except Exception:
            return False
        try:
            if not _port_acks_close(ser):
                log.info(f"阀口验证 {device}: 无 OK OFF 应答，非阀口，跳过")
                ser.close()
                return False
            # 协议选择：只认显式 env（word），默认一律 single——
            # 实测 2026-09-12 该板单字符 O/F 可用且回 OK ON/OK OFF。
            self.protocol = "word" if VALVE_PROTOCOL == "word" else "single"
            log.info(f"{self.name} 阀口验证 {device}（USB {usb_topology(device)}）: OK OFF → 协议 {self.protocol}")
            self.ser = ser
            self._known_port = device
            return True
        except Exception:
            ser.close()
            return False

    def connect(self) -> bool:
        if self.ser and self.ser.is_open:
            return True
        ok = False
        if self._known_port and self._probe(self._known_port):
            ok = True
        else:
            device = self._resolve_port()
            ok = bool(device) and self._probe(device)
        if ok:
            self._probe_fail_streak = 0
            log.info(
                f"{self.name} 就绪: {self.ser.port}（USB {usb_topology(self.ser.port)}）"
                f" @ {VALVE_BAUD}（协议: {self.protocol}）"
            )
        else:
            self._note_probe_fail()
        return ok

    def _note_probe_fail(self) -> None:
        """连口验证连续失败：档案映射的口上不是阀（板卡被插到别的 hub 口/板卡掉线）。

        这种失败不经过 ACK 环节，_note_silent 永远触发不了；而档案的"设备集合未变"
        比对只看拓扑段集合——板卡换了物理插位集合不变，会永远沿用错口（2026-09-19
        实测：阀1 板卡被插到别的 hub 口，档案钉着 7-1.4，探测无应答，阀1 永久
        not_ready）。连续 _SILENT_STREAK_LIMIT 次失败 → 让档案失效走全量重扫，
        按应答特征重新认口（断电重排号/换口都功能对应）。"""
        self._probe_fail_streak += 1
        if self._probe_fail_streak < _SILENT_STREAK_LIMIT:
            return
        now = time.monotonic()
        if now - self._last_reclassify_at < _RECLASSIFY_MIN_INTERVAL_S:
            return
        self._last_reclassify_at = now
        self._probe_fail_streak = 0
        self._known_port = None
        invalidate_role_archive(
            f"{self.name} 连续 {_SILENT_STREAK_LIMIT} 次找不到可用阀口（拓扑档案可能与物理插位不符）"
        )

    def _scan_state(self, seconds: float) -> Optional[str]:
        """在输入流中扫描阀状态应答。板卡（新版下料机一体板）持续输出称重数据行，
        指令应答 'OK ON' / 'OK OFF' 混在数据流里，必须按子串搜（不能整行 startswith）。"""
        deadline = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < deadline:
            if self.ser.in_waiting:
                buf += self.ser.read(self.ser.in_waiting)
            text = buf.decode("utf-8", errors="ignore")
            if "OK ON" in text or "\r\nON" in text or text.startswith("ON"):
                return "ON"
            if "OK OFF" in text or "\r\nOFF" in text or text.startswith("OFF"):
                return "OFF"
            time.sleep(0.02)
        return None

    def _query_status(self) -> Optional[str]:
        try:
            self.ser.write(b"STATUS\r\n")
            self.ser.flush()
            return self._scan_state(STATUS_TIMEOUT_S)
        except Exception as e:
            log.warning(f"STATUS 查询失败: {e}")
            self._drop_dead_handle(f"STATUS 查询异常 {type(e).__name__}")
            return None

    def _drop_dead_handle(self, reason: str) -> None:
        """丢弃疑似死亡的串口句柄。USB 拔插后内核串口对象已换新（设备节点常同名），
        旧句柄读写必 EIO 而 is_open 仍为 True，会骗过 connect() 的快速路径一直撞墙
        （2026-09-19 拔插 USB 后阀失控全程 write EIO 直到重启服务）。丢弃后下次
        connect() 重新验证/重扫拿新句柄——拔插后下一条命令自动恢复，无需重启。"""
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        log.warning(f"{self.name} 串口句柄已丢弃（{reason}），下次命令自动重连")

    def _write(self, data: bytes) -> bool:
        try:
            self.ser.write(data)
            self.ser.flush()
            return True
        except Exception as e:
            log.warning(f"阀串口写入失败: {e}")
            self._drop_dead_handle(f"写入失败 {type(e).__name__}")
            return False

    def _note_silent(self) -> None:
        """无 ACK：可能是板卡掉线/锁错口，也可能只是命令太密、板卡来不及应答。

        连续 _SILENT_STREAK_LIMIT 次才丢弃已知口并断开、下次命令重新分类；且两次
        "因无应答重分类"之间加 _RECLASSIFY_MIN_INTERVAL_S 冷却——重分类要全扫 4 个口
        （约 8~10 秒），命令密集时若不冷却会陷入"重分类 → 期间新命令又 SILENT → 又重分类"
        的风暴，前端表现为越点越卡（2026-09-15 实测踩坑）。
        """
        self.silent_streak += 1
        if self.silent_streak < _SILENT_STREAK_LIMIT:
            return
        now = time.monotonic()
        if now - self._last_reclassify_at < _RECLASSIFY_MIN_INTERVAL_S:
            log.warning(
                f"{self.name} 连续 {self.silent_streak} 次无应答，但距上次重分类不足 "
                f"{_RECLASSIFY_MIN_INTERVAL_S:.0f}s，暂不重分类（沿用当前口）"
            )
            return
        self._last_reclassify_at = now
        log.warning(f"{self.name} 连续 {self.silent_streak} 次无应答，丢弃已知口，下次命令重新分类")
        self._known_port = None
        # 口径连续不应答 → 让角色档案也失效，下次按当前 USB 集合重新识别并重建档
        invalidate_role_archive(f"{self.name} 连续 {self.silent_streak} 次无应答")
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def open(self, deadline: Optional[float] = None) -> dict:
        """开阀：'O' 后扫输入流确认 'OK ON' 应答（该板卡对 O/F 带明确 ACK），
        非确认则重发，最多 OPEN_MAX_ATTEMPTS 次；全程无应答按静默固件放行
        （与后端 valve.py 语义一致），并计入无应答计数触发重新分类。

        deadline：绝对时间上限（time.monotonic()，故障切换的时限判据）——到点仍未
        确认 ON 即返回当前状态（state != "ON" 视为没启动）；None = 只按重发次数控制。
        """
        attempt = 0
        last_state: Optional[str] = None
        while attempt < OPEN_MAX_ATTEMPTS:
            if deadline is not None:
                remain = deadline - time.monotonic()
                if remain <= 0.2:
                    log.warning(
                        f"{self.name} 开阀时限已到（{OPEN_CONFIRM_TIMEOUT_S:.1f}s 内未确认），停止重发"
                    )
                    break
            attempt += 1
            if not self.connect():
                return {"ok": False, "error": "valve_serial_not_ready"}
            try:
                # 清残留应答（2026-09-15）：上一条指令——尤其只写不等应答的保活 'O'——
                # 可能留下未读的 "OK ON"/"OK OFF"，不清掉会被本次 _scan_state 当成本次
                # 结果，造成"关阀却回报 ON"/"开阀被误判没开"。
                self.ser.reset_input_buffer()
            except Exception:
                pass
            if self.protocol == "word":
                self._write(b"ON\r\n")
            elif not self._write(b"O"):
                # 写失败（USB 拔插后旧句柄 EIO 常见）：句柄已在 _write 里丢弃，
                # connect() 重拿新句柄再试，而不是直接放弃整次开阀
                continue
            wait = _OPEN_ACK_WAIT_S
            if deadline is not None:
                wait = max(0.2, min(_OPEN_ACK_WAIT_S, deadline - time.monotonic()))
            state = self._scan_state(wait)
            last_state = state
            if state == "ON":
                self.silent_streak = 0
                if attempt > 1:
                    log.info(f"{self.name} 开阀确认 ON（重发第 {attempt} 次后）")
                return {"ok": True, "state": "ON"}
            log.warning(f"{self.name} 开阀未收到 ON 确认（{state}），重发（{attempt}/{OPEN_MAX_ATTEMPTS}）")
            if attempt < OPEN_MAX_ATTEMPTS:
                time.sleep(OPEN_RETRY_DELAY_S)
        if last_state is None:
            self._note_silent()
            return {"ok": True, "state": "SILENT"}  # 与后端 valve.py 静默放行语义一致
        return {"ok": True, "state": last_state}

    def ping_open(self) -> dict:
        """保活重发：只写一次 'O' 立即返回（板卡对 O 幂等，重复写无副作用）。

        用于下料期间后端的周期保活重发——不等 'OK ON' 应答、不重发、不做故障切换、
        不触发重新分类，单条耗时 <10ms。否则按 0.5s 一条发（每条要等串口 ACK
        0.3~1s），Pi 的命令队列会持续积压，后面的手动关阀要排队数秒才生效
        （2026-09-15 实测同一秒积压 12 条）。
        未连接时不做重连/分类这类慢动作，交由下一次完整 open 恢复。
        """
        if not (self.ser and self.ser.is_open):
            return {"ok": False, "state": "KEEPALIVE_NO_PORT"}
        ok = self._write(b"ON\r\n") if self.protocol == "word" else self._write(b"O")
        return {"ok": bool(ok), "state": "KEEPALIVE" if ok else "KEEPALIVE_WRITE_FAIL"}

    def close(self) -> dict:
        """关阀（'F' 幂等）最多发 2 次：首次没等到应答就补发一次。

        补发是为了压缩"冷启动 / 命令密集时首条命令无应答"产生的 SILENT 窗口
        ——SILENT 会累积 silent_streak，可能进一步触发一次 8~10 秒的全量重分类。
        'F' 幂等，重复发送无副作用。

        SILENT 快速路径（2026-09-21）：对已连续 _SILENT_STREAK_LIMIT 次无应答的阀，
        写一次 'F' 成功即返回——慢路径等满 2×1s ACK 超时的最终结果同样是
        {'ok': True, 'state': 'SILENT'}，等待不增加任何安全性（板子只是不回话，
        'F' 写入即关），却把每次关阀广播拖慢 2.1s。板卡恢复应答后（silent_streak
        被任一次成功 ACK 清零）自动回到慢路径。
        """
        if self.silent_streak >= _SILENT_STREAK_LIMIT:
            if not self.connect():
                return {"ok": False, "error": "valve_serial_not_ready"}
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            if self._write(b"OFF\r\n" if self.protocol == "word" else b"F"):
                return {"ok": True, "state": "SILENT"}
            return {"ok": False, "error": "valve_write_failed"}
        for attempt in (1, 2):
            if not self.connect():
                return {"ok": False, "error": "valve_serial_not_ready"}
            try:
                # 清残留应答：否则可能把上一次开阀遗留的 "OK ON" 当成本次关阀结果，
                # 回报出 {"state": "ON"} 这种自相矛盾的状态。
                self.ser.reset_input_buffer()
            except Exception:
                pass
            ok = self._write(b"OFF\r\n") if self.protocol == "word" else self._write(b"F")
            if not ok:
                # 写失败（USB 拔插后旧句柄 EIO 常见）：句柄已丢弃，connect() 重拿
                # 新句柄补发——关阀是安全路径，绝不能因句柄陈旧直接放弃
                if attempt == 1:
                    log.warning(f"{self.name} 关阀写入失败，重连后补发")
                    continue
                return {"ok": False, "error": "valve_write_failed"}
            state = self._scan_state(_OPEN_ACK_WAIT_S)
            if state:
                self.silent_streak = 0
                if attempt > 1:
                    log.info(f"{self.name} 关阀确认（重发第 {attempt} 次后）")
                return {"ok": True, "state": state}
            if attempt == 1:
                log.warning(f"{self.name} 关阀未收到应答，补发一次")
                time.sleep(OPEN_RETRY_DELAY_S)
        self._note_silent()
        return {"ok": True, "state": "SILENT"}

    def status(self) -> dict:
        if not self.connect():
            return {"ok": False, "error": "valve_serial_not_ready"}
        state = self._query_status()
        if state:
            self.silent_streak = 0
        else:
            self._note_silent()
        return {"ok": True, "state": state or "SILENT"}


VALVES: dict[int, Valve] = {}
_valves_lock = threading.Lock()


def get_valve(channel: int) -> Valve:
    """取/建某通道的阀实例（通道号从 1 起）"""
    channel = max(1, int(channel or 1))
    with _valves_lock:
        v = VALVES.get(channel)
        if v is None:
            v = Valve(channel)
            VALVES[channel] = v
        return v


def all_channels() -> list[int]:
    with _valves_lock:
        return sorted(VALVES)


def ensure_valves() -> list[int]:
    """按串口分类结果预建各通道实例，返回通道号列表（hello/清单上报用）"""
    try:
        valve_ports, _ = classify_ports()
        for ch in assign_channels(valve_ports):
            get_valve(ch)
    except Exception as e:
        log.warning(f"预建阀通道失败: {e}")
    if not all_channels():
        get_valve(1)          # 至少保留通道1（口找不到时由 connect 自行重试）
    return all_channels()


def valves_snapshot() -> list[dict]:
    """各通道当前口位/连接状态（上报后端展示用）。

    尚未连过的通道取不到 self._known_port，就用通道分配表里的口位兜底，
    这样后端一问就能看到「电磁阀1 = /dev/ttyUSB2（USB 1-1.4）」这类映射。
    """
    try:
        valve_ports, _ = classify_ports()
        assigned = assign_channels(valve_ports)
    except Exception:
        assigned = {}
    snapshot = []
    for ch in all_channels():
        v = get_valve(ch)
        dev = (v.ser.port if v.ser and v.ser.is_open else v._known_port) or assigned.get(ch, "")
        snapshot.append({
            "channel": ch,
            "name": v.name,
            "device": dev,
            "topology": usb_topology(dev) if dev else "",
            "connected": bool(v.ser and v.ser.is_open),
        })
    return snapshot


def channels_for(channel: int) -> list[int]:
    """channel>0 → 只该通道；channel<=0/缺省 → 全部通道（关阀广播是安全兜底：
    多个阀时绝不能只关一个，防另一个阀遗留通电烧毁）"""
    if channel and channel > 0:
        return [int(channel)]
    ensure_valves()
    return all_channels() or [1]


def failover_target(channel: int) -> Optional[int]:
    """备用通道号（OPI_VALVE_FAILOVER 形如 "1>2,2>1"）；没配或未命中返回 None"""
    for pair in FAILOVER_SPEC.split(","):
        pair = pair.strip()
        if ">" not in pair:
            continue
        head, tail = pair.split(">", 1)
        try:
            if int(head.strip()) == int(channel):
                return int(tail.strip())
        except ValueError:
            continue
    return None


def open_with_failover(channel: int) -> tuple:
    """开阀 + 故障切换：主阀 OPEN_CONFIRM_TIMEOUT_S 内没确认开启（无 'OK ON'），
    自动改用备用阀再试一次。

    返回 (最终结果, 实际生效的通道号, 各次尝试明细)。明细随事件上报，后端
    /valve/status 能看到「电磁阀1 超时 → 电磁阀2 顶上」的完整过程。
    """
    attempts: list = []
    valve = get_valve(channel)
    result = valve.open(deadline=time.monotonic() + OPEN_CONFIRM_TIMEOUT_S)
    attempts.append({
        "channel": channel, "name": valve.name, "ok": result.get("ok"),
        "state": result.get("state"), "error": result.get("error"),
    })
    if result.get("state") == "ON":
        return result, channel, attempts

    target = failover_target(channel)
    if target is None:
        return result, channel, attempts

    backup = get_valve(target)
    log.warning(
        f"{valve.name} 在 {OPEN_CONFIRM_TIMEOUT_S:.1f}s 内未确认开启"
        f"（state={result.get('state')}）→ 切换到 {backup.name}"
    )
    backup_result = backup.open(deadline=time.monotonic() + OPEN_CONFIRM_TIMEOUT_S)
    attempts.append({
        "channel": target, "name": backup.name, "ok": backup_result.get("ok"),
        "state": backup_result.get("state"), "error": backup_result.get("error"),
    })
    return backup_result, target, attempts
