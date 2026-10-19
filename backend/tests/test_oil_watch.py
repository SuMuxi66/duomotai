# -*- coding: utf-8 -*-
"""
油温监测提醒（Oil Watch）单元测试
覆盖：目标温度解析、启动/停止生命周期、连续命中防抖、超时、OIL_WATCH 工具标记分发
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import cooking_core, cooking_tools, oil_watch
from app.session import SessionState
import app.ws.llm_handler as llm_handler_module


# ---------- 假件 ----------

class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def make_state():
    st = SimpleNamespace(
        interrupted=False,
        oil_watch=None,
        oil_watch_task=None,
    )
    return st


def make_cooking_state(current_step=1, recipe=None):
    """带做饭字段的假状态（供油温快答"结合菜谱"测试）"""
    recipe = recipe or {
        "name": "红烧狮子头",
        "steps": [
            {"step_number": 1, "title": "准备食材", "content": "备好猪肉、荸荠等原料"},
            {"step_number": 2, "title": "制作肉馅", "content": "肉馅中加入调料拌匀"},
            {"step_number": 3, "title": "团成丸子", "content": "将肉馅团成大丸子"},
            {"step_number": 4, "title": "油炸丸子", "content": "锅中倒入足量食用油，加热至五成热（约 150℃），下丸子炸至金黄"},
            {"step_number": 5, "title": "红烧收汁", "content": "另起锅加糖炒色，放入丸子小火炖煮收汁"},
        ],
        "tips": "油温保持五六成热（约150-180°C）复炸更酥脆。",
    }
    st = make_state()
    st.cooking_enabled = True
    st.cooking_state = "COOKING"
    st.current_recipe = recipe
    st.current_step = current_step
    return st


@pytest.fixture
def capture_announce(monkeypatch):
    """拦截 send_cooking_text_with_tts，记录播报文本"""
    texts = []

    async def _send(runtime, ws, state, text, **kw):
        texts.append(text)

    monkeypatch.setattr(cooking_core, "send_cooking_text_with_tts", _send)
    return texts


@pytest.fixture
def fake_temp(monkeypatch):
    """控制 get_valid_oil_temp 的返回值"""
    holder = {"temp": None}

    def _set(v):
        holder["temp"] = v

    monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: holder["temp"])
    return _set


# ---------- 目标温度解析 ----------

class TestResolveTarget:
    def test_auto_and_empty_fallback(self):
        assert oil_watch.resolve_target("auto") == 160
        assert oil_watch.resolve_target("") == 160
        assert oil_watch.resolve_target(None) == 160

    def test_numeric_and_clamp(self):
        assert oil_watch.resolve_target("160") == 160
        assert oil_watch.resolve_target("999") == 300
        assert oil_watch.resolve_target("10") == 50

    def test_garbage_fallback(self):
        assert oil_watch.resolve_target("hot") == 160


# ---------- 生命周期 ----------

class TestLifecycle:
    async def test_start_creates_task_and_stop_clears(self, monkeypatch):
        st = make_state()
        texts = []
        runtime = SimpleNamespace()
        ws = FakeWS()

        async def _noop_announce(*a, **k):
            texts.append(a[3] if len(a) > 3 else "")

        monkeypatch.setattr(cooking_core, "send_cooking_text_with_tts", _noop_announce)
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)

        await oil_watch.start_oil_watch(runtime, ws, st, 160)
        assert st.oil_watch is not None
        assert st.oil_watch["target"] == 160
        assert st.oil_watch_task is not None
        assert not st.oil_watch_task.done()

        oil_watch.stop_oil_watch(st)
        assert st.oil_watch is None
        assert st.oil_watch_task is None
        await asyncio.sleep(0.05)  # 让取消落地

    async def test_restart_replaces_old_watch(self, monkeypatch):
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        monkeypatch.setattr(cooking_core, "send_cooking_text_with_tts", lambda *a, **k: None)
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)

        await oil_watch.start_oil_watch(runtime, ws, st, 120)
        old_task = st.oil_watch_task
        await oil_watch.start_oil_watch(runtime, ws, st, 180)
        assert st.oil_watch["target"] == 180
        assert st.oil_watch_task is not old_task
        await asyncio.sleep(0.05)


# ---------- 达标播报与超时 ----------

class TestMonitorLoop:
    async def test_reaches_target_after_consecutive_hits(self, monkeypatch, capture_announce, fake_temp):
        """连续 3 次达标 → 播报"油温到了"并自动停止"""
        monkeypatch.setattr(oil_watch, "POLL_INTERVAL", 0.01)
        fake_temp(170)  # 恒定高于目标 160

        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.start_oil_watch(runtime, ws, st, 160)

        await asyncio.sleep(0.15)  # 3 次命中需要约 3 个轮询周期
        assert any("油温到了" in t for t in capture_announce), capture_announce
        assert st.oil_watch is None  # 一次性触发后自动停止

    async def test_below_target_keeps_watching(self, monkeypatch, capture_announce, fake_temp):
        """温度一直低于目标 → 不播报、继续监测"""
        monkeypatch.setattr(oil_watch, "POLL_INTERVAL", 0.01)
        fake_temp(100)

        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.start_oil_watch(runtime, ws, st, 160)

        await asyncio.sleep(0.15)
        assert capture_announce == []
        assert st.oil_watch is not None  # 仍在监测
        oil_watch.stop_oil_watch(st)

    async def test_timeout_announces_and_stops(self, monkeypatch, capture_announce, fake_temp):
        """超过监测时限未达标 → 播报超时提示并停止"""
        monkeypatch.setattr(oil_watch, "POLL_INTERVAL", 0.01)
        monkeypatch.setattr(oil_watch, "TIMEOUT_SECONDS", 0.05)
        fake_temp(100)

        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.start_oil_watch(runtime, ws, st, 160)

        await asyncio.sleep(0.3)
        assert any("油温监测超时" in t for t in capture_announce), capture_announce
        assert st.oil_watch is None

    async def test_sensor_invalid_resets_consecutive(self, monkeypatch, capture_announce, fake_temp):
        """传感器无有效数据 → 连续命中清零，不播报"""
        monkeypatch.setattr(oil_watch, "POLL_INTERVAL", 0.01)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.start_oil_watch(runtime, ws, st, 160)

        fake_temp(170)
        await asyncio.sleep(0.03)  # 命中 2 次
        fake_temp(None)            # 数据失效 → 清零
        await asyncio.sleep(0.03)
        fake_temp(170)             # 重新计数
        await asyncio.sleep(0.12)  # 3 次命中后播报
        assert any("油温到了" in t for t in capture_announce)
        oil_watch.stop_oil_watch(st)


# ---------- 工具标记分发 ----------

class TestDispatchOilWatch:
    async def test_dispatch_start(self, monkeypatch, capture_announce):
        """[COOKING:OIL_WATCH:160] → 确认语 + 启动监测"""
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)
        st = make_state()
        st.interrupted = False
        runtime = SimpleNamespace()
        ws = FakeWS()

        handled = await cooking_tools.dispatch_cooking_tool(
            runtime, ws, st, {"action": "OIL_WATCH", "param": "160"}, request_id="r1")

        assert handled is True
        assert any("监测油温" in t for t in capture_announce)
        assert st.oil_watch is not None and st.oil_watch["target"] == 160
        oil_watch.stop_oil_watch(st)

    async def test_dispatch_stop(self, monkeypatch, capture_announce):
        """[COOKING:OIL_WATCH:STOP] → 停止监测"""
        st = make_state()
        st.interrupted = False
        st.oil_watch = {"target": 160, "started_at": 0, "consecutive": 0}
        runtime = SimpleNamespace()
        ws = FakeWS()

        handled = await cooking_tools.dispatch_cooking_tool(
            runtime, ws, st, {"action": "OIL_WATCH", "param": "STOP"}, request_id="r1")

        assert handled is True
        assert any("停止油温监测" in t for t in capture_announce)
        assert st.oil_watch is None

    async def test_dispatch_auto_target(self, monkeypatch, capture_announce):
        """[COOKING:OIL_WATCH:auto] → 兜底 160°C"""
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)
        st = make_state()
        st.interrupted = False
        runtime = SimpleNamespace()
        ws = FakeWS()

        await cooking_tools.dispatch_cooking_tool(
            runtime, ws, st, {"action": "OIL_WATCH", "param": "auto"}, request_id="r1")

        assert any("160" in t for t in capture_announce)
        oil_watch.stop_oil_watch(st)


# ---------- 油温要求提取（菜谱/步骤） ----------

class TestExtractOilTempRequirement:
    def test_current_step_hint(self):
        """当前步骤含"X成热（约 N°C）" → 命中当前步骤"""
        st = make_cooking_state(current_step=4)
        req = oil_watch.extract_oil_temp_requirement(st)
        assert req is not None
        assert req["source"] == "step"
        assert req["step_index"] == 4
        assert req["desc"] in ("约150°C", "五成热（约150-160°C）")

    def test_fallback_other_step(self):
        """当前步骤无油温信息 → 回退到其他步骤"""
        st = make_cooking_state(current_step=1)
        req = oil_watch.extract_oil_temp_requirement(st)
        assert req is not None
        assert req["source"] == "other_step"
        assert req["step_index"] == 4
        assert req["lo"] >= 100

    def test_fallback_tips(self):
        """步骤都无油温信息 → 用小贴士"""
        recipe = {
            "name": "无油温步骤",
            "steps": [
                {"step_number": 1, "title": "备料", "content": "备好食材"},
                {"step_number": 2, "title": "炖煮", "content": "加水小火炖 30 分钟"},
            ],
            "tips": "收汁时油温五六成热（约150-180°C）更香。",
        }
        st = make_cooking_state(current_step=2, recipe=recipe)
        req = oil_watch.extract_oil_temp_requirement(st)
        assert req is not None
        assert req["source"] == "tips"
        assert req["lo"] == 150 and req["hi"] == 180

    def test_no_recipe_returns_none(self):
        """无菜谱/无步骤 → None（维持原快答）"""
        st = SimpleNamespace(current_recipe=None, current_step=0)
        assert oil_watch.extract_oil_temp_requirement(st) is None

    def test_hint_ignores_water_temp(self):
        """水温/烤箱温等噪声语境不应被当油温"""
        recipe = {
            "name": "干扰",
            "steps": [
                {"step_number": 1, "title": "焖煮", "content": "水温保持在 70℃ 焖 25 分钟"},
                {"step_number": 2, "title": "完成", "content": "取出即可"},
            ],
            "tips": "",
        }
        st = make_cooking_state(current_step=1, recipe=recipe)
        assert oil_watch.extract_oil_temp_requirement(st) is None


# ---------- 油温快答拦截问法 ----------

class TestLooksLikeOilTempQuery:
    def test_basic_coverage(self):
        """含"油温/锅温"字样的常见问法都应命中"""
        for q in (
            "现在油温多少",
            "油温到了吗",
            "油温够不够热可以下菜了吗",
            "锅温多少",
            "油温到没到",
        ):
            assert oil_watch.looks_like_oil_temp_query(q), q

    def test_monitor_requests_excluded(self):
        """"盯/提醒/监测"类请求不拦截（交给 LLM 输出 OIL_WATCH 标记）"""
        for q in ("帮我盯油温", "油温到了提醒我", "油温监测一下", "盯着锅温"):
            assert not oil_watch.looks_like_oil_temp_query(q), q

    def test_extra_phrases_cooking_active(self):
        """不含"油温"字样但明确下锅时机问法：做饭模式开启时命中"""
        st = make_cooking_state(current_step=3)
        for q in ("现在几成热", "可以下菜了吗", "能下料了吗", "油够不够热", "油热了没", "八成热了吗"):
            assert oil_watch.looks_like_oil_temp_query(q, st), q

    def test_extra_phrases_idle_not_match(self):
        """做饭模式未开启时，不含"油温"字样的问法不拦截（防误伤普通问答）"""
        st = SimpleNamespace(cooking_enabled=False, current_recipe=None)
        for q in ("现在几成热", "可以下菜了吗", "油够不够热"):
            assert not oil_watch.looks_like_oil_temp_query(q, st), q

    def test_statements_not_intercepted(self):
        """陈述句（先下菜再放肉/下锅了）即使做饭中也不拦截"""
        st = make_cooking_state(current_step=3)
        for q in ("先下菜再放肉", "下锅了", "油温是什么原理"):
            assert not oil_watch.looks_like_oil_temp_query(q, st), q


# ---------- 快答结合菜谱 ----------

class TestAnswerOilTempQueryCombined:
    async def test_combined_reply_reached(self, monkeypatch, capture_announce, fake_temp):
        """实测已达到步骤要求 → 回复含实测温度 + 菜谱/步骤要求 + 可下锅"""
        fake_temp(165)
        st = make_cooking_state(current_step=4)
        runtime = SimpleNamespace()
        ws = FakeWS()

        handled = await oil_watch.answer_oil_temp_query(runtime, ws, st, "油温够不够热")
        assert handled is True
        text = "".join(capture_announce)
        assert "165" in text        # 实测温度
        assert "红烧狮子头" in text  # 菜谱名
        assert "第4步" in text or "要求" in text
        assert any(k in text for k in ("可以", "下锅", "达到", "要求"))

    async def test_combined_reply_below_target(self, monkeypatch, capture_announce, fake_temp):
        """实测未达到步骤要求 → 给出还差多少建议"""
        fake_temp(110)
        st = make_cooking_state(current_step=4)
        runtime = SimpleNamespace()
        ws = FakeWS()

        await oil_watch.answer_oil_temp_query(runtime, ws, st, "油温可以下菜了吗")
        text = "".join(capture_announce)
        assert "110" in text          # 实测温度
        assert "还差" in text or "再等" in text  # 建议
        assert "红烧狮子头" in text

    async def test_no_recipe_falls_back_to_generic(self, monkeypatch, capture_announce, fake_temp):
        """无当前菜谱 → 维持原快答（实测 + 通用成热参考）"""
        fake_temp(145)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()

        await oil_watch.answer_oil_temp_query(runtime, ws, st, "现在油温多少")
        text = "".join(capture_announce)
        assert "145" in text
        assert "三四成热" in text
        assert "盯着" in text

    async def test_no_sensor_data(self, monkeypatch, capture_announce):
        """传感器无数据 → 提示探头对准锅面"""
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)
        st = make_cooking_state(current_step=4)
        runtime = SimpleNamespace()
        ws = FakeWS()

        await oil_watch.answer_oil_temp_query(runtime, ws, st, "油温多少了")
        text = "".join(capture_announce)
        assert "没有读到有效数据" in text


# ---------- 油温盯守确认门（2026-09-06 回归）----------


class TestOilWatchConfirmHelpers:
    """确认/取消词匹配（is_oil_watch_confirm_text / is_oil_watch_cancel_text）"""

    def test_confirm_phrases(self):
        for q in ("需要，帮我盯着吧", "帮我盯着", "继续盯着", "开始盯",
                  "好", "好的", "好啊", "行", "可以", "需要", "ok", "okay",
                  "继续", "开始", "盯油温", "帮我看", "看着吧"):
            assert oil_watch.is_oil_watch_confirm_text(q), q

    def test_confirm_rejects_questions(self):
        """含疑问语气词的语句不视为确认（避免"我要怎么做"被"要"误判）"""
        for q in ("我要怎么做这道菜吗", "现在油温多少", "盯油温是什么意思",
                  "为什么需要盯着", "怎么盯", "多少度能下锅"):
            assert not oil_watch.is_oil_watch_confirm_text(q), q

    def test_confirm_rejects_blank(self):
        assert oil_watch.is_oil_watch_confirm_text("") is False
        assert oil_watch.is_oil_watch_confirm_text(None) is False

    def test_cancel_words(self):
        for q in ("不用了", "算了", "不需要", "别盯了", "暂不", "先不", "取消"):
            assert oil_watch.is_oil_watch_cancel_text(q), q

    def test_cancel_rejects_blank(self):
        assert oil_watch.is_oil_watch_cancel_text("") is False
        assert oil_watch.is_oil_watch_cancel_text(None) is False


class TestOilWatchProposalSetsFlag:
    """油温快答 fallback 文案发出"我帮你盯着油温"→ 置确认门标志"""

    async def test_no_recipe_proposal_sets_flag(self, monkeypatch, capture_announce, fake_temp):
        """无菜谱快答回复含盯守提议 → 标志置位、目标=兜底 160"""
        fake_temp(145)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(runtime, ws, st, "现在油温多少")
        assert st.awaiting_oil_watch_confirm is True
        assert st.oil_watch_proposed_target == 160
        assert any("盯着" in t for t in capture_announce)

    async def test_recipe_reply_does_not_set_flag(self, monkeypatch, capture_announce, fake_temp):
        """结合菜谱的快答（不含盯守提议）不置标志"""
        fake_temp(165)
        st = make_cooking_state(current_step=4)
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(runtime, ws, st, "油温够不够热")
        assert getattr(st, "awaiting_oil_watch_confirm", False) is False

    async def test_sensor_no_data_does_not_set_flag(self, monkeypatch, capture_announce):
        """传感器无数据提示不含盯守提议，不置标志"""
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: None)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(runtime, ws, st, "油温多少了")
        assert getattr(st, "awaiting_oil_watch_confirm", False) is False


class _OilWatchGateHarness:
    """handle_user_text 路由夹具：mock 温度/TTS/启动，捕获播报与启动调用。

    2026-09-12 起油温快答默认关闭（改走 LLM 工具链路），本组测试验证的就是
    旧快答 + 确认门逻辑，因此统一打开 OIL_TEMP_FAST_ANSWER_ENABLED。
    """

    def __init__(self, monkeypatch, temp=145):
        self.sent = []
        self.watch_started = []
        self.llm_called = []

        from app.config import settings
        monkeypatch.setattr(settings, "oil_temp_fast_answer_enabled", True)
        monkeypatch.setattr(oil_watch, "get_valid_oil_temp", lambda: temp)

        async def fake_send_text(runtime, ws, state, text, **kw):
            self.sent.append(text)

        async def fake_start_oil_watch(runtime, ws, state, target):
            self.watch_started.append(target)

        async def fake_llm_response(*a, **k):
            self.llm_called.append("llm")
            return "（不应到达）"

        async def fake_scene_response(*a, **k):
            return False

        monkeypatch.setattr(cooking_core, "send_cooking_text_with_tts", fake_send_text)
        monkeypatch.setattr(oil_watch, "start_oil_watch", fake_start_oil_watch)
        from app.ws import llm_handler as _llm
        monkeypatch.setattr(_llm, "handle_llm_response", fake_llm_response)
        monkeypatch.setattr(_llm, "handle_scene_response", fake_scene_response)

    @property
    def runtime(self):
        return SimpleNamespace(llm=None, tts=None)


def test_route_confirm_starts_watch_no_fan(monkeypatch):
    """回归：盯守提议后"需要，帮我盯着吧"→ 启动 OilWatch，不到 LLM、不开风扇。"""
    h = _OilWatchGateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-oil-gate-confirm"

    # 第一轮：油温查询 → fallback 提议盯守
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "现在油温多少", request_id="r1"))
    assert state.awaiting_oil_watch_confirm is True

    # 第二轮：短确认 → 启动监测，不到 LLM/不开风扇
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要，帮我盯着吧", request_id="r2"))
    assert h.llm_called == []
    assert h.watch_started == [160]
    assert state.awaiting_oil_watch_confirm is False
    assert any("监测油温" in t for t in h.sent)
    assert not any("[FAN_ON]" in t for t in h.sent)


def test_route_cancel_does_not_start(monkeypatch):
    """盯守提议后"不用了"→ 回复放弃、不启动监测、清标志。"""
    h = _OilWatchGateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-oil-gate-cancel"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "现在油温多少", request_id="r1"))
    assert state.awaiting_oil_watch_confirm is True

    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "不用了", request_id="r2"))
    assert h.watch_started == []
    assert h.llm_called == []
    assert state.awaiting_oil_watch_confirm is False
    assert any("需要的时候随时叫我" in t for t in h.sent)


def test_route_other_text_clears_flag_and_routes(monkeypatch):
    """盯守提议后收到无关内容 → 清标志放行到 LLM（不劫持后续消息）。"""
    h = _OilWatchGateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-oil-gate-other"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "现在油温多少", request_id="r1"))
    assert state.awaiting_oil_watch_confirm is True

    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "随便聊聊", request_id="r2"))
    assert state.awaiting_oil_watch_confirm is False
    assert h.llm_called  # 放行到 LLM
    assert h.watch_started == []


def test_route_expired_clears(monkeypatch):
    """确认门 10 分钟过期：即使回复"需要"也不启动监测，清标志放行。"""
    h = _OilWatchGateHarness(monkeypatch)
    state = SessionState()
    state.session_id = "unit-oil-gate-expired"
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "现在油温多少", request_id="r1"))
    assert state.awaiting_oil_watch_confirm is True

    state.oil_watch_proposed_at -= oil_watch.OIL_WATCH_CONFIRM_EXPIRE_S + 1
    asyncio.run(llm_handler_module.handle_user_text(
        h.runtime, None, state, "需要，帮我盯着吧", request_id="r2"))
    assert h.watch_started == []
    assert state.awaiting_oil_watch_confirm is False


# ---------- 问句意图 → 目标油温（2026-09-06 新增）----------


class TestQuestionIntentReply:
    """问句自带烹饪意图（炸/煎/炒…）时按意图目标回答：实测温度 + 目标区间 +
    温差结论 + 下锅后建议；未达下限附带提议盯守（复用既有确认门）。"""

    async def test_fry_low_temp_proposes_watch(
        self, monkeypatch, capture_announce, fake_temp
    ):
        """炸意图+低温：回复含实测温度/还差/再等/不用关小火，并提议盯守目标=170"""
        fake_temp(32.0)
        st = make_state()  # 无菜谱 → 不走菜谱要求分支
        runtime = SimpleNamespace()
        ws = FakeWS()
        text = (
            "我想炸出外酥里嫩的软炸里脊/炸鱼块，我现在能下锅吗？"
            "还是再等油温升到多少度？下锅后我需要立刻关小火吗？"
        )
        handled = await oil_watch.answer_oil_temp_query(runtime, ws, st, text)
        assert handled is True
        reply = "".join(capture_announce)
        assert "32" in reply                       # 实测温度
        assert "还差" in reply                     # 温差结论
        assert "再等" in reply                     # 还需等待
        assert "不用关小火" in reply               # 下锅后建议
        # 提议盯守：确认门标志已置位，目标=炸意图下限 170
        assert st.awaiting_oil_watch_confirm is True
        assert st.oil_watch_proposed_target == 170

    async def test_fry_reached_no_proposal(
        self, monkeypatch, capture_announce, fake_temp
    ):
        """炸意图+达标（175）：回复含可以下锅/不用关小火，不提议盯守"""
        fake_temp(175.0)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(
            runtime, ws, st, "炸鱼块能下锅了吗")
        reply = "".join(capture_announce)
        assert "可以下锅" in reply
        assert "不用关小火" in reply
        # 达标不提议盯守
        assert getattr(st, "awaiting_oil_watch_confirm", False) is False

    async def test_fry_above_range_direct(
        self, monkeypatch, capture_announce, fake_temp
    ):
        """炸意图+超过区间（190）：回复含'直接下锅'"""
        fake_temp(190.0)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(
            runtime, ws, st, "炸鱼块能下锅了吗")
        reply = "".join(capture_announce)
        assert "直接下锅" in reply

    async def test_no_intent_falls_back_generic(
        self, monkeypatch, capture_announce, fake_temp
    ):
        """无意图问句（"现在油温多少度"）→ 通用兜底，含"参考"，提议目标=FALLBACK_TARGET(160)"""
        fake_temp(32.0)
        st = make_state()
        runtime = SimpleNamespace()
        ws = FakeWS()
        await oil_watch.answer_oil_temp_query(
            runtime, ws, st, "现在油温多少度")
        reply = "".join(capture_announce)
        assert "参考" in reply
        assert st.awaiting_oil_watch_confirm is True
        assert st.oil_watch_proposed_target == oil_watch.FALLBACK_TARGET
        assert st.oil_watch_proposed_target == 160

    async def test_recipe_requirement_priority_over_intent(
        self, monkeypatch, capture_announce, fake_temp
    ):
        """菜谱要求优先于问句意图：make_cooking_state(current_step=4)，第 4 步
        要求五成热（约 150℃），问句带"炸"且温度 32 → 走 _build_recipe_combined_reply，
        不走意图盯守（不置意图目标 170）。"""
        fake_temp(32.0)
        st = make_cooking_state(current_step=4)
        runtime = SimpleNamespace()
        ws = FakeWS()
        # 先确认第 4 步能解析出油温要求
        req = oil_watch.extract_oil_temp_requirement(st)
        assert req is not None and req["source"] == "step"
        assert req["lo"] == 150  # 五成热解析为 150

        await oil_watch.answer_oil_temp_query(
            runtime, ws, st, "我想炸鱼块现在能下锅了吗")
        reply = "".join(capture_announce)
        # 走菜谱分支：含第 4 步/150 字样
        assert "第4步" in reply or "150" in reply
        # 不含意图分支专属文案"还需要再等一会"
        assert "还需要再等一会" not in reply
        # 意图盯守目标 170 不应被设置（菜谱分支不置意图盯守）
        assert getattr(st, "oil_watch_proposed_target", None) != 170

    def test_extract_intent_specificity_huachao(self):
        """意图表特异性：'滑炒肉片现在能下锅吗' 命中'滑炒'(100) 而非'炒'(150)"""
        intent = oil_watch._extract_question_oil_intent("滑炒肉片现在能下锅吗")
        assert intent is not None
        assert intent[0] == "滑炒"
        assert intent[1] == 100
        assert intent[2] == 120
