# -*- coding: utf-8 -*-
"""温度双源优先级：远程 opi（香橙派推流）fresh 优先，断流回落本机 local 槽。

2026-09-12 用户需求：MCP get_temperature 的数据源合并两条采集链路（远端 Pi /
本地 USB），优先 Pi，Pi 没数据再选本地 USB——替代旧的"最后写入者胜"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.temperature import (
    LOCAL_SOURCE,
    REMOTE_FRESH_SECONDS,
    REMOTE_SOURCE,
    MLX90640Reader,
)


def _frame(max_temp: float):
    return [max_temp] * 768


def test_remote_fresh_preferred():
    """双源都在线：优先返回远程 opi 数据"""
    r = MLX90640Reader()
    r.update_temperature_data(_frame(30.0), 25.0, source=LOCAL_SOURCE)
    r.update_temperature_data(_frame(50.0), 26.0, source=REMOTE_SOURCE)
    data = r.get_temperature_data()
    assert data["maximum"] == 50.0
    assert data["source"] == REMOTE_SOURCE


def test_remote_stale_falls_back_local():
    """远程断流超时：读取自动回落本机 local，无需等新一帧写入"""
    r = MLX90640Reader()
    r.update_temperature_data(_frame(30.0), 25.0, source=LOCAL_SOURCE)
    r.update_temperature_data(_frame(50.0), 26.0, source=REMOTE_SOURCE)
    with r.lock:
        r.source_mono[REMOTE_SOURCE] -= REMOTE_FRESH_SECONDS + 1
    data = r.get_temperature_data()
    assert data["maximum"] == 30.0
    assert data["source"] == LOCAL_SOURCE


def test_remote_resume_preempts_local():
    """远程恢复推流：读取重新切回 opi 源"""
    r = MLX90640Reader()
    r.update_temperature_data(_frame(30.0), 25.0, source=LOCAL_SOURCE)
    r.update_temperature_data(_frame(50.0), 26.0, source=REMOTE_SOURCE)
    with r.lock:
        r.source_mono[REMOTE_SOURCE] -= REMOTE_FRESH_SECONDS + 1
    assert r.get_temperature_data()["source"] == LOCAL_SOURCE
    r.update_temperature_data(_frame(55.0), 26.0, source=REMOTE_SOURCE)
    data = r.get_temperature_data()
    assert data["maximum"] == 55.0
    assert data["source"] == REMOTE_SOURCE


def test_both_sources_keep_own_slots():
    """双源互不覆盖：各自槽内保留最近一帧（旧行为是单存储直接覆盖）"""
    r = MLX90640Reader()
    r.update_temperature_data(_frame(30.0), 25.0, source=LOCAL_SOURCE)
    r.update_temperature_data(_frame(50.0), 26.0, source=REMOTE_SOURCE)
    assert r.source_data[LOCAL_SOURCE]["maximum"] == 30.0
    assert r.source_data[REMOTE_SOURCE]["maximum"] == 50.0


def test_local_only_source_tagged():
    """只有本机源（无 Pi 推流）：正常返回并带 local 标记"""
    r = MLX90640Reader()
    r.update_temperature_data(_frame(30.0), 25.0, source=LOCAL_SOURCE)
    data = r.get_temperature_data()
    assert data["maximum"] == 30.0
    assert data["source"] == LOCAL_SOURCE


def test_no_data_returns_zero_defaults():
    """无任何源写入：返回初始零值（timestamp None），下游新鲜度判定判无效"""
    r = MLX90640Reader()
    data = r.get_temperature_data()
    assert data["maximum"] == 0.0
    assert data["timestamp"] is None
