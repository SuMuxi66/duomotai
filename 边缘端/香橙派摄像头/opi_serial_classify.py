"""opi_serial_classify.py — 串口按应答特征分类 + 角色/通道档案（2026-09-21 从 opi_sensors_client.py 拆出）。

纯搬运：函数体与拆分前完全一致（含全部踩坑注释）。
职责：枚举候选口 → 按"阀应答 OK OFF / 称重静读出数据行"分类 → 角色档案落盘复用。
状态文件读写统一走 opi_serial_common.open_state_file（带目录校验，默认行为不变）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Optional

import serial
import serial.tools.list_ports

from opi_serial_common import (
    MAX_DEVICE_INDEX,
    MAX_VALVE_CHANNELS,
    SERIAL_ROLE_FILE,
    VALVE_BAUD,
    WEIGHT_IDENTIFY_S,
    _CLASSIFY_TTL_S,
    _VALVE_ACK_PROBE_S,
    log_opi_sensors as log,
    open_state_file,
)


def candidate_ports() -> list[str]:
    ports = []
    try:
        for p in serial.tools.list_ports.comports():
            if "/dev/ttyUSB" in p.device or "/dev/ttyACM" in p.device:
                ports.append(p.device)
    except Exception as e:
        log.warning(f"枚举串口失败: {e}")
    if not ports:
        ports = [f"/dev/ttyUSB{i}" for i in range(MAX_DEVICE_INDEX)]
        ports += [f"/dev/ttyACM{i}" for i in range(MAX_DEVICE_INDEX)]
    return ports


_classify_lock = threading.Lock()
_classify_cache: Optional[tuple[float, tuple[list[str], Optional[str]]]] = None
_role_archive_dirty = False   # 档案被判失效（口对不上/连续无应答）→ 下次走全量扫描

_TOPO_RE = re.compile(r"\b(\d+)-(\d+(?:\.\d+)*)\b")
_channel_map_lock = threading.Lock()


def usb_topology(device: str) -> str:
    """取设备在 USB 树上的拓扑段（如 "1-1.4"）——CH340 无唯一序列号，
    按编号/by-id 断电重插都会洗牌，只有物理插口路径稳定。读不到时回退设备名。"""
    try:
        real = os.path.realpath(f"/sys/class/tty/{os.path.basename(device)}/device")
        hits = _TOPO_RE.findall(real)
        if hits:
            bus, rest = hits[-1]
            return f"{bus}-{rest}"
    except Exception:
        pass
    return os.path.basename(device)


def spec_matches(device: str, spec: str) -> bool:
    """应急钉死匹配：spec 可写设备名（/dev/ttyUSB2）或拓扑段（1-1.4）"""
    spec = (spec or "").strip()
    if not spec:
        return False
    return spec == device or spec == usb_topology(device)


def load_channel_map() -> dict:
    """通道绑定表 {拓扑段: 通道号}，落盘保证断电重插后「电磁阀1/2」不互换"""
    try:
        with open_state_file("", ".valve_channels.json", "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"读取通道绑定表失败（将重新分配）: {e}")
    return {}


def save_channel_map(mapping: dict) -> None:
    try:
        with open_state_file("", ".valve_channels.json", "w") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        log.warning(f"写入通道绑定表失败: {e}")


def assign_channels(valve_ports: list[str]) -> dict:
    """把分类出的阀口分配到通道号，返回 {通道号: 设备}。

    - 绑定表里已记录的物理口（按拓扑段）沿用原通道号 → 断电重插不互换；
    - 首次分配（无绑定表）按拓扑**降序**：现场把当前在用的下料阀挂在 hub 末位口
      （1-1.4，实测即 /dev/ttyUSB2），让它继续当电磁阀1，**不改变下料主链路的物理
      去向**；后来插上的（1-1.2，/dev/ttyUSB0）自动成为电磁阀2；
    - 想重新分配删掉 CHANNEL_STATE_FILE 即可。
    """
    with _channel_map_lock:
        mapping = load_channel_map()
        result: dict[int, str] = {}
        unknown: list[str] = []
        for dev in valve_ports:
            ch = mapping.get(usb_topology(dev))
            if ch and ch not in result:
                result[ch] = dev
            else:
                unknown.append(dev)
        for dev in sorted(unknown, key=usb_topology, reverse=True):
            ch = 1
            while ch in result:
                ch += 1
            result[ch] = dev
        if result:
            for ch, dev in result.items():
                mapping[usb_topology(dev)] = ch
            save_channel_map(mapping)
        return dict(sorted(result.items()))


def _current_topology_map() -> dict:
    """当前枚举到的串口 {USB 拓扑段: 设备}——档案比对的依据"""
    out: dict = {}
    for dev in candidate_ports():
        topo = usb_topology(dev)
        if topo and topo not in out:
            out[topo] = dev
    return out


def load_role_map() -> dict:
    """读「USB 拓扑段 → 角色」档案（角色形如 valve:1 / valve:2 / weight）"""
    try:
        with open_state_file(SERIAL_ROLE_FILE, ".serial_roles.json", "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"读取串口角色档案失败（将重新扫描）: {e}")
    return {}


def save_role_map(mapping: dict) -> None:
    try:
        with open_state_file(SERIAL_ROLE_FILE, ".serial_roles.json", "w") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        log.warning(f"写入串口角色档案失败: {e}")


def invalidate_role_archive(reason: str = "") -> None:
    """让角色档案失效（下次 classify_ports 走全量重扫）——用于"口对不上/板卡不应答"时"""
    global _role_archive_dirty, _classify_cache
    _role_archive_dirty = True
    # 必须连内存分类缓存一起清：不清的话 60s TTL 内 classify_ports 直接返回旧结果，
    # 根本走不到档案脏检查，失效标记形同虚设（2026-09-19 实测踩坑）
    _classify_cache = None
    if reason:
        log.warning(f"串口角色档案已失效（下次重扫）：{reason}")


def _roles_from_archive() -> Optional[tuple]:
    """按档案 + 当前设备集合直接算出 (阀口列表, 称重口)。

    约束：档案里记录的拓扑段集合必须与当前枚举到的**完全一致**——少一个（拔了）或
    多一个（插了新的）都判为"有变动"，返回 None 交给全量扫描重新识别。
    """
    global _role_archive_dirty
    if _role_archive_dirty:
        return None
    cached = load_role_map()
    if not cached:
        return None
    # 档案里连一个可用角色都没有（USB 掉线时扫描失败会把所有口写成 other）→ 视为无效，
    # 强制重扫。2026-09-16 踩坑：不留这道判断的话，档案一旦被污染就永远"沿用"、
    # 设备插回来也认不出，只能人工删档案。
    if not any(r.startswith("valve") or r == "weight" for r in cached.values()):
        log.info("串口角色档案里没有任何可用角色（只有 other/未知）→ 强制重扫并重建")
        return None
    current = _current_topology_map()
    if set(cached) != set(current):
        log.info(
            f"串口设备集合与档案不一致（档案 {sorted(cached)} ≠ 当前 {sorted(current)}）→ 需要重扫"
        )
        return None
    valves: list = []
    weight: Optional[str] = None
    for topo, role in cached.items():
        dev = current.get(topo)
        if not dev:
            continue
        if role == "weight":
            weight = dev
        elif role.startswith("valve"):
            try:
                ch = int(role.split(":", 1)[1])
            except (IndexError, ValueError):
                ch = 0
            valves.append((ch, dev))
    valves.sort(key=lambda x: x[0])
    return [d for _, d in valves], weight


def _save_role_archive(valve_ports: list, weight_dev: Optional[str]) -> None:
    """扫描结果落盘：{拓扑段: 角色}（阀口按拓扑降序编号，与 assign_channels 首次分配同序）。

    所有枚举到的口都要记档——已知但非本脚本职责的口（如红外）记成 "other"。
    否则"档案集合 == 当前集合"的比对永远不成立（档案缺红外口），会退化成每次都重扫。

    ⚠️ 但**一个可用角色都没识别出来时不落盘**（2026-09-16 踩坑）：USB 掉线期间扫描必然
    全口失败，若把一片 "other" 覆盖上去，设备插回来时会误判"档案命中"而永不重扫。
    """
    mapping: dict = {}
    ordered = sorted(valve_ports, key=usb_topology, reverse=True)
    for idx, dev in enumerate(ordered, start=1):
        mapping[usb_topology(dev)] = f"valve:{idx}"
    if weight_dev:
        mapping[usb_topology(weight_dev)] = "weight"
    if not mapping:
        log.warning("扫描未识别出任何角色（阀/称重都为空）→ 保留原档案不覆盖，等设备恢复后重扫")
        return
    # 拔插时设备可能「陆续」枚举（先回来 2 个、再回来 1 个）：中间那次扫描会识别到
    # 部分角色，若直接覆盖档案就把没回来的那个角色的绑定抹掉了。故：新角色是旧角色的
    # **真子集**（少了）时判定设备未到齐，保留原档案，等齐了再更新（2026-09-16）。
    old_roles = {
        r for r in load_role_map().values() if r.startswith("valve") or r == "weight"
    }
    new_roles = set(mapping.values())
    if old_roles and not old_roles.issubset(new_roles):
        log.warning(
            f"扫描识别到的角色少于档案（档案 {sorted(old_roles)} ⊄ 本次 {sorted(new_roles)}）"
            "→ 判定设备尚未全部回来，保留原档案不覆盖"
        )
        return
    for dev in _current_topology_map().values():
        mapping.setdefault(usb_topology(dev), "other")
    if mapping:
        save_role_map(mapping)
        log.info(f"串口角色档案已更新: {mapping}")


def _port_acks_close(ser: serial.Serial) -> bool:
    """发 'F'（幂等关阀，对任何板子都无副作用）→ 应答含 OK OFF 即阀（下料机一体板）"""
    ser.reset_input_buffer()
    ser.write(b"F")
    ser.flush()
    deadline = time.monotonic() + _VALVE_ACK_PROBE_S
    buf = b""
    while time.monotonic() < deadline:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        text = buf.decode("utf-8", errors="ignore").upper()
        if "OK OFF" in text or "\r\nOFF" in text:
            return True
        time.sleep(0.02)
    return False


def looks_like_weight_line(text: str) -> bool:
    """严格匹配称重数据行：必须带 '='（Weight= / W= / 原始值:）。
    注意不能只用 weight 子串——另一块 HX711 模块输出 'Raw: X | Real Weight:  g'
    （Real Weight 后跟冒号），子串匹配会误认。"""
    return ("Weight=" in text or "W=" in text
            or "原始值" in text or "真实重量" in text
            or "Tmx=" in text)


def classify_ports(force: bool = False) -> tuple[list[str], Optional[str]]:
    """取得 (阀口列表, 称重口)——**档案优先，通常零扫描**。

    2026-09-15 改造（用户要求）：识别一次就把「USB 拓扑段 → 角色」写进 SERIAL_ROLE_FILE，
    之后只要**当前设备集合与档案一致**就直接按档案绑定、完全不扫描（0 秒）；
    只有以下情况才走全量扫描（5~10 秒）：
      · 首次运行（没有档案）
      · USB 设备集合有变动（插拔/换口：档案里的拓扑段与当前枚举的集合对不上）
      · 档案被判失效（口径连续无应答，见 invalidate_role_archive）
      · force=True（调用方显式要求重扫）

    阀口列表按通道号排序；通道号由 assign_channels() 统一分配。
    结果另有 _CLASSIFY_TTL_S 秒的内存缓存。
    """
    global _classify_cache, _role_archive_dirty
    with _classify_lock:
        if (not force and _classify_cache is not None
                and time.monotonic() - _classify_cache[0] < _CLASSIFY_TTL_S):
            return _classify_cache[1]

        # ★ 档案优先：设备集合没变 → 直接绑定，不扫描
        if not force:
            hit = _roles_from_archive()
            if hit is not None:
                log.info(
                    f"串口角色沿用档案（设备集合未变，跳过扫描）: "
                    f"阀={'/'.join(hit[0]) or '无'} 称重={hit[1] or '无'}"
                )
                _classify_cache = (time.monotonic(), hit)
                return hit

        # 全量扫描（首次 / 设备集合变动 / 强制重扫）
        valve_ports: list[str] = []
        weight_dev: Optional[str] = None
        for device in candidate_ports():
            try:
                ser = serial.Serial(port=device, baudrate=VALVE_BAUD,
                                    bytesize=8, parity="N", stopbits=1, timeout=0.5)
            except Exception:
                continue
            try:
                # ① 阀：'F' 幂等关阀 → OK OFF（称重板/红外对该指令静默）
                if _port_acks_close(ser):
                    if len(valve_ports) < MAX_VALVE_CHANNELS:
                        valve_ports.append(device)
                    log.info(f"串口分类 {device}: 应答 OK OFF → 电磁阀候选（USB {usb_topology(device)}）")
                    continue
                # ② 称重：静读数据行（一体板的 Raw:/Real Weight: 行不满足 looks_like_weight_line）
                ser.reset_input_buffer()
                deadline = time.monotonic() + WEIGHT_IDENTIFY_S
                is_weight = False
                while time.monotonic() < deadline:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line and looks_like_weight_line(line):
                        is_weight = True
                        break
                if is_weight:
                    log.info(f"串口分类 {device}: Weight 数据行 → 称重板卡")
                    if weight_dev is None:
                        weight_dev = device
                else:
                    log.info(f"串口分类 {device}: 无特征（红外/空闲口），跳过")
            except Exception as e:
                log.warning(f"串口分类 {device} 异常: {e}")
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        valve_ports.sort(key=usb_topology, reverse=True)
        log.info(
            f"串口分类结果: 阀={'/'.join(valve_ports) or '未找到'}（共 {len(valve_ports)} 个）"
            f" 称重={weight_dev or '未找到'}"
        )
        _save_role_archive(valve_ports, weight_dev)   # 落盘归档，下次直接复用（免扫描）
        _role_archive_dirty = False
        _classify_cache = (time.monotonic(), (valve_ports, weight_dev))
        return _classify_cache[1]
