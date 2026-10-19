# -*- coding: utf-8 -*-
"""主对话 LLM 工具层：schema 合法性、执行器容错、dispense_start 跨轮确认门。"""
import json

import pytest

import app.llm_tools as llm_tools
from app.config import settings

_ALL_TOOLS = {
    "get_temperature", "get_weight", "weight_tare",
    "dispense_start", "dispense_stop", "dispense_status",
    "search_recipes", "get_recipe",
    "get_fan_status", "fan_on", "fan_off",
    "voice_continuous_start", "voice_continuous_stop", "voice_continuous_status",
}


@pytest.fixture(autouse=True)
def _no_real_mcp(monkeypatch):
    """硬性隔离：禁走真实 MCP 链路（防烧红线，2026-09-21 事故修复）。

    llm_tools 的 dispense_start/stop 默认经 mcp_client 常驻子进程 → mcp_server
    HTTP 回环 → **真实开阀**。本文件的确认门测试 mock 的是进程内直调层
    （app.dispense.*），MCP 不旁路则 mock 失效、测试真驱动电磁阀（当天全量
    pytest 期间前端观察到阀疯狂开关）。conftest 已设 MCP_DISPENSE_ENABLED=0，
    此处 monkeypatch 模块常量双保险（防 env 被外部覆盖）。
    """
    monkeypatch.setattr(llm_tools, "_MCP_DISPENSE_ENABLED", False)


def test_tool_schemas_well_formed():
    names = [s["function"]["name"] for s in llm_tools.TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    for schema in llm_tools.TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"


def test_all_tools_exposed():
    """全部工具（与 mcp_server.py 同构，2026-09-10 起 14 个）都进模型工具表。"""
    names = {s["function"]["name"] for s in llm_tools.TOOL_SCHEMAS}
    assert names == _ALL_TOOLS
    assert set(llm_tools._TOOL_SCHEMA_NAMES) == _ALL_TOOLS


def test_get_tool_setup_disabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_tools_enabled", False)
    assert llm_tools.get_tool_setup() == (None, None)


def test_get_tool_setup_enabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_tools_enabled", True)
    schemas, executor = llm_tools.get_tool_setup()
    assert schemas is llm_tools.TOOL_SCHEMAS
    assert callable(executor)


@pytest.mark.asyncio
async def test_execute_tool_unknown_name():
    result = await llm_tools.execute_tool("no_such_tool_xyz", "{}")
    assert "error" in json.loads(result)


@pytest.mark.asyncio
async def test_execute_tool_bad_arguments_no_crash():
    result = await llm_tools.execute_tool("get_temperature", "{不是json")
    data = json.loads(result)
    assert "error" not in data or isinstance(data, dict)  # 容错不抛


@pytest.mark.asyncio
async def test_execute_tool_get_temperature():
    result = json.loads(await llm_tools.execute_tool("get_temperature", "{}"))
    assert isinstance(result, dict)
    assert "maximum" in result  # 与 /temperature 端点同构


@pytest.mark.asyncio
async def test_execute_tool_get_recipe_not_found():
    result = json.loads(await llm_tools.execute_tool("get_recipe", '{"name": "不存在的菜名xyzq"}'))
    assert "error" in result


@pytest.mark.asyncio
async def test_execute_tool_search_recipes():
    result = json.loads(await llm_tools.execute_tool("search_recipes", '{"ingredients": "鸡蛋"}'))
    assert isinstance(result, dict)
    assert "recipes" in result or "error" in result


# ===== 写操作执行器（monkeypatch 硬件模块，不碰真设备） =====


@pytest.mark.asyncio
async def test_execute_tool_weight_tare(monkeypatch):
    monkeypatch.setattr("app.weight.tare_weight", lambda: {"success": True, "message": "去皮完成"})
    result = json.loads(await llm_tools.execute_tool("weight_tare", "{}"))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_tool_fan_toggle(monkeypatch):
    monkeypatch.setattr("app.fan.fan_on", lambda: {"success": True})
    monkeypatch.setattr("app.fan.fan_off", lambda: {"success": True})
    assert json.loads(await llm_tools.execute_tool("fan_on", "{}"))["success"] is True
    assert json.loads(await llm_tools.execute_tool("fan_off", "{}"))["success"] is True


@pytest.mark.asyncio
async def test_execute_tool_dispense_stop_and_status(monkeypatch):
    async def fake_stop():
        return {"success": True, "message": "下料已停止"}

    monkeypatch.setattr("app.dispense.stop_dispense", fake_stop)
    monkeypatch.setattr("app.dispense.get_dispense_status", lambda: {"active": False})
    assert json.loads(await llm_tools.execute_tool("dispense_stop", "{}"))["success"] is True
    assert json.loads(await llm_tools.execute_tool("dispense_status", "{}"))["active"] is False


# ===== dispense_start 跨轮确认门 =====


@pytest.mark.asyncio
async def test_dispense_start_gate_requires_cross_turn_confirmation(monkeypatch):
    """首次只预约；同轮重复不放行（防绕过）；下一轮同克数才执行。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    key = "gate-test-1"

    r1 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=1))
    assert r1["status"] == "confirmation_required"
    assert called == []

    # 同一轮工具循环里模型立即再调（试图绕过确认）——仍不放行
    r2 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=1))
    assert r2["status"] == "confirmation_required"
    assert called == []

    # 下一轮（用户已口头确认）→ 真正执行
    r3 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=2))
    assert r3["success"] is True
    assert called == [3]


@pytest.mark.asyncio
async def test_dispense_start_gate_gram_change_reconfirms(monkeypatch):
    """预约后克数变化 → 重新预约确认，不执行旧值。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    key = "gate-test-2"

    r1 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=1))
    assert r1["status"] == "confirmation_required"
    # 下一轮但克数变了 → 重新预约
    r2 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 5}', session_key=key, turn=2))
    assert r2["status"] == "confirmation_required"
    assert called == []
    # 再下一轮、与新克数一致 → 执行
    r3 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 5}', session_key=key, turn=3))
    assert r3["success"] is True
    assert called == [5]


