# -*- coding: utf-8 -*-
"""温度假数据源（TEMPERATURE_MOCK_ENABLED）：围绕基准环境温度平滑随机游走。
get_temperature() 是唯一数据出口——/temperature 接口、油温监测、温度语音快答
全部经它取数，mock 拦在这一层即覆盖全部消费方。"""
import pytest

import app.temperature as temperature_module
from app.config import settings


@pytest.fixture()
def reset_singletons():
    """保存/恢复 temperature 模块的 mock 与 reader 单例，避免测试间污染。

    进入时先置空：其他测试文件可能在 mock 开启的 env 下调过 get_temperature()，
    留下带演示机基准（如 70°C）的缓存实例——不置空则 _get_mock() 复用旧实例，
    本文件的 monkeypatch 基准失效（全量跑挂、单文件跑过）。
    """
    saved = (temperature_module._mock_source, temperature_module._temperature_reader)
    temperature_module._mock_source = None
    temperature_module._temperature_reader = None
    yield
    if temperature_module._mock_source:
        temperature_module._mock_source.stop()
    if temperature_module._temperature_reader:
        temperature_module._temperature_reader.stop()
    temperature_module._mock_source, temperature_module._temperature_reader = saved


# ---------- mock 开：get_temperature 出假数据 ----------

def test_mock_enabled_returns_env_temperature(monkeypatch, reset_singletons):
    monkeypatch.setattr(settings, "temperature_mock_enabled", True)
    monkeypatch.setattr(settings, "temperature_mock_base", 26.0)
    monkeypatch.setattr(settings, "temperature_mock_amplitude", 1.2)
    data = temperature_module.get_temperature()
    assert data["timestamp"]  # 启动即有数据，不等第一个周期
    # 目标温度夹在基准±幅度内，输出叠加 ±0.2 正弦漂移
    assert 26.0 - 1.5 <= data["target"] <= 26.0 + 1.5
    assert data["maximum"] >= data["average"] >= data["minimum"]


def test_mock_values_fluctuate_over_ticks(monkeypatch, reset_singletons):
    monkeypatch.setattr(settings, "temperature_mock_enabled", True)
    mock = temperature_module._get_mock()
    seen = set()
    for _ in range(200):
        mock._tick()
        seen.add(mock.get_data()["target"])
    assert len(seen) > 1  # 持续上下浮动，不是死值


def test_mock_start_monitoring_skips_serial(monkeypatch, reset_singletons):
    monkeypatch.setattr(settings, "temperature_mock_enabled", True)

    def fail_reader_start(self):
        raise AssertionError("mock 模式不得启动串口 reader")

    monkeypatch.setattr(temperature_module.MLX90640Reader, "start", fail_reader_start)
    assert temperature_module.start_temperature_monitoring() == {"status": "started", "mock": True}
    assert temperature_module.stop_temperature_monitoring() == {"status": "stopped", "mock": True}


def test_mock_mode_ignores_external_writes(monkeypatch, reset_singletons):
    """假数据模式下温重板卡 Tmx 写入被忽略，mock 数值不被外部来源覆盖"""
    monkeypatch.setattr(settings, "temperature_mock_enabled", True)
    # 演示机 env 可能把基准设成 70°C，固定基准避免断言依赖环境配置
    monkeypatch.setattr(settings, "temperature_mock_base", 26.0)
    monkeypatch.setattr(settings, "temperature_mock_amplitude", 1.2)
    temperature_module.apply_external_temperature(99.0, 98.0, 97.0, 96.0)
    data = temperature_module.get_temperature()
    assert data["maximum"] < 50.0


# ---------- mock 关：真实链路行为不变 ----------

def test_real_mode_still_accepts_external_writes(monkeypatch, reset_singletons):
    """开关关闭时外部写入链路（温重板卡 Tmx）行为与原来一致（不真启动串口扫描）"""
    monkeypatch.setattr(settings, "temperature_mock_enabled", False)
    monkeypatch.setattr(temperature_module.MLX90640Reader, "start", lambda self: None)
    temperature_module.apply_external_temperature(99.0, 98.0, 97.0, 96.0)
    data = temperature_module.get_temperature()
    assert data["maximum"] == 99.0
    assert data["target"] == 98.0
    assert data["minimum"] == 97.0
