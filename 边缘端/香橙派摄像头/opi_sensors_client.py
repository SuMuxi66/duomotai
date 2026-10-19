#!/usr/bin/env python3
"""
香橙派外设采集客户端（称重板卡 + 电磁阀 → 后端 /ws/opi-sensors）

2026-09-12 硬件迁移：称重板卡（温重二合一）与电磁阀控制器从后端机 USB 改接香橙派。
本客户端常驻做两件事：

1. 称重上行：读称重板卡串口文本行（"Weight=..." / "原始值: X | 真实重量: Y g"，
   可含 Tmx/Tce/Tav/Tam 温度字段），原样经 WebSocket 推给后端——解析、软件去皮、
   零点跟踪、增益修正、EMA 全部在后端完成（与本机串口同一套管线），客户端零逻辑。
2. 电磁阀下行：接收后端 {"type":"valve_open"/"valve_close"/"valve_status"}（可带
   "channel":N）命令，写本机电磁阀串口（'O'/'F' 单字符协议，或 ON/OFF/STATUS 全词
   协议），执行结果以 {"type":"event",...,"channel":N,"name":"电磁阀N"} 异步回报。
   开阀带 STATUS 校验 + 重发（与后端 valve.py 同逻辑，搬到串口所在的一侧执行）。

多阀支持（2026-09-15，用户要求"另一个阀也接上控制，备注电磁阀2"）：
    串口分类会收集**所有**应答 OK OFF 的阀口，按通道号分别持有独立 Valve 实例。
    通道号与物理口的绑定关系沿用首次分配结果并落盘 STATE_FILE（按 USB 拓扑路径
    做 key，ttyUSB 编号洗牌不影响），因此"电磁阀1/电磁阀2"在断电重插后保持稳定。
    channel 缺省或 0 = 对**全部**通道执行（关阀全广播是安全兜底：多个阀时绝不能
    只关一个，防另一个阀遗留通电烧毁）。

开阀故障切换（2026-09-15 同日追加，用户要求"电磁阀1 超过 5 秒没启动就换电磁阀2"）：
    主阀在 OPI_VALVE_OPEN_TIMEOUT_S（默认 5s）内没拿到 'OK ON' 确认 → 判定"没启动"，
    自动改用 OPI_VALVE_FAILOVER 指定的备用阀（默认 1>2）。切换过程写进事件回报
    （failover=true + attempts 明细），后端 /valve/status 可查。
    ⚠️ 只在两阀**同一条料路**（互为备用）时合理；各管不同调料时必须关掉。

串口自动分类（2026-09-14，断电/重插后 ttyUSB 编号会洗牌，按端口号钉死必翻车）：
    按"应答特征"分类而不是端口号——
    阀（下料机一体板）：发 'F'（幂等关阀，对任何板子无副作用）→ 应答含 OK OFF；
    称重板卡：不应答指令，但静读期持续输出 Weight=/W=/原始值 数据行；
    红外（归 opi-ir 管）：两者皆非 → 跳过。
    阀命令连续无应答视为锁错口/板卡掉线：丢弃已知口，下次命令重新分类。

红外 MLX90640 走独立的 opi_ir_client.py（/ws/opi-ir），本脚本不涉及。

支持多后端：OPI_SENSORS_SERVER 逗号分隔；称重行同时分发所有已连接后端；
阀命令任意后端下发均执行（多后端同时下料场景不存在，后命令覆盖前命令）。

模块拆分（2026-09-21，纯结构搬运、零行为变更）：
    opi_serial_common.py     共享配置常量 + 日志
    opi_serial_classify.py   串口按应答特征分类 + 角色/通道档案
    opi_valve.py             电磁阀执行器（多通道 + 开阀故障切换）
    opi_weight_reader.py     称重板卡找口/解卡
    opi_discovery.py         子网自动发现兜底（三个客户端共享）
    本文件                   进程装配：WS 目标/分发 + 称重上行循环 + main

依赖（香橙派上已就绪）：python3 + pyserial + websockets
    pip3 install pyserial websockets -i https://pypi.tuna.tsinghua.edu.cn/simple

配置（环境变量）：
    OPI_SENSORS_SERVER  后端 WS 地址，逗号分隔可配多个（必填），
                        如 ws://192.168.1.96:8010/ws/opi-sensors
    OPI_WEIGHT_DEVICE   可选，应急钉死称重口；默认自动按应答特征分类
    OPI_WEIGHT_BAUD     称重波特率，默认 9600（与后端 weight.py 一致）
    OPI_VALVE_DEVICE    可选，应急钉死电磁阀1 的口；默认自动按应答特征分类
    OPI_VALVE2_DEVICE   可选，应急钉死电磁阀2 的口
    OPI_VALVE_BAUD      阀波特率，默认 9600
    OPI_VALVE_PROTOCOL  auto（默认，按单字符 O/F + ACK 执行）| word
    OPI_VALVE_OPEN_TIMEOUT_S  开阀确认时限，默认 5（秒）：主阀在这个时间内拿不到
                        'OK ON' 应答就判定"没启动"，触发故障切换
    OPI_VALVE_FAILOVER  开阀故障切换表，默认 "1>2"（电磁阀1 没启动就改用电磁阀2）；
                        支持互备 "1>2,2>1"；设成空串 = 关闭切换
    OPI_VALVE_CHANNELS_FILE  通道↔物理口绑定表落盘路径（默认脚本同目录
                        .valve_channels.json）；删掉该文件即重新分配通道号
    其余共享常量见 opi_serial_common.py（含各阈值的环境变量覆盖说明）

运行：
    python3 opi_sensors_client.py       # 前台调试
    sudo systemctl start opi-sensors    # 生产（见 opi-sensors.service）
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

import serial
import websockets

from opi_serial_common import (
    READ_HARD_TIMEOUT_S,
    RECONNECT_BACKOFF_MAX,
    SEND_TIMEOUT_S,
    WEIGHT_BAUD,
    WEIGHT_DISPATCH_STALE_S,
    log_opi_sensors as log,
)
from opi_serial_classify import invalidate_role_archive
from opi_valve import (
    channels_for,
    ensure_valves,
    get_valve,
    open_with_failover,
    valve_name,
    valves_snapshot,
)
from opi_weight_reader import _force_unblock_close, find_weight_port
from opi_discovery import discovery_loop

SERVERS = [s.strip() for s in os.getenv("OPI_SENSORS_SERVER", "").split(",") if s.strip()]


# ---------------- WS 目标与分发 ----------------

class Target:
    """一个后端推送目标：独立连接 + 独立重连 + 独立接收循环（处理下行阀命令）"""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.hello_pending = False
        self.hello_json = ""

    async def connection_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.url, max_size=None, ping_interval=20, ping_timeout=20
                ) as ws:
                    self.ws = ws
                    self.hello_pending = True
                    log.info(f"后端已连接: {self.url}")
                    backoff = 1.0
                    # 阀通道清单独立上报：阀与称重是两条独立链路，不能等 weight_loop
                    # 找到称重板卡才随 hello 发出（板卡不在场时阀通道就漏报了）
                    asyncio.create_task(self._report_valves(ws))
                    await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"{self.url} 连接异常: {type(e).__name__}: {e}")
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _report_valves(self, ws) -> None:
        try:
            await asyncio.to_thread(ensure_valves)
            await ws.send(json.dumps({
                "type": "hello", "kind": "valve_channels",
                "valves": valves_snapshot(),
            }, ensure_ascii=False))
        except Exception as e:
            log.warning(f"上报阀通道清单失败: {type(e).__name__}: {e}")

    async def _receive_loop(self, ws) -> None:
        """接收下行：JSON 控制命令（阀）→ 执行 → 事件回报。

        命令带 "channel"：正整数只作用于该通道；缺省/0 = 全部通道
        （后端 /dispense/stop 关阀走全通道广播，防多阀时漏关遗留通电）。"""
        async for raw in ws:
            text = (raw or "").strip()
            if not text.startswith("{"):
                continue
            try:
                ctrl = json.loads(text)
            except json.JSONDecodeError:
                continue
            ctype = ctrl.get("type", "")
            if ctype == "ping":
                try:
                    await ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
            elif ctype in ("valve_open", "valve_close", "valve_status"):
                action = ctype[len("valve_"):]
                try:
                    channel = int(ctrl.get("channel") or 0)
                except (TypeError, ValueError):
                    channel = 0
                try:
                    await self._handle_valve(action, channel, bool(ctrl.get("keepalive")))
                except serial.SerialException as e:
                    # 阀口串口异常（板卡拔出/集线器抖动）：只标记该口待重分类，
                    # 绝不带崩 WS 接收循环——连接断了后续所有下行命令都收不到
                    log.warning(f"阀串口异常（标记待重分类，连接保持）: {e}")
                    invalidate_role_archive(f"SerialException: {e}")
                except Exception as e:
                    log.warning(f"阀命令处理异常（忽略，连接保持）: {type(e).__name__}: {e}")
            elif ctype == "valve_list":
                await dispatch_event({
                    "type": "event", "event": "valve_list",
                    "valves": valves_snapshot(),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

    async def _handle_valve(self, action: str, channel: int = 0, keepalive: bool = False) -> None:
        # 多通道（关阀广播 channel=0）并行执行（2026-09-21）：原串行 for 循环下，
        # 一个 SILENT 阀要吃满 2.1s ACK 超时，把正常阀的关阀也拖慢（前端表现为
        # "点下料关阀卡 2 秒+"）。各通道是独立 Valve 实例/独立串口，并行安全；
        # 共享的 classify_ports 有锁，并行时自然串行化，无竞态。
        channels = channels_for(channel)
        if action == "open" and keepalive:
            # 保活重发：只写指令立即返回（不等 ACK、不做故障切换），且**不发事件**——
            # 否则会刷屏并覆盖 /valve/status 里真正的开阀结果
            for ch in channels:
                valve = get_valve(ch)
                await asyncio.to_thread(self._exec_valve, valve, "ping")
            return

        async def run_one(ch: int) -> None:
            if action == "open":
                # 开阀走故障切换：主阀 OPEN_CONFIRM_TIMEOUT_S 内没确认，备用阀顶上
                result, used, attempts = await asyncio.to_thread(open_with_failover, ch)
                event = {
                    "type": "event", "event": "valve_open_result",
                    "channel": used,              # 实际生效的通道
                    "requested_channel": ch,      # 原本要求的通道
                    "name": valve_name(used),
                    "failover": used != ch,
                    "attempts": attempts,
                    **result,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                if used != ch:
                    log.warning(
                        f"开阀故障切换：{valve_name(ch)} → {valve_name(used)}（结果 {result}）"
                    )
                else:
                    log.info(f"{valve_name(ch)} 命令 open → {result}")
            else:
                valve = get_valve(ch)
                result = await asyncio.to_thread(self._exec_valve, valve, action)
                event = {"type": "event", "event": f"valve_{action}_result",
                         "channel": ch, "name": valve.name, **result,
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                log.info(f"{valve.name} 命令 {action} → {result}")
            await dispatch_event(event)

        if len(channels) == 1:
            await run_one(channels[0])
        else:
            await asyncio.gather(*(run_one(ch) for ch in channels))

    @staticmethod
    def _exec_valve(valve, action: str) -> dict:
        if action == "open":
            return valve.open()
        if action == "ping":
            return valve.ping_open()      # 保活重发：只写不等应答
        if action == "close":
            return valve.close()
        if action == "status":
            return valve.status()
        return {"ok": False, "error": f"unknown_action:{action}"}


async def dispatch(targets: list[Target], text: str) -> None:
    """称重文本行分发给所有已连接后端。

    2026-09-16：每次 send 都套 asyncio.wait_for 超时。半死连接（ws 对象还在但发不出去）
    会让裸 await send() 永久挂起，把 weight_loop 卡死；超时后主动置 ws=None 交给重连逻辑。
    """
    for t in targets:
        if t.ws is None:
            continue
        try:
            if t.hello_pending:
                await asyncio.wait_for(t.ws.send(t.hello_json), timeout=SEND_TIMEOUT_S)
                t.hello_pending = False
            await asyncio.wait_for(t.ws.send(text), timeout=SEND_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning(f"{t.url} 发送超时 >{SEND_TIMEOUT_S}s（疑似半死连接）→ 置为待重连")
            t.ws = None
        except Exception as e:
            log.warning(f"{t.url} 发送失败: {type(e).__name__}: {e}，等重连")
            t.ws = None


async def dispatch_event(event: dict) -> None:
    """阀执行结果事件回报给所有已连接后端"""
    payload = json.dumps(event, ensure_ascii=False)
    for t in TARGETS:
        if t.ws is not None:
            try:
                await t.ws.send(payload)
            except Exception:
                pass


TARGETS: list[Target] = []


async def weight_loop() -> None:
    """称重板卡读取会话：找到板卡后逐行转发，串口冻结/丢失自动重找"""
    backoff = 1.0
    while True:
        ser = await asyncio.to_thread(find_weight_port)
        if ser is None:
            log.warning("未找到称重板卡，退避重试（插入即用）")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
            continue
        backoff = 1.0
        hello = (f'{{"type":"hello","device":"{os.path.basename(ser.port or "serial")}",'
                 f'"kind":"weight","baudrate":{WEIGHT_BAUD}}}')
        for t in TARGETS:
            t.hello_json = hello
        log.info(f"称重板卡就绪 @ {WEIGHT_BAUD} → {len(TARGETS)} 个后端")
        try:
            last_data = time.monotonic()
            last_dispatch_ok = time.monotonic()
            while True:
                # 总看门狗：不管串口有没有数据，只要连续这么久没跑完一轮分发就重找板卡。
                # 旧的 15s 判断挂在 elif 上，readline 一直有数据时永远走不到——卡在
                # dispatch 里的时候正是"有数据但发不出去"，所以必须独立判断。
                if time.monotonic() - last_dispatch_ok > WEIGHT_DISPATCH_STALE_S:
                    log.warning(f"{WEIGHT_DISPATCH_STALE_S:g}s 未完成一轮分发，重找称重板卡")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    break
                try:
                    line = await asyncio.wait_for(
                        asyncio.to_thread(ser.readline), timeout=READ_HARD_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    # 内核态读楔死：放弃等待并强制关开串口解卡（重开失败则重找板卡）
                    log.warning(
                        f"readline 超过 {READ_HARD_TIMEOUT_S:g}s 未返回（串口疑似楔死），强制解卡"
                    )
                    ser = _force_unblock_close(ser)
                    if ser is None:
                        break
                    continue
                text = line.decode("utf-8", errors="ignore").strip()
                if text:
                    await dispatch(TARGETS, text)
                    last_data = time.monotonic()
                    last_dispatch_ok = time.monotonic()
                elif time.monotonic() - last_data > 15.0:
                    log.warning("15s 无数据行，重找称重板卡")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    break
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"称重会话异常: {type(e).__name__}: {e}")
            try:
                ser.close()
            except Exception:
                pass
        await asyncio.sleep(2.0)


async def main() -> None:
    if not SERVERS:
        log.error("未配置 OPI_SENSORS_SERVER（如 ws://192.168.1.96:8010/ws/opi-sensors）")
        return
    global TARGETS
    TARGETS = [Target(url) for url in SERVERS]
    conn_tasks = [asyncio.create_task(t.connection_loop()) for t in TARGETS]
    conn_tasks.append(asyncio.create_task(discovery_loop(
        TARGETS, "/ws/opi-sensors", log,
        make_target=Target,
        spawn=lambda t: asyncio.create_task(t.connection_loop()),
        hello_json=f'{{"type":"hello","device":"auto-discovered","kind":"weight","baudrate":{WEIGHT_BAUD}}}',
    )))
    weight_task = asyncio.create_task(weight_loop())
    await asyncio.gather(weight_task, *conn_tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("手动退出")
