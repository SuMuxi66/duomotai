# -*- coding: utf-8 -*-
"""温度确定性快答："现在温度多少度"类查询直接用传感器实测回答，
不走 LLM（GLM 等模型收到实测数据仍可能拒答"无法获取实时温度"）。"""
import asyncio
from datetime import datetime, timedelta

import app.temperature as temperature_module
from app.temperature import (
    answer_temperature_query,
    looks_like_general_temp_query,
)

FRESH_DATA = {
    "target": 25.3, "average": 25.3, "maximum": 27.1,
    "minimum": 25.3, "sensor": 30.9,
    "timestamp": "FRESH",  # 哨兵：_run 中替换为当前时间（全量跑时模块加载到执行可能间隔 >30s）
}


# ---------- 触发判定 ----------

def test_matches_now_temp_queries():
    assert looks_like_general_temp_query("现在温度多少度")
    assert looks_like_general_temp_query("当前温度是多少")
    assert looks_like_general_temp_query("实时温度多少")


def test_rejects_oil_and_knowledge_queries():
    assert not looks_like_general_temp_query("油温多少了")           # 油温快答管
    assert not looks_like_general_temp_query("锅温够不够")           # 油温快答管
    assert not looks_like_general_temp_query("水多少度沸腾")         # 知识类（无即时限定词）
    assert not looks_like_general_temp_query("人体正常温度是多少")    # 知识类
    assert not looks_like_general_temp_query("")                     # 空
    assert not looks_like_general_temp_query("现在的温度是多少度啊我感觉厨房里面好热呀")  # 超长


# ---------- answer_temperature_query ----------

def _run(monkeypatch, data):
    reply = {}
    data = dict(data)
    if data.get("timestamp") == "FRESH":
        data["timestamp"] = datetime.now().isoformat()

    async def fake_send(runtime, ws, state, text, **kw):
        reply["text"] = text

    monkeypatch.setattr(temperature_module, "get_temperature", lambda: data)
    monkeypatch.setattr(
        __import__("app.cooking_core", fromlist=["send_cooking_text_with_tts"]),
        "send_cooking_text_with_tts", fake_send,
    )
    answered = asyncio.run(answer_temperature_query(None, None, None, "现在温度多少度"))
    return answered, reply.get("text", "")


def test_answers_with_fresh_sensor_data(monkeypatch):
    answered, text = _run(monkeypatch, FRESH_DATA)
    assert answered is True
    assert "27" in text and "25" in text  # 实测最高/平均温度
    assert "°C" in text


def test_stale_timestamp_falls_through(monkeypatch):
    stale = dict(FRESH_DATA, timestamp=(datetime.now() - timedelta(seconds=60)).isoformat())
    answered, _ = _run(monkeypatch, stale)
    assert answered is False  # 过期数据放行走原 LLM 路径


def test_missing_timestamp_falls_through(monkeypatch):
    answered, _ = _run(monkeypatch, {**FRESH_DATA, "timestamp": None})
    assert answered is False
