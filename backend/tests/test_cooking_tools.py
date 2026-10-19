# -*- coding: utf-8 -*-
"""
单元测试：cooking_tools 工具调用协议
覆盖：工具标记解析（parse_cooking_tool）、剥离（strip_cooking_tools）、调度（dispatch_cooking_tool）
"""
import pytest

from app import cooking_tools, cooking_core
from app.cooking_tools import parse_cooking_tool, strip_cooking_tools, dispatch_cooking_tool


# ============ 标记解析 ============

class TestParseCookingTool:
    def test_start(self):
        assert parse_cooking_tool("[COOKING:START]") == {"action": "START", "param": ""}

    def test_select_with_param(self):
        assert parse_cooking_tool("[COOKING:SELECT:番茄炒蛋]") == {
            "action": "SELECT", "param": "番茄炒蛋"}

    def test_param_with_spaces(self):
        assert parse_cooking_tool("[COOKING:SELECT: 西红柿炒鸡蛋 ]") == {
            "action": "SELECT", "param": "西红柿炒鸡蛋"}

    def test_lowercase(self):
        assert parse_cooking_tool("[cooking:confirm_step]")["action"] == "CONFIRM_STEP"

    def test_skip_and_exit(self):
        assert parse_cooking_tool("[COOKING:SKIP_STEP]")["action"] == "SKIP_STEP"
        assert parse_cooking_tool("[COOKING:EXIT]")["action"] == "EXIT"

    def test_embedded_in_text_takes_last(self):
        # 约定只输出一个标记，多标记时取最后一个
        assert parse_cooking_tool("好的 [COOKING:SELECT:番茄炒蛋] 开始吧 [COOKING:START]")["action"] == "START"

    def test_no_marker(self):
        assert parse_cooking_tool("今天天气不错") is None
        assert parse_cooking_tool("") is None
        assert parse_cooking_tool(None) is None

    def test_partial_marker_ignored(self):
        # 未闭合的标记不算
        assert parse_cooking_tool("文本 [COOKING:START 缺失括号") is None


class TestStripCookingTools:
    def test_remove_markers_keep_text(self):
        result = strip_cooking_tools("好的[COOKING:START]，我们开始吧")
        assert "COOKING" not in result and "好的" in result and "我们开始吧" in result

    def test_multiple_markers(self):
        result = strip_cooking_tools("[COOKING:SELECT:番茄炒蛋][COOKING:START]")
        assert result.strip() == ""

    def test_no_marker(self):
        assert strip_cooking_tools("普通回复") == "普通回复"

    def test_empty(self):
        assert strip_cooking_tools("") == ""

    def test_none_input(self):
        # 修复#5：None 防护
        assert strip_cooking_tools(None) == ""


# ============ 调度 ============

