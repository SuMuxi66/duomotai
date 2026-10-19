"""opi_weight_reader.py — 称重板卡找口与读口解卡（2026-09-21 从 opi_sensors_client.py 拆出）。

纯搬运：函数体与拆分前完全一致（含全部踩坑注释）。
"""
from __future__ import annotations

from typing import Optional

import serial

from opi_serial_common import WEIGHT_BAUD, WEIGHT_DEVICE, log_opi_sensors as log
from opi_serial_classify import classify_ports


def _force_unblock_close(ser: serial.Serial) -> Optional[serial.Serial]:
    """强制解除称重串口内核态读阻塞：关闭并按原参数重开，返回新句柄。

    ch341 被集线器电气抖动楔死时 readline 会卡在内核态（外层 asyncio.wait_for
    只能放弃等待，卡住的线程仍在读），关闭再重开是该状态下的解卡手段；
    重开失败返回 None，由调用方走"重找板卡"路径。"""
    port = ser.port
    try:
        ser.close()
    except Exception:
        pass
    try:
        new_ser = serial.Serial(port=port, baudrate=WEIGHT_BAUD,
                                bytesize=8, parity="N", stopbits=1, timeout=1)
        log.warning(f"称重串口 {port} 读卡死，已强制重开解卡")
        return new_ser
    except Exception as e:
        log.warning(f"称重串口 {port} 强制重开失败: {e}")
        return None


def find_weight_port() -> Optional[serial.Serial]:
    """找到称重板卡：env 显式钉死 > 按应答特征分类（断电重排号也功能对应）"""
    if WEIGHT_DEVICE:
        try:
            ser = serial.Serial(port=WEIGHT_DEVICE, baudrate=WEIGHT_BAUD,
                                bytesize=8, parity="N", stopbits=1, timeout=1)
            log.info(f"称重板卡（显式配置）: {WEIGHT_DEVICE} @ {WEIGHT_BAUD}")
            return ser
        except Exception as e:
            log.error(f"显式称重串口打开失败 {WEIGHT_DEVICE}: {e}")
            return None
    _, weight_dev = classify_ports()
    if not weight_dev:
        return None
    try:
        ser = serial.Serial(port=weight_dev, baudrate=WEIGHT_BAUD,
                            bytesize=8, parity="N", stopbits=1, timeout=1)
        log.info(f"称重板卡: {weight_dev} @ {WEIGHT_BAUD}")
        return ser
    except Exception as e:
        log.warning(f"分类出的称重口 {weight_dev} 打开失败: {e}")
        return None