@pytest.mark.asyncio
async def test_dispense_start_gate_target_range(monkeypatch):
    monkeypatch.setattr("app.dispense.start_dispense", lambda tg: {"success": True})
    for bad in ("0.1", "150", '"abc"'):
        args = '{"target_g": %s}' % bad
        result = json.loads(await llm_tools.execute_tool("dispense_start", args, session_key="gate-test-3", turn=1))
        assert result.get("error") or result.get("status") == "confirmation_required"
        assert result.get("success") is not True  # 任何情况都不能直接执行


@pytest.mark.asyncio
async def test_dispense_start_gate_expired(monkeypatch):
    """预约过期后需重新确认。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    key = "gate-test-4"

    r1 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=1))
    assert r1["status"] == "confirmation_required"
    # 把预约改成已过期
    llm_tools._DISPENSE_GATES[key]["expires_at"] = 0.0
    r2 = json.loads(await llm_tools.execute_tool("dispense_start", '{"target_g": 3}', session_key=key, turn=2))
    assert r2["status"] == "confirmation_required"
    assert called == []


# ===== dispense_start 用户确认词放行（模型口头征询过、用户本轮刚确认） =====


@pytest.mark.asyncio
async def test_dispense_start_user_confirmation_phrase_executes(monkeypatch):
    """用户本轮消息是确认词（对/好的/确认）：首次调用直接执行，不再二次确认。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    for phrase in ("对", "好的", "确认", "可以"):
        key = f"gate-text-{phrase}"
        r = json.loads(await llm_tools.execute_tool(
            "dispense_start", '{"target_g": 3}', session_key=key, turn=1, user_text=phrase))
        assert r["success"] is True, phrase
    assert called == [3, 3, 3, 3]


@pytest.mark.asyncio
async def test_dispense_start_non_confirmation_text_still_gated(monkeypatch):
    """普通消息/否定语义不放行。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    for text in ("我下料", "不对", "别放", "换成20克", "好的，顺便放点糖和酱油一起下去吧"):
        key = f"gate-text-neg-{text[:4]}"
        r = json.loads(await llm_tools.execute_tool(
            "dispense_start", '{"target_g": 3}', session_key=key, turn=1, user_text=text))
        assert r["status"] == "confirmation_required", text
    assert called == []


@pytest.mark.asyncio
async def test_dispense_start_no_double_exec_in_same_turn(monkeypatch):
    """同一轮工具循环里重复调用：第二次拒绝重复执行，防双份下料。"""
    called = []

    async def fake_start(target_g):
        called.append(target_g)
        return {"success": True, "target_g": target_g}

    monkeypatch.setattr("app.dispense.start_dispense", fake_start)
    key = "gate-text-dup"
    r1 = json.loads(await llm_tools.execute_tool(
        "dispense_start", '{"target_g": 3}', session_key=key, turn=1, user_text="对"))
    assert r1["success"] is True
    # 模型同一轮又调了一次（例如工具循环第二轮）——不得重复执行
    r2 = json.loads(await llm_tools.execute_tool(
        "dispense_start", '{"target_g": 3}', session_key=key, turn=1, user_text="对"))
    assert r2["status"] == "already_executed"
    assert called == [3]


def test_is_confirmation_phrase_rules():
    f = llm_tools._is_confirmation_phrase
    assert f("对") and f("好的") and f("确认") and f("ok")
    assert not f("我下料") and not f("不对") and not f("别放") and not f("换成20克")
    assert not f("") and not f("") and not f("好的，那顺便再介绍一下今天的菜谱推荐吧谢谢")


def test_get_tool_setup_binds_session_and_turn(monkeypatch):
    """get_tool_setup(state) 绑定 session_id，且每次调用（每个用户请求）轮次递增。"""

    class _FakeState:
        session_id = "setup-test"

    monkeypatch.setattr(settings, "llm_tools_enabled", True)
    s1, e1 = llm_tools.get_tool_setup(_FakeState())
    assert llm_tools._TURN_SEQ["setup-test"] == 1
    s2, e2 = llm_tools.get_tool_setup(_FakeState())
    assert llm_tools._TURN_SEQ["setup-test"] == 2
    assert e1 is not e2  # 每个请求一个绑定当前轮次的执行器
    assert s1 is s2 is llm_tools.TOOL_SCHEMAS


def test_looks_like_meta_question():
    """问句甄别：非做饭提问→True；食材清单/做饭语境→False（2026-09-11 工具问句误解析回归）"""
    from app.cooking_core import looks_like_meta_question

    assert looks_like_meta_question("你现在手上有什么工具") is True
    assert looks_like_meta_question("你都有哪些功能") is True
    assert looks_like_meta_question("现在几点了") is True
    assert looks_like_meta_question("还有多久") is True
    assert looks_like_meta_question("我有鸡蛋和西红柿") is False
    assert looks_like_meta_question("番茄、鸡蛋") is False
    assert looks_like_meta_question("用这些能做什么菜") is False  # 烹饪域词：留在做饭流程
    assert looks_like_meta_question("番茄炒蛋怎么做") is False
    assert looks_like_meta_question("") is False
