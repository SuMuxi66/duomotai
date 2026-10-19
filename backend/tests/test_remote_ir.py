"""远程红外帧解析入库测试：合成 MLX90640 帧走 process_ir_frame 路径，
验证 /temperature 数据链路（与 WS 传输无关，传输层已在摄像头链路验证过同构）"""
import time
from datetime import datetime

import pytest

from app.remote_ir import IR_FRAME_SIZE, IR_HEADER, process_ir_frame
from app.temperature import _get_reader
import app.temperature as temperature_module


@pytest.fixture(autouse=True)
def _isolated_reader():
    """用独立 reader 替换模块单例：测试写入（opi 源槽）不污染其他用例的共享单例"""
    saved = temperature_module._temperature_reader
    temperature_module._temperature_reader = temperature_module.MLX90640Reader()
    yield
    temperature_module._temperature_reader = saved


def build_frame(temps_c: list[float], sensor_c: float) -> bytes:
    """按 temperature.py 的协议构造一帧：5A5A + 2字节填充 + 768点LE u16(×100) + 传感器LE u16 + 2字节填充"""
    buf = bytearray(IR_HEADER + b"\x00\x00")
    for t in temps_c:
        buf += int(round(t * 100)).to_bytes(2, "little")
    buf += int(round(sensor_c * 100)).to_bytes(2, "little")
    buf += b"\x00\x00"
    assert len(buf) == IR_FRAME_SIZE
    return bytes(buf)


def make_pixel_frame(max_c: float, min_c: float, base_c: float) -> bytes:
    temps = [base_c] * 768
    temps[0] = min_c
    temps[767] = max_c
    return build_frame(temps, sensor_c=base_c)


def test_process_ir_frame_accepts_valid_frame():
    reader = _get_reader()
    frame = make_pixel_frame(max_c=30.0, min_c=25.0, base_c=26.0)
    assert process_ir_frame(frame) is True
    data = reader.get_temperature_data()
    assert data["maximum"] == 30.0
    assert data["minimum"] == 25.0
    # 目标温度 = 768 点中心点（index 384）= 26.0
    assert data["target"] == 26.0
    assert 25.0 < data["average"] <= 26.0
    assert data["sensor"] == 26.0
    ts = datetime.fromisoformat(str(data["timestamp"]))
    assert (datetime.now() - ts).total_seconds() < 5


def test_process_ir_frame_rejects_bad_frames():
    reader = _get_reader()
    good = make_pixel_frame(max_c=30.0, min_c=25.0, base_c=26.0)
    assert process_ir_frame(good) is True

    # 错误帧头 / 过短 / 空帧：一律丢弃，且不清掉上一帧的合法数据
    assert process_ir_frame(b"\x12\x34" + good[2:]) is False
    assert process_ir_frame(good[:100]) is False
    assert process_ir_frame(b"") is False
    data = reader.get_temperature_data()
    assert data["maximum"] == 30.0


def test_frame_builder_matches_reader_protocol():
    """帧构造器与 reader.parse_temperatures 的偏移/字节序约定一致"""
    reader = _get_reader()
    temps, sensor_temp = reader.parse_temperatures(
        build_frame([25.0, 26.5, 30.0] * 256, sensor_c=28.5)
    )
    assert len(temps) == 768
    assert temps[0] == 25.0
    assert temps[1] == 26.5
    assert temps[2] == 30.0
    assert sensor_temp == 28.5
