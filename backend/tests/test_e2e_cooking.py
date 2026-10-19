# -*- coding: utf-8 -*-
"""
E2E 测试：连真实后端（默认 127.0.0.1:8010），驱动 cooking 全流程
- 使用隔离 session_id=qa_e2e_<ts>，不污染正式会话
- hello 事件把 TTS 覆盖为 browser，避免测试期间真实外呼小米 TTS
- 依赖：后端正在运行；若不可达则整组 skip
"""
import asyncio
import json
import os
import time
import uuid

import pytest
import httpx
import websockets

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8010"))
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
WS_URL = f"ws://127.0.0.1:{BACKEND_PORT}/ws"


def backend_alive() -> bool:
    # trust_env=False：环境里的 HTTP_PROXY 会把 127.0.0.1 也发给代理导致超时误判
    # "后端未运行"而整组 skip（2026-09-11 实测），与后端 get_http_client 同款直连
    try:
        r = httpx.get(f"{BACKEND_URL}/health", timeout=3, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not backend_alive(), reason="后端 8010 未运行，跳过 E2E")


class E2EClient:
    """简易 WS 客户端：收发 JSON 消息并采集事件流"""

    def __init__(self, session_id: str):
        self.url = f"{WS_URL}?session_id={session_id}"
        self.events: list[dict] = []

    async def __aenter__(self):
        self._ws = await websockets.connect(self.url, open_timeout=10)
        return self

    async def __aexit__(self, *exc):
        try:
            await self._ws.close()
        except Exception:
            pass

    async def _drain(self, seconds: float = 0.8):
        """尽量收齐当前窗口内的事件"""
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=0.15)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
            try:
                data = __import__("json").loads(msg)
                self.events.append(data)
            except Exception:
                pass

    async def send(self, typ: str, payload: dict):
        await self._ws.send(__import__("json").dumps({"type": typ, "payload": payload}))
        await self._drain(1.0)

    def texts(self):
        out = []
        for ev in self.events:
            payload = ev.get("payload", {}) or {}
            if ev.get("type") == "assistant_delta":
                out.append(payload.get("delta", ""))
            elif ev.get("type") in ("assistant_final", "tts_announce"):
                out.append(payload.get("text", ""))
        return "".join(out)

    def last_cooking_status(self):
        for ev in reversed(self.events):
            if ev.get("type") == "cooking_status":
                return ev.get("payload", {}) or {}
        return None


@pytest.fixture
async def client():
    session_id = f"qa_e2e_{uuid.uuid4().hex[:12]}"
    async with E2EClient(session_id) as c:
        # 覆盖 TTS 为 browser，防止真实外呼
        await c.send("hello", {"tts_provider": "browser", "tts_model": "browser"})
        yield c


# ---------- 用例 ----------

class TestE2ECooking:
    async def _start_and_select(self, client, recipe_name="番茄炒蛋"):
        await client.send("cooking", {"action": "start"})
        await client.send("cooking", {"action": "select_recipe", "recipe_name": recipe_name})

    async def test_health_and_connect(self, client):
        assert backend_alive() is True
        assert len(client.events) >= 0  # 已成功连接（未抛异常即通过）

    async def test_start_cooking(self, client):
        await client.send("cooking", {"action": "start"})
        status = client.last_cooking_status()
        assert status is not None
        assert status.get("enabled") is True
        assert status.get("state") == "COLLECTING_INGREDIENTS"
        assert "步骤式" in client.texts()

    async def test_select_recipe(self, client):
        await self._start_and_select(client)
        status = client.last_cooking_status()
        # 新流程：选中菜谱直接进入 COOKING 第 1 步（不再询问几人份）
        assert status.get("state") == "COOKING"
        assert status.get("step") == 1
        assert status.get("recipe", {}).get("name") == "番茄炒蛋"

    async def test_direct_start_step1(self, client):
        """2026-09-06：份数问答/智能计算食材已移除，选菜即开做第 1 步"""
        await self._start_and_select(client)
        status = client.last_cooking_status()
        assert status.get("state") == "COOKING"
        assert status.get("step") == 1
        assert status.get("servings") in (None, 0)

    async def test_confirm_and_skip(self, client):
        await self._start_and_select(client)
        assert client.last_cooking_status().get("step") == 1
        await client.send("user_text", {"text": "好了"})
        assert client.last_cooking_status().get("step") == 2
        await client.send("user_text", {"text": "好了"})
        assert client.last_cooking_status().get("step") == 3

    async def test_switch_dish(self, client):
        await self._start_and_select(client)
        await client.send("user_text", {"text": "我要做红烧肉"})
        status = client.last_cooking_status()
        assert status.get("recipe", {}).get("name") == "红烧肉"
        assert status.get("state") == "COOKING"
        assert status.get("step") == 1

    async def test_stop_cooking(self, client):
        await client.send("cooking", {"action": "start"})
        await client.send("cooking", {"action": "stop"})
        status = client.last_cooking_status()
        assert status.get("enabled") is False
        assert status.get("state") == "IDLE"

    @pytest.mark.skipif(
        os.environ.get("E2E_NEW_CODE") != "1",
        reason="该用例验证写死入口+远程生成兜底（缺陷#9/#10 修复），需连接新代码后端实例",
    )
    async def test_stepwise_synonym_direct_enters(self, client):
        """修复9 真实链路：用户说「一步一步教我做番茄炒鸡蛋」→ 写死入口直接进引导，
        本地无此名时远程生成菜谱进入准备流程（不重新询问菜名、不输出 cooking 标记）。
        注：依赖真实 LLM 生成，结果可能因模型不同略有差异，做宽松断言。"""
        await client.send("user_text", {"text": "一步一步教我做番茄炒鸡蛋"})
        await client._drain(30)
        status = client.last_cooking_status()
        assert status is not None, "应已进入做饭引导（收到 cooking_status）"
        assert status.get("enabled") is True, "写死入口应命中，直接进入引导"
        # 展示文本不得出现 cooking 工具标记泄漏
        assert "COOKING" not in client.texts()
        # 若已解析出菜谱（远程生成成功）→ 已直接开始第 1 步且不再询问菜名
        if status.get("state") == "COOKING":
            assert "有什么想做的" not in client.texts()