# -*- coding: utf-8 -*-
"""
决定性查证测试：GLM（智谱）供应商下，多轮对话的上下文（历史消息）是否真的随请求
发送给了模型。

策略：mock 掉 UnifiedOpenAIProvider 的 HTTP 层（get_http_client），捕获实际发往
open.bigmodel.cn 的请求体 payload["messages"]，断言第二轮请求里确实包含第一轮的
user/assistant 消息。分别覆盖：
  1. 纯文本路径（stream_chat → _text_messages）
  2. 带图路径（stream_chat_with_image → 视觉 messages）
  3. cooking_core._collect_llm_text 路径（当前是否带上下文）
"""
import asyncio
import json
from typing import Any

import pytest

from app.context_engine import ContextEngine
from app.providers.registry import PROVIDER_REGISTRY
from app.providers import unified


# ===== HTTP 层 mock =====

class _FakeStreamResponse:
    """模拟 httpx 的 stream() 返回的异步上下文管理器 + aiter_text。"""

    def __init__(self, sse_chunks: list[str]):
        self._chunks = sse_chunks
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b""


class _FakeStreamCtxManager:
    """client.stream(...) 返回的异步上下文管理器工厂。"""

    def __init__(self, payload_capture: list[dict], sse_chunks: list[str]):
        self._capture = payload_capture
        self._chunks = sse_chunks

    def __call__(self, method: str, url: str, *, headers=None, json=None):
        # 捕获实际请求体
        self._capture.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse(self._chunks)


class _FakeHttpClient:
    """假 httpx.AsyncClient：只实现 stream()，捕获 payload。"""

    def __init__(self):
        self.captured_payloads: list[dict] = []
        self._sse_chunks = self._make_chunks("好的，我是AI助手。")

    @staticmethod
    def _make_chunks(text: str) -> list[str]:
        """构造一条 SSE 流式响应 chunk。"""
        delta = {"choices": [{"delta": {"content": text}}]}
        return [f"data: {json.dumps(delta, ensure_ascii=False)}\n", "data: [DONE]\n"]

    def stream(self, method: str, url: str, *, headers=None, json=None):
        self.captured_payloads.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse(self._sse_chunks)

    async def post(self, url: str, *, headers=None, json=None):
        raise AssertionError("测试不应走到非流式 post")


def _make_zhipu_provider(monkeypatch) -> tuple[unified.UnifiedOpenAIProvider, _FakeHttpClient]:
    """构造一个 zhipu spec 的 UnifiedOpenAIProvider，HTTP 层已 mock。"""
    spec = PROVIDER_REGISTRY["zhipu"]
    fake_client = _FakeHttpClient()
    monkeypatch.setattr(unified, "get_http_client", lambda: fake_client)
    provider = unified.UnifiedOpenAIProvider(
        spec=spec,
        api_key="test-key-not-real",
        model="glm-4.6v-flash",
    )
    return provider, fake_client


def _messages_summary(payload: dict) -> list[dict]:
    """从捕获的 payload 里提取 messages 数组，每条只保留 role + content 摘要。"""
    msgs = payload.get("json", {}).get("messages", [])
    summary = []
    for m in msgs:
        content = m.get("content")
        if isinstance(content, list):
            # 带图 content：提取 text 项
            texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            content_repr = "[image+text] " + " | ".join(texts)[:80]
        else:
            content_repr = str(content)[:120]
        summary.append({"role": m.get("role"), "content_preview": content_repr})
    return summary


# ===== 用例 =====

