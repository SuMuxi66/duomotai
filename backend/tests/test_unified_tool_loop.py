# -*- coding: utf-8 -*-
"""UnifiedOpenAIProvider 工具循环（_stream_with_tool_loop）。

2026-09-11 连续对话改造：模型"过程播报正文 + 同轮 tool_calls"时也要执行工具并
把播报回填 assistant.content，工具结果回填后续答实际结果——旧行为只要正文已交付
就丢弃 tool_calls，造成"正在为您查询风扇状态。"之后再无下文。

同日事故回归：DeepSeek 流式轮偶发空完成（无正文无 tool_calls），旧代码无兜底时
任务无痕死亡（用户请求 26s 零产出零日志）。DeepSeek 现已开启
stream_fallback_non_stream，兜底拿到 tool_calls 也要能进入工具循环。
"""
import json

import pytest

from app.providers import unified as unified_module
from app.providers.registry import PROVIDER_REGISTRY
from app.providers.unified import UnifiedOpenAIProvider

TOOLS = [{"type": "function", "function": {"name": "get_fan_status", "parameters": {"type": "object", "properties": {}}}}]


def _make_provider(rounds, captured_messages):
    """构造 provider 并把 _stream_messages 替换为按脚本回放的假流。

    rounds: 每轮脚本 (deltas, tool_calls)——deltas 为该轮正文分片列表，
    tool_calls 为该轮结束后写入 tool_calls_out 的调用列表。
    """
    provider = UnifiedOpenAIProvider(api_key="test-key", model="test-model", base_url="http://fake.local/v1")
    state = {"round": 0}

    async def _fake_stream_messages(self, messages, **kwargs):
        idx = state["round"]
        state["round"] += 1
        captured_messages.append([dict(m) for m in messages])
        deltas, tool_calls = rounds[min(idx, len(rounds) - 1)]
        if idx >= len(rounds):
            raise AssertionError("脚本轮数耗尽后仍被请求新一轮")
        for d in deltas:
            yield d, ""
        tc_out = kwargs.get("tool_calls_out")
        if tc_out is not None:
            tc_out.extend(tool_calls)

    provider._stream_messages = _fake_stream_messages.__get__(provider)
    return provider


