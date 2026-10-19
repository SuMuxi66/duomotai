"""香橙派外设通道（称重 + 电磁阀）——/ws/opi-sensors

2026-09-12 硬件迁移：称重板卡、电磁阀控制器从后端本机 USB 移到香橙派。
红外 MLX90640 早已走 /ws/opi-ir，本模块补齐另外两路：

上行（Pi → 后端）：
- 文本帧 = 称重板卡串口原始行（"Weight=..." / "原始值: X | 真实重量: Y g"，
  可含 Tmx/Tce/Tav/Tam 温度字段），后端 weight.process_remote_line 复用本地
  同一条解析管线（软件去皮/零点跟踪/增益/EMA 全部保留），写入 opi 源槽；
  温度字段写入温度 local 源槽（MLX90640 的 opi 槽 fresh 优先，本字段仅兜底）。
- JSON 帧：{"type":"hello"/"ping"} 控制；{"type":"event","event":...} 为 Pi 侧
  电磁阀执行结果回报（异步、仅记日志与状态快照）。

下行（后端 → Pi，电磁阀控制）：
- {"type":"valve_open"} / {"type":"valve_close"} / {"type":"valve_status"}
  （可带 "channel":N —— 2026-09-15 双阀支持：现场 2 个下料阀，Pi 端自动分类并按
  USB 拓扑分配通道号，1 = 电磁阀1（原在用阀）、2 = 电磁阀2；**不带 channel 或
  channel=0 表示对全部通道执行**，关阀一律走全通道广播，防多阀时漏关遗留通电）
- {"type":"valve_list"} → Pi 回报 {"event":"valve_list","valves":[...]} 通道清单
- 发送即成功（fire-and-forget，语义同单字符固件"写成功即成功"）；
  开阀确认逻辑（'O' + STATUS 校验 + 重发）由 Pi 端客户端执行，结果经
  event 异步回报（带 channel / name）。后端不做阻塞等待——调用方多在事件
  循环线程，阻塞等 WS 回包会与接收循环死锁。

本地串口仍在时自动降级：valve.py / weight 各函数先查 is_connected()，
Pi 未连接走原本地路径，行为与迁移前一致。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .logger import backend_log

log = logging.getLogger("opi-sensors")

# 活跃推流连接（单设备演示通常 1 个；多连接时控制命令广播给全部）
_clients: set = set()
_loop: Optional[asyncio.AbstractEventLoop] = None

# 最近一次 Pi 侧电磁阀执行结果（观测用）
_last_valve_event: dict = {}
_last_event_time: float = 0.0

# 各通道最近一次执行结果 {channel: event}，以及 Pi 上报的通道清单
_valve_events: dict = {}
_valve_channels: list = []

# 推流统计（观测用）
_stats = {"lines_ok": 0, "lines_bad": 0, "events": 0, "commands_sent": 0}


def valve_channels() -> list:
    """Pi 上报的阀通道清单（[{"channel","name","device","topology","connected"}]）"""
    return list(_valve_channels)


def set_valve_channels(channels) -> None:
    global _valve_channels
    _valve_channels = list(channels) if isinstance(channels, list) else []


def is_connected() -> bool:
    """Pi 外设通道是否有活跃连接（valve/weight 远程路径的开关判据）"""
    return bool(_clients)


def stats() -> dict:
    return {
        **_stats,
        "connected": is_connected(),
        "clients": len(_clients),
        "last_valve_event": dict(_last_valve_event),
        "last_event_age_s": round(time.time() - _last_event_time, 1) if _last_event_time else None,
        "valve_channels": valve_channels(),
        "valve_events": {str(k): v for k, v in _valve_events.items()},
    }


def send_valve_command(action: str, channel: int = 0, keepalive: bool = False) -> dict:
    """向 Pi 下发电磁阀控制命令（非阻塞，fire-and-forget）。

    action: "open" / "close" / "status"。
    channel: 正整数 = 只控该通道（1 电磁阀1 / 2 电磁阀2）；0 或缺省 = **全部通道**
    （关阀走全通道广播：多个阀时只关一个会留下通电开启的阀，有烧毁风险）。
    keepalive: 下料期间的周期保活重发标记——Pi 端只写串口指令立即返回（不等 ACK、
    不做故障切换），避免 0.5 秒一条的命令把 Pi 队列压积压、手动关阀排队。
    返回 {"ok": True} 表示命令已调度发送（不等于阀已动作——执行结果由 Pi 异步
    回报 event）；无连接返回 {"ok": False}。可从事件循环线程或工作线程安全调用。
    """
    if not _clients:
        return {"ok": False, "error": "pi_not_connected"}
    if _loop is None:
        return {"ok": False, "error": "loop_not_ready"}
    payload = {"type": f"valve_{action}"}
    if channel:
        try:
            payload["channel"] = int(channel)
        except (TypeError, ValueError):
            pass
    if keepalive:
        payload["keepalive"] = True
    msg = json.dumps(payload, ensure_ascii=False)
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(msg), _loop)
        _stats["commands_sent"] += 1
        return {"ok": True, "action": action, "channel": int(channel or 0)}
    except Exception as exc:
        backend_log(f"[远程外设] 命令调度失败 action={action}: {exc}")
        return {"ok": False, "error": str(exc)}


def request_valve_list() -> dict:
    """请求 Pi 回报阀通道清单（结果异步到 event=valve_list）"""
    if not _clients or _loop is None:
        return {"ok": False, "error": "pi_not_connected"}
    try:
        asyncio.run_coroutine_threadsafe(
            _broadcast(json.dumps({"type": "valve_list"}, ensure_ascii=False)), _loop
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _broadcast(msg: str) -> None:
    for ws in list(_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            _clients.discard(ws)


def _handle_event(event: dict) -> None:
    """记录 Pi 侧执行结果回报（电磁阀开/关校验结果、通道清单等）"""
    global _last_valve_event, _last_event_time
    _last_valve_event = event
    _last_event_time = time.time()
    _stats["events"] += 1
    if event.get("event") == "valve_list" and isinstance(event.get("valves"), list):
        set_valve_channels(event["valves"])
    channel = event.get("channel")
    if isinstance(channel, int) and channel > 0:
        _valve_events[channel] = event
    backend_log(f"[远程外设] Pi 事件: {json.dumps(event, ensure_ascii=False)}")


def _handle_weight_line(line: str) -> None:
    """称重板卡串口原始行 → 本地同款解析管线（opi 源槽）"""
    from . import weight as weight_mod

    try:
        # 首批数据到达：补做"连接时刻去皮"（板卡直读自带数百克偏移，见 weight.py）
        weight_mod.ensure_remote_zero_baseline()
        weight_mod.process_remote_line(line)
        _stats["lines_ok"] += 1
    except Exception as exc:
        _stats["lines_bad"] += 1
        log.warning("weight line error: %s", exc)


async def handle_opi_sensors_ws(ws: WebSocket) -> None:
    """香橙派外设通道：接收称重文本行 + 控制事件，下发电磁阀命令"""
    global _loop

    await ws.accept()
    client = ws.client.host if ws.client else "unknown"
    _clients.add(ws)
    _loop = asyncio.get_running_loop()
    backend_log(f"[远程外设] 采集端已连接: {client}（当前连接数 {len(_clients)}）")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text is None:
                continue
            stripped = text.strip()
            if not stripped:
                continue
            # JSON 控制帧与原始数据行的区分：能解析为 dict 且带 type 字段视为控制帧
            if stripped.startswith("{"):
                try:
                    ctrl = json.loads(stripped)
                except json.JSONDecodeError:
                    ctrl = None
                if isinstance(ctrl, dict) and ctrl.get("type"):
                    ctype = ctrl.get("type")
                    if ctype == "ping":
                        try:
                            await ws.send_text(json.dumps({"type": "pong"}))
                        except Exception:
                            pass
                    elif ctype == "hello":
                        if ctrl.get("kind") == "valve_channels":
                            set_valve_channels(ctrl.get("valves"))
                            backend_log(
                                f"[远程外设] 阀通道清单: {json.dumps(valve_channels(), ensure_ascii=False)}"
                            )
                        else:
                            backend_log(f"[远程外设] hello: {client} dev={ctrl.get('device', '?')}")
                    elif ctype == "event":
                        _handle_event(ctrl)
                    continue
            _handle_weight_line(stripped)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        backend_log(f"[远程外设] 连接异常: {type(exc).__name__}: {exc}")
    finally:
        _clients.discard(ws)
        backend_log(f"[远程外设] 采集端已断开: {client}（剩余连接数 {len(_clients)}）")