class TestDispatchCookingTool:
    """用假 cooking handlers 验证 dispatch 分发到对应 control_* 函数"""

    @pytest.fixture
    def fake_handlers(self, monkeypatch):
        calls = []

        async def _start(runtime, ws, state, payload):
            calls.append(("control_start_cooking", payload))

        async def _select(runtime, ws, state, payload):
            calls.append(("control_select_recipe", payload))

        async def _confirm(runtime, ws, state, payload):
            calls.append(("control_confirm_step", payload))

        async def _skip(runtime, ws, state, payload):
            calls.append(("control_skip_step", payload))

        async def _stop(runtime, ws, state, payload):
            calls.append(("control_stop_cooking", payload))

        async def _send_text(runtime, ws, state, text, **kw):
            calls.append(("send_text", text))

        class Fake:
            control_start_cooking = staticmethod(_start)
            control_select_recipe = staticmethod(_select)
            control_confirm_step = staticmethod(_confirm)
            control_skip_step = staticmethod(_skip)
            control_stop_cooking = staticmethod(_stop)
            send_cooking_text_with_tts = staticmethod(_send_text)

        import app.ws_handlers.cooking as cooking_handlers
        monkeypatch.setattr(cooking_handlers, "control_start_cooking", _start)
        monkeypatch.setattr(cooking_handlers, "control_select_recipe", _select)
        monkeypatch.setattr(cooking_handlers, "control_confirm_step", _confirm)
        monkeypatch.setattr(cooking_handlers, "control_skip_step", _skip)
        monkeypatch.setattr(cooking_handlers, "control_stop_cooking", _stop)
        return calls

    @pytest.fixture
    def fresh_handlers(self, monkeypatch):
        """SELECT 未命中路径：验证 dispatch 改走 cooking_core 正式出口"""
        calls = []

        async def _send_text(runtime, ws, state, text, **kw):
            calls.append(("send_text", text))

        import app.cooking_core as cc
        monkeypatch.setattr(cc, "send_cooking_text_with_tts", _send_text, raising=True)
        return calls

    async def test_dispatch_start(self, fake_handlers):
        handled = await dispatch_cooking_tool(None, None, None, {"action": "START"})
        assert handled is True
        assert fake_handlers[-1][0] == "control_start_cooking"

    async def test_dispatch_select_existing_recipe(self, fake_handlers):
        handled = await dispatch_cooking_tool(None, None, None, {"action": "SELECT", "param": "番茄炒蛋"})
        assert handled is True
        name, payload = fake_handlers[-1]
        assert name == "control_select_recipe"
        assert payload["recipe_name"] == "番茄炒蛋"

    async def test_dispatch_select_missing_recipe(self, fresh_handlers):
        # 修复#6：SELECT 未命中走 cooking_core.send_cooking_text_with_tts，返回提示而非 AttributeError
        handled = await dispatch_cooking_tool(None, None, None, {"action": "SELECT", "param": "云朵泡泡汤"})
        assert handled is True
        assert fresh_handlers and fresh_handlers[-1][0] == "send_text"
        assert "没找到" in fresh_handlers[-1][1]

    async def test_dispatch_select_missing_recipe_no_crash(self, monkeypatch):
        """修复#6 回归：不挂任何假件时 SELECT 未命中也不抛异常（曾 AttributeError 被静默吞）"""
        import app.cooking_core as cc
        sent = []

        async def _send_text(runtime, ws, state, text, **kw):
            sent.append(text)

        monkeypatch.setattr(cc, "send_cooking_text_with_tts", _send_text)
        handled = await dispatch_cooking_tool(None, None, None, {"action": "SELECT", "param": "云朵泡泡汤"})
        assert handled is True
        assert sent and "没找到" in sent[0]

    async def test_dispatch_select_empty_param_falls_back_start(self, fake_handlers):
        handled = await dispatch_cooking_tool(None, None, None, {"action": "SELECT", "param": ""})
        assert handled is True
        assert fake_handlers[-1][0] == "control_start_cooking"

    async def test_dispatch_confirm(self, fake_handlers):
        await dispatch_cooking_tool(None, None, None, {"action": "CONFIRM_STEP"})
        assert fake_handlers[-1][0] == "control_confirm_step"

    async def test_dispatch_skip(self, fake_handlers):
        await dispatch_cooking_tool(None, None, None, {"action": "SKIP_STEP"})
        assert fake_handlers[-1][0] == "control_skip_step"

    async def test_dispatch_exit(self, fake_handlers):
        await dispatch_cooking_tool(None, None, None, {"action": "EXIT"})
        assert fake_handlers[-1][0] == "control_stop_cooking"

    async def test_unknown_action(self, monkeypatch):
        handled = await dispatch_cooking_tool(None, None, None, {"action": "FOOBAR"})
        assert handled is False

    async def test_case_insensitive_action(self, fake_handlers):
        await dispatch_cooking_tool(None, None, None, {"action": "confirm_step"})
        assert fake_handlers[-1][0] == "control_confirm_step"