@pytest.mark.asyncio
async def test_narration_with_tool_call_still_executes_and_answers():
    """播报正文 + tool_calls 同轮：工具必须执行，二轮续答实际状态（连续对话）。"""
    captured_messages = []
    provider = _make_provider(
        [
            (["正在为您查询", "风扇状态。"], [{"id": "call_1", "name": "get_fan_status", "arguments": "{}"}]),
            (["排气扇当前", "已开启。"], []),
        ],
        captured_messages,
    )
    executed = []

    async def executor(name, arguments):
        executed.append((name, arguments))
        return json.dumps({"is_on": True, "message": "风扇已开启"}, ensure_ascii=False)

    out = []
    async for delta, _reasoning in provider.stream_chat("排气上的状态是什么", tools=TOOLS, tool_executor=executor):
        out.append(delta)

    assert "".join(out) == "正在为您查询风扇状态。排气扇当前已开启。"
    assert executed == [("get_fan_status", "{}")]

    # 第二轮请求应带上 assistant(tool_calls, content=播报) 与 tool 结果两条消息
    second = captured_messages[1]
    assistant_msg = [m for m in second if m.get("role") == "assistant"][-1]
    assert assistant_msg["content"] == "正在为您查询风扇状态。"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_fan_status"
    tool_msg = [m for m in second if m.get("role") == "tool"][-1]
    assert json.loads(tool_msg["content"])["is_on"] is True
    assert tool_msg["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_pure_tool_calls_path_unchanged():
    """纯 tool_calls（无正文）→ 执行工具 → 二轮作答（原有路径回归）。"""
    captured_messages = []
    provider = _make_provider(
        [
            ([], [{"id": "abc", "name": "get_fan_status", "arguments": "{}"}]),
            (["风扇没开。"], []),
        ],
        captured_messages,
    )

    async def executor(name, arguments):
        return json.dumps({"is_on": False})

    out = []
    async for delta, _r in provider.stream_chat("风扇开着吗", tools=TOOLS, tool_executor=executor):
        out.append(delta)
    assert "".join(out) == "风扇没开。"
    # 无正文轮回填的 assistant.content 为空串
    assistant_msg = [m for m in captured_messages[1] if m.get("role") == "assistant"][-1]
    assert assistant_msg["content"] == ""


@pytest.mark.asyncio
async def test_plain_text_no_tool_execution():
    """无 tool_calls 的普通回答：执行器不被调用，文本原样透传。"""
    captured_messages = []
    provider = _make_provider([(["今天适合做饭。"], [])], captured_messages)
    executed = []

    async def executor(name, arguments):
        executed.append(name)

    out = []
    async for delta, _r in provider.stream_chat("你好", tools=TOOLS, tool_executor=executor):
        out.append(delta)
    assert "".join(out) == "今天适合做饭。"
    assert executed == []


@pytest.mark.asyncio
async def test_tool_rounds_bounded():
    """每轮都播报+调工具：达到 max_tool_rounds 后丢弃后续 tool_calls，不再无限循环。"""
    captured_messages = []
    loop_call = [{"id": "x", "name": "get_fan_status", "arguments": "{}"}]
    provider = _make_provider(
        [
            (["一。"], list(loop_call)),
            (["二。"], list(loop_call)),
            (["三。"], list(loop_call)),
        ],
        captured_messages,
    )
    count = {"n": 0}

    async def executor(name, arguments):
        count["n"] += 1
        return "{}"

    out = []
    async for delta, _r in provider.stream_chat("循环", tools=TOOLS, tool_executor=executor, max_tool_rounds=2):
        out.append(delta)
    assert "".join(out) == "一。二。三。"
    assert count["n"] == 2


# ===== 2026-09-11 事故回归：DeepSeek 流式空完成 → 非流式兜底 → 工具循环 =====

_SSE_ANSWER = 'data: {"choices":[{"delta":{"content":"风扇已打开。"}}]}\n\ndata: [DONE]\n\n'
_NON_STREAM_TOOL_CALLS = {
    "choices": [{
        "message": {
            "content": None,
            "tool_calls": [{
                "id": "call_fb",
                "type": "function",
                "function": {"name": "get_fan_status", "arguments": "{}"},
            }],
        }
    }]
}


class _FakeResponse:
    def __init__(self, sse_text="", json_data=None):
        self.status_code = 200
        self._sse = sse_text
        self._json = json_data or {}
        self.text = ""

    async def aiter_text(self):
        yield self._sse

    def json(self):
        return self._json


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """stream() 依次返回脚本里的 SSE 响应（耗尽后重复最后一个）；post() 返回兜底响应。"""

    def __init__(self, stream_responses, post_response):
        self._stream_responses = list(stream_responses)
        self.post_response = post_response
        self.stream_calls = 0
        self.post_calls = 0

    def stream(self, method, url, **kwargs):
        idx = min(self.stream_calls, len(self._stream_responses) - 1)
        self.stream_calls += 1
        return _FakeStreamCtx(self._stream_responses[idx])

    async def post(self, url, **kwargs):
        self.post_calls += 1
        return self.post_response


def test_deepseek_spec_enables_stream_fallback():
    """DeepSeek 必须开启流式空响应非流式兜底（2026-09-11 无痕死亡事故）。"""
    assert PROVIDER_REGISTRY["deepseek"].stream_fallback_non_stream is True


@pytest.mark.asyncio
async def test_empty_stream_fallback_with_tool_calls_enters_tool_loop(monkeypatch):
    """流式空完成 → 非流式兜底拿到 tool_calls → 执行工具 → 二轮续答，全程无痕丢任务。"""
    provider = UnifiedOpenAIProvider(
        PROVIDER_REGISTRY["deepseek"], api_key="test-key", model="deepseek-v4-flash",
    )
    fake_client = _FakeClient(
        stream_responses=[
            _FakeResponse(sse_text=""),          # 第一轮：流式空完成（事故形态）
            _FakeResponse(sse_text=_SSE_ANSWER),  # 第二轮：正常续答
        ],
        post_response=_FakeResponse(json_data=_NON_STREAM_TOOL_CALLS),
    )
    monkeypatch.setattr(unified_module, "get_http_client", lambda: fake_client)
    executed = []

    async def executor(name, arguments):
        executed.append((name, arguments))
        return json.dumps({"is_on": True}, ensure_ascii=False)

    out = []
    async for delta, _r in provider.stream_chat("帮我打开排气扇", tools=TOOLS, tool_executor=executor):
        out.append(delta)

    assert "".join(out) == "风扇已打开。"
    assert "请重试" not in "".join(out)
    assert executed == [("get_fan_status", "{}")]
    assert fake_client.post_calls == 1