class TestGLMContextHistory:
    """查证：GLM 供应商多轮对话上下文是否真实下发。"""

    def test_text_path_multi_turn_history_sent(self, monkeypatch):
        """纯文本路径：第二轮请求的 messages 必须包含第一轮 user/assistant。"""
        provider, fake_client = _make_zhipu_provider(monkeypatch)
        ctx = ContextEngine()
        session_id = "test-glm-text"

        # 第一轮：用户提问 → 记录 → build_history（应为空，因为还没 assistant 回复）
        ctx.record_user_message(session_id, content="你好，我是小明", scene_type="general")
        history_t1 = ctx.build_history(session_id, max_tokens=2000, scene_type="general")
        assert history_t1 == [], f"第一轮 build_history 应为空（pending 未配对），实际: {history_t1}"

        async def _turn1():
            chunks = []
            async for delta, _ in provider.stream_chat(
                "你好，我是小明",
                system_prompt="你是烹饪助手",
                history=history_t1,
            ):
                if delta:
                    chunks.append(delta)
            return "".join(chunks)

        reply1 = asyncio.run(_turn1())
        assert reply1, "第一轮应返回内容"
        ctx.record_assistant_message(session_id, content=reply1)

        # 第二轮：用户用指代语"你叫什么"——此时 build_history 应包含第一轮完整 turn
        ctx.record_user_message(session_id, content="你刚才叫什么名字", scene_type="general")
        history_t2 = ctx.build_history(session_id, max_tokens=2000, scene_type="general")
        assert len(history_t2) >= 2, (
            f"第二轮 build_history 应含第一轮 user+assistant，实际只有 {len(history_t2)} 条: "
            f"{_messages_summary({'json': {'messages': history_t2}})}"
        )
        assert history_t2[0]["role"] == "user"
        assert "小明" in history_t2[0]["content"]
        assert history_t2[1]["role"] == "assistant"

        async def _turn2():
            chunks = []
            async for delta, _ in provider.stream_chat(
                "你刚才叫什么名字",
                system_prompt="你是烹饪助手",
                history=history_t2,
            ):
                if delta:
                    chunks.append(delta)
            return "".join(chunks)

        reply2 = asyncio.run(_turn2())

        # 捕获第二轮请求 payload
        assert len(fake_client.captured_payloads) >= 2, (
            f"应至少捕获 2 次请求，实际 {len(fake_client.captured_payloads)}"
        )
        payload_t2 = fake_client.captured_payloads[-1]
        msgs_t2 = payload_t2["json"]["messages"]

        # 断言：第二轮 messages 含 system + 第一轮 user + 第一轮 assistant + 第二轮 user
        roles = [m["role"] for m in msgs_t2]
        assert "system" in roles, f"缺少 system 消息: {roles}"
        assert roles.count("user") >= 2, f"应含 2 条 user（第一轮+第二轮），roles={roles}"
        assert "assistant" in roles, f"缺少第一轮 assistant 回复，roles={roles}"

        # 断言：第一轮 user 消息确实在第二轮请求里
        user_contents = [m["content"] for m in msgs_t2 if m["role"] == "user"]
        assert any("小明" in c for c in user_contents), (
            f"第二轮请求未含第一轮 user 消息（'小明'），user 消息: {user_contents}"
        )
        # 断言：第一轮 assistant 回复在第二轮请求里
        assistant_contents = [m["content"] for m in msgs_t2 if m["role"] == "assistant"]
        assert len(assistant_contents) >= 1, (
            f"第二轮请求未含第一轮 assistant 回复: {assistant_contents}"
        )

        # 验证请求 URL 指向智谱端点
        assert "bigmodel.cn" in payload_t2["url"], f"URL 不指向智谱: {payload_t2['url']}"

    def test_vision_path_multi_turn_history_sent(self, monkeypatch):
        """带图路径：第二轮带图请求的 messages 必须包含第一轮 user/assistant。"""
        provider, fake_client = _make_zhipu_provider(monkeypatch)
        ctx = ContextEngine()
        session_id = "test-glm-vision"

        # 第一轮：用户带图提问
        ctx.record_user_message(session_id, content="锅里有什么", scene_type="vision")
        history_t1 = ctx.build_history(session_id, max_tokens=2000, scene_type="vision")
        assert history_t1 == []

        async def _turn1():
            chunks = []
            async for delta, _ in provider.stream_chat_with_image(
                "锅里有什么",
                jpeg_bytes=b"\xff\xd8\xff\xe0fake-jpeg",
                system_prompt="你是视觉助手",
                history=history_t1,
            ):
                if delta:
                    chunks.append(delta)
            return "".join(chunks)

        reply1 = asyncio.run(_turn1())
        ctx.record_assistant_message(session_id, content=reply1)

        # 第二轮：用户追问带图
        ctx.record_user_message(session_id, content="那它熟了吗", scene_type="vision")
        history_t2 = ctx.build_history(session_id, max_tokens=2000, scene_type="vision")
        assert len(history_t2) >= 2

        async def _turn2():
            chunks = []
            async for delta, _ in provider.stream_chat_with_image(
                "那它熟了吗",
                jpeg_bytes=b"\xff\xd8\xff\xe0fake-jpeg-2",
                system_prompt="你是视觉助手",
                history=history_t2,
            ):
                if delta:
                    chunks.append(delta)
            return "".join(chunks)

        asyncio.run(_turn2())

        payload_t2 = fake_client.captured_payloads[-1]
        msgs_t2 = payload_t2["json"]["messages"]
        roles = [m["role"] for m in msgs_t2]

        assert "assistant" in roles, f"视觉第二轮请求缺第一轮 assistant: {roles}"
        user_contents = [m["content"] for m in msgs_t2 if m["role"] == "user"]
        assert any("锅里有什么" in str(c) for c in user_contents), (
            f"视觉第二轮请求缺第一轮 user 文本: {_messages_summary(payload_t2)}"
        )

    def test_collect_llm_text_no_history_by_design(self, monkeypatch):
        """cooking_core._collect_llm_text 路径：当前不带上下文（设计如此，记录现状）。

        该路径用于菜谱生成 / 份数计算 / 意图分类，均为一次性结构化任务，
        不依赖对话历史。此测试记录现状而非断言 bug。
        """
        provider, fake_client = _make_zhipu_provider(monkeypatch)

        # _collect_llm_text 用的是 runtime.llm（即 provider 本身）
        runtime = type("R", (), {"llm": provider, "vlm": provider})()

        from app import cooking_core

        async def _call():
            return await cooking_core._collect_llm_text(
                runtime,
                "请生成一道番茄炒蛋的菜谱JSON",
                system_prompt="你是菜谱生成器",
            )

        asyncio.run(_call())

        payload = fake_client.captured_payloads[-1]
        msgs = payload["json"]["messages"]
        roles = [m["role"] for m in msgs]

        # 现状记录：_collect_llm_text 只发 system + user，没有 history
        assert roles == ["system", "user"], (
            f"_collect_llm_text 应只发 system+user（无 history），实际 roles={roles}"
        )
