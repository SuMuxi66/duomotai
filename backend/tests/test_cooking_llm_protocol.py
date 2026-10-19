# -*- coding: utf-8 -*-
"""
协议链路测试：LLM 输出 [COOKING:...] 标记 → llm_handler 剥离 → dispatch → 预设状态机接管
（昨晚工具化改造的核心链路，用 mock LLM 模拟输出，验证端到端不泄漏标记）
"""
import asyncio
import sys
from types import SimpleNamespace

import pytest
import websockets  # noqa: F401 确保 ws 依赖类型可用
from starlette.websockets import WebSocketState

from app import prompts as app_prompts
from app.ws import llm_handler
from app import cooking_core, cooking_tools


# ---------- 假件 ----------

class MockLLM:
    """假 LLM：stream_chat 输出预设文本"""

    def __init__(self, output: str):
        self._output = output

    async def stream_chat(self, prompt, **kwargs):
        yield self._output


class FakeWS:
    """假 WS：满足 ws_send 的 client_state + send_json"""

    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []

    async def send_json(self, data: dict):
        self.sent.append(data)

    def deltas(self):
        return [
            p.get("delta", "") for m in self.sent
            if m.get("type") == "assistant_delta"
            for p in [m.get("payload", {})]
        ]

    def finals(self):
        return [
            p.get("text", "") for m in self.sent
            if m.get("type") == "assistant_final"
            for p in [m.get("payload", {})]
        ]


def _make_state():
    st = SimpleNamespace(
        interrupted=False,
        llm_temperature=0.7,
        llm_max_tokens=1024,
        llm_top_p=0.9,
        llm_sample_count=1,
        llm_frequency_penalty=0.0,
        llm_response_format="md",
        llm_enable_thinking=False,
        vision_observations=[],
        history=[],
        cooking_enabled=False,
        current_task=None,
        ai_echo_shield=False,
    )

    def _noop(*a, **k):
        pass

    st.add_observation = _noop
    return st


class MockRuntime:
    def __init__(self, output: str):
        # LLM/VLM 已统一到 VLM 端点：stream_llm_response 走 runtime.vlm
        self.vlm = MockLLM(output)
        self.llm = self.vlm  # 别名保持兼容
        self.tts = None


@pytest.fixture
def tracing_handlers(monkeypatch):
    """记录 dispatch 是否把 SELECT/START 交给预设控制函数"""
    calls = []

    async def _select(runtime, ws, state, payload):
        calls.append(("select", payload.get("recipe_name")))

    async def _start(runtime, ws, state, payload):
        calls.append(("start", None))

    async def _send_text(runtime, ws, state, text, **kw):
        calls.append(("send_text", text))

    import app.ws_handlers.cooking as ch
    monkeypatch.setattr(ch, "control_select_recipe", _select)
    monkeypatch.setattr(ch, "control_start_cooking", _start)
    # 缺陷#6：ws_handlers.cooking 无此属性，dispatch 未命中分支会 AttributeError；此处挂上以测成功路径
    monkeypatch.setattr(ch, "send_cooking_text_with_tts", _send_text, raising=False)
    # dispatch 内部 `from .ws_handlers import cooking` 取到的同一模块对象
    return calls


async def _run_llm(output: str, ws: FakeWS, monkeypatch, state=None):
    runtime = MockRuntime(output)
    state = state or _make_state()
    final, multimodal, tts_started = await llm_handler.stream_llm_response(
        runtime, ws, state, "用户问题", "用户问题", "", None, None, None,
        request_id="qa-req", tts_relay=None,
        system_prompt="<system>", history=None, file_b64=None,
    )
    return final, ws


# ---------- 用例 ----------

class TestPromptInjection:
    def test_cooking_context_injects_tool_protocol(self):
        _, sys_prompt = app_prompts.build_chat_prompt(
            "教我做番茄炒蛋", [], temperature=None, weight=None, cooking_context=True)
        assert "[COOKING:" in sys_prompt
        assert "COOKING:SELECT" in sys_prompt
        assert "不要自行展开完整菜谱" in sys_prompt

    def test_non_cooking_context_no_injection(self):
        _, sys_prompt = app_prompts.build_chat_prompt(
            "今天天气不错", [], temperature=None, weight=None, cooking_context=False)
        assert "[COOKING:" not in sys_prompt


