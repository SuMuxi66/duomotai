#!/usr/bin/env python3
"""
香橙派红外测温采集客户端（MLX90640 → 后端 /ws/opi-ir）

MLX90640 热像模组接在香橙派 USB 串口上：本客户端读串口原始帧（115200bps，
帧头 5A5A、定长 1544 字节），经 WebSocket 推流到后端。解析在后端完成
（remote_ir.process_ir_frame 复用 temperature.reader），客户端只做帧同步转发。

支持多后端同时推流：OPI_IR_SERVER 逗号分隔多个地址，串口只开一次、
帧同时分发给所有已连接的后端；每个连接独立自动重连，单路故障不影响其他路。
（2026-09-12，与 opi_camera_client 的多目标模式同构）

依赖（香橙派上已就绪）：python3 + pyserial + websockets
    pip3 install pyserial websockets -i https://pypi.tuna.tsinghua.edu.cn/simple

配置（环境变量）：
    OPI_IR_SERVER   后端 WS 地址，逗号分隔可配多个，
                    如 ws://192.168.1.96:8010/ws/opi-ir,ws://192.168.1.236:8010/ws/opi-ir（必填）
    OPI_IR_DEVICE   串口设备（默认自动扫 /dev/ttyUSB* / /dev/ttyACM*）
    OPI_IR_BAUD     波特率，默认 115200（与后端 temperature.py 一致）

运行：
    python3 opi_ir_client.py          # 前台调试
    sudo systemctl start opi-ir       # 生产（见 opi-ir.service）
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import serial
import serial.tools.list_ports
import websockets

SERVERS = [s.strip() for s in os.getenv("OPI_IR_SERVER", "").split(",") if s.strip()]
DEVICE = os.getenv("OPI_IR_DEVICE", "")
BAUDRATE = int(os.getenv("OPI_IR_BAUD", "115200") or 115200)

# 协议常量（与后端 temperature.py 保持一致）
START_CMD = bytes([0xA5, 0x35, 0x02, 0xDC])  # 起流命令
FRAME_SIZE = 1544                             # 帧头 5A5A + 填充 + 768点×2 + 传感器×2 + 填充
FRAME_HEADER = b"\x5A\x5A"

MAX_DEVICE_INDEX = 10
NO_FRAME_RESTART_CMD = 3.0    # 连续 N 秒无有效帧 → 重发起流命令
FREEZE_THRESHOLD = 10.0       # 连续 N 秒无帧 → 重开串口
RECONNECT_BACKOFF_MAX = 10.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("opi-ir")


def candidate_ports() -> list[str]:
    """枚举候选串口：显式配置优先，否则扫 USB 转串口 / CDC 设备"""
    if DEVICE:
        return [DEVICE]
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


def probe_port(device: str) -> Optional[serial.Serial]:
    """打开候选串口并发起流命令，检查是否为 MLX90640（与后端 find_port 同启发式）。

    帧头判定（2026-09-21 修）：必须在整个读到的数据块里找 5A5A，不能只看前 10 字节——
    模块推流是连续帧（~6KB/s），0.5s 后缓冲区里堆的是帧中间部分，前 10 字节恰好是
    帧头的概率看运气；"前 10 字节"版因此间歇性漏认（当天 107 次找不到传感器自杀重启
    的根因）。读到 ≥100B 且块内任意位置有帧头即认定。
    """
    try:
        ser = serial.Serial(port=device, baudrate=BAUDRATE,
                            bytesize=8, parity="N", stopbits=1, timeout=0.5)
        ser.reset_input_buffer()
        time.sleep(0.2)
        ser.write(START_CMD)
        time.sleep(0.5)
        if ser.in_waiting >= 100:
            data = ser.read(ser.in_waiting)
            if FRAME_HEADER in data:
                log.info(f"找到红外传感器: {device} @ {BAUDRATE}")
                return ser
        ser.close()
    except Exception as e:
        log.debug(f"{device} 探测失败: {e}")
    return None


def find_sensor() -> Optional[serial.Serial]:
    for device in candidate_ports():
        ser = probe_port(device)
        if ser is not None:
            return ser
    return None


def extract_frames(buffer: bytearray) -> list[bytes]:
    """从串口字节流中按 5A5A 头同步切出定长帧（逻辑与后端 read_frame 一致）"""
    frames = []
    while len(buffer) >= FRAME_SIZE:
        idx = buffer.find(FRAME_HEADER)
        if idx == -1:
            del buffer[:-2]
            break
        if len(buffer) >= idx + FRAME_SIZE:
            frames.append(bytes(buffer[idx:idx + FRAME_SIZE]))
            del buffer[:idx + FRAME_SIZE]
        else:
            break
    return frames


class Target:
    """一个后端推送目标：独立连接 + 独立重连，串口帧由读取循环统一分发"""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.hello_pending = False      # 连上后等读取循环补发 hello（需串口设备名）
        self.hello_json = ""

    async def loop(self) -> None:
        """重连循环：保持到该后端的长连接，断开自动重连"""
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
                    await ws.wait_closed()   # 断开（含服务端拒收/ping 超时）从这里返回
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"{self.url} 连接失败: {type(e).__name__}: {e}")
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


async def dispatch(targets: list, frame: bytes) -> None:
    """分发给所有已连接的后端；单个目标发送失败不影响其它目标"""
    for t in targets:
        if t.ws is None:
            continue
        try:
            if t.hello_pending:
                await t.ws.send(t.hello_json)
                t.hello_pending = False
            await t.ws.send(frame)
        except Exception as e:
            log.warning(f"{t.url} 发送失败: {type(e).__name__}: {e}，等重连")
            t.ws = None


async def sensor_loop(targets: list) -> None:
    """单次传感器会话：找到串口后读帧→帧同步→分发，异常上抛触发重连"""
    ser = await asyncio.to_thread(find_sensor)
    if ser is None:
        raise RuntimeError("找不到红外传感器（/dev/ttyUSB* / /dev/ttyACM*）")

    hello_json = (f'{{"type":"hello","device":"{os.path.basename(ser.port or "serial")}",'
                  f'"baudrate":{BAUDRATE}}}')
    for t in targets:
        t.hello_json = hello_json
    log.info(f"红外传感器就绪，开始推流 @ {BAUDRATE} → {len(targets)} 个后端")

    buffer = bytearray()
    last_frame = time.monotonic()
    last_restart_cmd = time.monotonic()
    sent = 0
    last_report = time.monotonic()

    while True:
        now = time.monotonic()
        # 起流命令保活：长时间无帧可能是模块停流，重发（与后端探测命令一致，幂等）
        if now - last_frame > NO_FRAME_RESTART_CMD and now - last_restart_cmd > 1.0:
            try:
                await asyncio.to_thread(ser.write, START_CMD)
                last_restart_cmd = now
            except Exception as e:
                raise RuntimeError(f"写起流命令失败: {e}")

        # 串口冻结看门狗：重开设备
        if now - last_frame > FREEZE_THRESHOLD:
            log.warning(f"{FREEZE_THRESHOLD:.0f}s 无帧，重新探测串口")
            try:
                ser.close()
            except Exception:
                pass
            await asyncio.sleep(1.0)
            reopened = await asyncio.to_thread(find_sensor)
            if reopened is None:
                raise RuntimeError("红外传感器重开失败")
            ser = reopened
            buffer.clear()
            last_frame = time.monotonic()
            continue

        # 读串口（阻塞式放线程池，避免卡死 WS 心跳）
        n = ser.in_waiting
        if n:
            data = await asyncio.to_thread(ser.read, n)
            buffer += data
        for frame in extract_frames(buffer):
            await dispatch(targets, frame)
            sent += 1
            last_frame = time.monotonic()

        now = time.monotonic()
        if now - last_report >= 15.0:
            log.info(f"已推流 {sent} 帧（{sent / (now - last_report):.1f}fps）")
            sent = 0
            last_report = now

        await asyncio.sleep(0.01)


# 子网自动发现兜底见 opi_discovery.py（2026-09-21 与 sensors/camera 共享同一模块）

from opi_discovery import discovery_loop


async def main() -> None:
    if not SERVERS:
        log.error("未配置 OPI_IR_SERVER（逗号分隔可配多个，如 ws://192.168.1.96:8010/ws/opi-ir,ws://192.168.1.236:8010/ws/opi-ir）")
        return
    targets = [Target(url) for url in SERVERS]
    conn_tasks = [asyncio.create_task(t.loop()) for t in targets]
    conn_tasks.append(asyncio.create_task(discovery_loop(
        targets, "/ws/opi-ir", log,
        make_target=Target,
        spawn=lambda t: asyncio.create_task(t.loop()),
        hello_json=f'{{"type":"hello","device":"auto-discovered","baudrate":{BAUDRATE}}}',
    )))
    backoff = 1.0
    sensor_fail_streak = 0
    try:
        while True:
            try:
                await sensor_loop(targets)
                backoff = 1.0
                sensor_fail_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"推流会话结束: {type(e).__name__}: {e}，{backoff:.0f}s 后重连")
                # 连续找不到传感器：高概率是本进程僵死的读串口线程仍占用 tty 设备，
                # 退出进程让 systemd 拉起干净实例（Restart=always, 3s）
                if "找不到红外传感器" in str(e) or "红外传感器重开失败" in str(e):
                    sensor_fail_streak += 1
                    if sensor_fail_streak >= 3:
                        log.error("连续 3 次找不到传感器，退出进程交由 systemd 重启以释放串口占用")
                        os._exit(1)
                else:
                    sensor_fail_streak = 0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
    finally:
        for task in conn_tasks:
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("手动退出")
