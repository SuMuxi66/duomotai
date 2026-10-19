"""
远程红外测温接入（香橙派采集端）

MLX90640 红外热像模组改接香橙派：采集客户端读串口原始帧（协议同 temperature.py：
115200bps，帧头 5A5A、定长 1544 字节，768 点温度小端 u16÷100 + 传感器温度），
经 WebSocket 推流到后端 /ws/opi-ir。本模块只做帧校验与解析入库——直接复用
temperature 模块的 reader（parse_temperatures / update_temperature_data），
/temperature 接口、"现在温度多少度"快答、油温盯守（oil_watch）零改动跟随远程数据；
远程断流后数据自然过期（下游按 timestamp 新鲜度判定，行为同温重二合一板卡注入路径）。

协议（ws://host:port/ws/opi-ir）：
- 二进制帧 = 一帧完整 1544 字节 MLX90640 原始帧（客户端负责 5A5A 帧同步）；
- 文本帧 = JSON 控制消息：{"type":"hello",...} 仅记日志，{"type":"ping"} 回 {"type":"pong"}。

单设备演示场景：多客户端同时推流时按"最后写入者胜"覆盖。
"""
from __future__ import annotations

import json
import threading
import time

from fastapi import WebSocket, WebSocketDisconnect

from .logger import backend_log
from .temperature import REMOTE_SOURCE, _get_reader

IR_FRAME_SIZE = 1544
IR_HEADER = b"\x5a\x5a"
# MLX90640 物理量程 -40~300°C（u16÷100 理论上限 655.35）。结构合法但数值出界的帧
# = 干扰/字节错位坏帧（2026-09-19 实测 654.2°C 毛刺直达 UI），整帧丢弃。
# 上限留到 320：真实油锅可到 ~300°C，不能把合法高温当坏帧。
IR_TEMP_MIN_C = -45.0
IR_TEMP_MAX_C = 320.0


class _IrFeedStats:
    """推流统计（仅日志/观测用；数据本体在 temperature 模块的 reader 里）"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames_ok = 0
        self._frames_bad = 0
        self._last_time = 0.0
        self._client = ""
        self._connected = False

    def on_ok(self, client: str) -> int:
        with self._lock:
            self._frames_ok += 1
            self._last_time = time.monotonic()
            self._client = client
            self._connected = True
            return self._frames_ok

    def on_bad(self) -> None:
        with self._lock:
            self._frames_bad += 1

    def mark_connected(self, client: str) -> None:
        with self._lock:
            self._connected = True
            self._client = client

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False

    def snapshot(self) -> dict:
        with self._lock:
            age = (time.monotonic() - self._last_time) if self._last_time > 0 else None
            return {
                "connected": self._connected,
                "client": self._client,
                "frames_ok": self._frames_ok,
                "frames_bad": self._frames_bad,
                "age_seconds": round(age, 1) if age is not None else None,
            }


_stats = _IrFeedStats()

# 活跃推流连接注册表：应用关闭（含 --reload 热重载）时主动断开，
# 否则长连 WS 会卡住 uvicorn 优雅关闭，拖死重启
_active_ws: set = set()


async def close_active_ws() -> None:
    """主动断开全部红外推流连接（main.py shutdown 事件调用）"""
    for ws in list(_active_ws):
        try:
            await ws.close()
        except Exception:
            pass
    _active_ws.clear()


def ir_stats() -> dict:
    return _stats.snapshot()


def process_ir_frame(data: bytes) -> bool:
    """校验并解析一帧 MLX90640 原始帧，写入温度模块存储。返回是否采纳。

    复用 temperature.MLX90640Reader 的解析与入库（单一解析实现），
    写入远程 opi 源槽（与本机 USB/local 槽分开，读取时 Pi fresh 优先、断流回落本机）。
    """
    if not data or len(data) < IR_FRAME_SIZE or not data.startswith(IR_HEADER):
        _stats.on_bad()
        return False
    reader = _get_reader()
    try:
        temps, sensor_temp = reader.parse_temperatures(data)
    except Exception as e:
        backend_log(f"[远程红外] 帧解析失败: {e}")
        _stats.on_bad()
        return False
    # 量程门控：坏内容帧（结构合法、数值离谱）整帧丢弃，UI/油温报警/温度快答都看不到它
    t_min, t_max = min(temps), max(temps)
    if t_min < IR_TEMP_MIN_C or t_max > IR_TEMP_MAX_C:
        _stats.on_bad()
        backend_log(
            f"[远程红外] 坏帧丢弃: max={t_max:.1f}°C min={t_min:.1f}°C "
            f"超出量程 [{IR_TEMP_MIN_C}, {IR_TEMP_MAX_C}]"
        )
        return False
    reader.update_temperature_data(temps, sensor_temp, source=REMOTE_SOURCE)
    _stats.on_ok("ws")
    return True


async def handle_opi_ir_ws(ws: WebSocket) -> None:
    """香橙派红外采集端 WebSocket：循环接收原始帧解析入库"""
    await ws.accept()
    client = ws.client.host if ws.client else "unknown"
    _active_ws.add(ws)
    _stats.mark_connected(client)
    backend_log(f"[远程红外] 采集端已连接: {client}")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                if process_ir_frame(data) and _stats.snapshot()["frames_ok"] % 100 == 0:
                    st = _stats.snapshot()
                    reader = _get_reader()
                    with reader.lock:
                        maximum = reader.temperature_data.get("maximum")
                    backend_log(
                        f"[远程红外] 已接收 {st['frames_ok']} 帧 "
                        f"(丢弃 {st['frames_bad']}, 当前最高温 {maximum}°C)"
                    )
                continue
            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except json.JSONDecodeError:
                    continue
                ctrl_type = ctrl.get("type", "")
                if ctrl_type == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif ctrl_type == "hello":
                    backend_log(f"[远程红外] hello: {client} dev={ctrl.get('device', '?')}")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        backend_log(f"[远程红外] 连接异常: {type(e).__name__}: {e}")
    finally:
        _active_ws.discard(ws)
        _stats.mark_disconnected()
        backend_log(f"[远程红外] 采集端已断开: {client}")