class TestMarkerDrivesPreset:
    async def test_select_marker_skips_llm_text_and_drives_preset(self, monkeypatch, tracing_handlers):
        """LLM 输出带 [COOKING:SELECT] → LLM 文本全部静默（含过渡语），SELECT 交给状态机
        （双重回复修复：检出交接标记后，模型自然语言不展示/不播报，用户只收到系统预设引导）"""
        ws = FakeWS()
        final, _ = await _run_llm("好的，我们做这道菜吧[COOKING:SELECT:番茄炒蛋]", ws, monkeypatch)
        assert final == ""  # 预设已接管，不再重复 TTS/入历史
        # assistant_final 为空文本，不推送 LLM 过渡语
        assert all(f == "" for f in ws.finals())
        assert all("COOKING" not in f for f in ws.finals())
        # 检出标记后文本静默：无任何 delta 外发
        assert ws.deltas() == []
        assert tracing_handlers and tracing_handlers[0][0] == "select"
        assert tracing_handlers[0][1] == "番茄炒蛋"

    async def test_start_marker_drives_start(self, monkeypatch, tracing_handlers):
        ws = FakeWS()
        final, _ = await _run_llm("好的，那我们开始吧[COOKING:START]", ws, monkeypatch)
        assert final == ""
        assert tracing_handlers and tracing_handlers[0][0] == "start"

    async def test_plain_output_untouched(self, monkeypatch, tracing_handlers):
        ws = FakeWS()
        final, _ = await _run_llm("今天天气不错，适合做菜", ws, monkeypatch)
        assert final == "今天天气不错，适合做菜"
        assert tracing_handlers == []  # 无标记不调度
        assert any("今天天气不错" in f for f in ws.finals())

    async def test_streaming_marker_stripped_per_chunk(self, monkeypatch, tracing_handlers):
        """修复#7：标记跨流式 chunk 拆分时，闭合前的半截标记滞留缓冲，不泄漏到 assistant_delta；
        双重回复修复：标记闭合后剩余文本（「马上开始」）静默，不再外发"""
        ws = FakeWS()

        class ChunkLLM:
            async def stream_chat(self, prompt, **kwargs):
                for piece in ["请稍等[COOKING:", "SELECT:青椒炒", "肉]马上开始"]:
                    yield piece

        runtime = SimpleNamespace(vlm=ChunkLLM(), tts=None)
        state = _make_state()
        final, _, _ = await llm_handler.stream_llm_response(
            runtime, ws, state, "q", "q", "", None, None, None,
            request_id="qa-req", tts_relay=None, system_prompt="<s>", history=None, file_b64=None)
        assert final == ""
        # 任何 delta 都不含标记协议特征
        assert all(("COOKING" not in d and "SELECT:" not in d) for d in ws.deltas())
        # 标记闭合前的「请稍等」已放行；闭合后的「马上开始」被静默
        assert "".join(ws.deltas()) == "请稍等"
        assert tracing_handlers and tracing_handlers[0][0] == "select"

    async def test_marker_at_final_only(self, monkeypatch, tracing_handlers):
        """标记只在 final 段出现时也能剥离并调度（闭合整块场景，应通过）"""
        ws = FakeWS()

        class FinalOnlyLLM:
            async def stream_chat(self, prompt, **kwargs):
                yield "好的，那就它了"
                yield "[COOKING:SELECT:红烧肉]"

        runtime = SimpleNamespace(vlm=FinalOnlyLLM(), tts=None)
        state = _make_state()
        final, _, _ = await llm_handler.stream_llm_response(
            runtime, ws, state, "q", "q", "", None, None, None,
            request_id="qa-req", tts_relay=None, system_prompt="<s>", history=None, file_b64=None)
        assert final == ""
        assert all("COOKING" not in d for d in ws.deltas())
        assert tracing_handlers and tracing_handlers[0][0] == "select"

    async def test_marker_at_final_only_dispatch_name(self, monkeypatch, tracing_handlers):
        """final 整块闭合标记 → 调度 SELECT 名称正确（通过路径）"""
        ws = FakeWS()

        class FinalOnlyLLM2:
            async def stream_chat(self, prompt, **kwargs):
                yield "[COOKING:SELECT:红烧肉]"

        runtime = SimpleNamespace(vlm=FinalOnlyLLM2(), tts=None)
        state = _make_state()
        await llm_handler.stream_llm_response(
            runtime, ws, state, "q", "q", "", None, None, None,
            request_id="qa-req", tts_relay=None, system_prompt="<s>", history=None, file_b64=None)
        assert tracing_handlers and tracing_handlers[0] == ("select", "红烧肉")