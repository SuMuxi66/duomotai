# -*- coding: utf-8 -*-
"""
用量问答自动下料回归测试：
做菜进行中菜谱没写清调料用量，用户问"盐放多少"，AI 给出克数建议后
系统捕获该克数自动接通下料机（_maybe_dispense_recommended_amount）。

三层验证：
- looks_like_seasoning_amount_question：问句判定（调料词 + 用量询问 cue）
- extract_recommended_dispense_grams：AI 回答克数提取（调料词相邻、多候选取最小）
- _maybe_dispense_recommended_amount：状态守卫 + start_dispense 交互（monkeypatch，不触硬件）
参考 tests/test_dispense_auto.py 的 monkeypatch 风格。
"""
import asyncio

from app import cooking_core
from app.session import SessionState
import app.ws_handlers.cooking as cooking_module


# ---------- looks_like_seasoning_amount_question：纯函数 ----------

def test_question_salt_amount_hit():
    """典型用量询问命中。"""
    assert cooking_core.looks_like_seasoning_amount_question("盐放多少") is True
    assert cooking_core.looks_like_seasoning_amount_question("这步要放多少盐") is True
    assert cooking_core.looks_like_seasoning_amount_question("要几克糖") is True
    assert cooking_core.looks_like_seasoning_amount_question("生抽的用量是多少") is True


def test_question_non_amount_salt_mention_miss():
    """含调料词但非用量询问不命中（求救/闲聊不该触发下料）。"""
    assert cooking_core.looks_like_seasoning_amount_question("盐放多了怎么办") is False
    assert cooking_core.looks_like_seasoning_amount_question("为什么要放糖") is False
    assert cooking_core.looks_like_seasoning_amount_question("盐是不是不够咸") is False


def test_question_amount_cue_without_seasoning_miss():
    """含用量 cue 但无调料词不命中（"水放多少"不是下料机投放物）。"""
    assert cooking_core.looks_like_seasoning_amount_question("水要放多少") is False
    assert cooking_core.looks_like_seasoning_amount_question("火要开多久") is False


def test_question_empty_miss():
    assert cooking_core.looks_like_seasoning_amount_question("") is False
    assert cooking_core.looks_like_seasoning_amount_question(None) is False


# ---------- extract_recommended_dispense_grams：纯函数 ----------

def test_extract_direct_recommendation():
    """AI 直接建议："建议放 3 克盐" → 3。"""
    assert cooking_core.extract_recommended_dispense_grams("建议放 3 克盐。") == 3.0
    assert cooking_core.extract_recommended_dispense_grams("两个人份的话，放 5g 盐比较合适") == 5.0


def test_extract_background_amount_picks_min():
    """背景量干扰："每 100 克肉放 3 克盐" → 取 3（最小），不把背景量 100 当投放量。"""
    assert cooking_core.extract_recommended_dispense_grams("一般每 100 克肉放 3 克盐就够了") == 3.0


def test_extract_no_seasoning_adjacent_gram_no_trigger():
    """无调料词相邻的克数不触发（宁不下料不下错料）。"""
    assert cooking_core.extract_recommended_dispense_grams("适量即可，尝尝味道再调整") is None
    assert cooking_core.extract_recommended_dispense_grams("取 200 克五花肉切块") is None


def test_extract_range_limits():
    """0.5~100g 合法范围约束与步骤引导一致。"""
    assert cooking_core.extract_recommended_dispense_grams("放 0.2 克盐提提味") is None
    assert cooking_core.extract_recommended_dispense_grams("腌咸菜要 500 克盐") is None


def test_extract_empty_answer():
    assert cooking_core.extract_recommended_dispense_grams("") is None
    assert cooking_core.extract_recommended_dispense_grams(None) is None


# ---------- _maybe_dispense_recommended_amount：monkeypatch 交互 ----------

def _make_state(cooking_state="COOKING"):
    state = SessionState()
    state.cooking_state = cooking_state
    return state


def test_flow_starts_dispense_with_recommended_grams(monkeypatch):
    """COOKING 态 + 用量问句 + AI 回答含克数 → 启动下料并播报确认语。"""
    captured = {"target_g": None, "sent": []}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True, "target_g": target_g}

    async def fake_send_text(runtime, ws, state, text, **kw):
        captured["sent"].append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(
            None, None, state, "盐放多少", "菜谱没写的话，建议放 3 克盐。"
        )
    )
    assert captured["target_g"] == 3.0
    assert any("自动下料 3 克" in t for t in captured["sent"])


def test_flow_step_waiting_state_allowed(monkeypatch):
    """STEP_WAITING 同为进行中状态，允许触发。"""
    captured = {"target_g": None}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True}

    async def fake_send_text(runtime, ws, state, text, **kw):
        pass

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = _make_state("STEP_WAITING")
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(None, None, state, "糖放多少", "建议放 4 克糖")
    )
    assert captured["target_g"] == 4.0


def test_flow_ignored_outside_active_states(monkeypatch):
    """RECOMMENDING 等非进行中状态不触发。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state("RECOMMENDING")
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(None, None, state, "盐放多少", "建议放 3 克盐")
    )
    assert called["n"] == 0


def test_flow_non_amount_question_no_start(monkeypatch):
    """非用量询问（"盐放多了怎么办"）不触发。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(
            None, None, state, "盐放多了怎么办", "可以加点土豆吸盐，或加水稀释。"
        )
    )
    assert called["n"] == 0


def test_flow_answer_without_grams_no_start(monkeypatch):
    """AI 回答没给克数（"适量"）不触发。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(None, None, state, "盐放多少", "放适量即可，尝尝味道。")
    )
    assert called["n"] == 0


def test_flow_active_dispense_not_interrupted(monkeypatch):
    """已有下料进行中：跳过，不打断进行中的投放。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    def fake_status():
        return {"active": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.dispense_module, "get_dispense_status", fake_status)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(None, None, state, "盐放多少", "建议放 3 克盐")
    )
    assert called["n"] == 0


def test_flow_start_failure_announces_manual_hint(monkeypatch):
    """启动失败（串口未就绪）：不崩溃，播报按克数手动放的提示。"""
    sent = []

    async def fake_start_dispense(*a, **k):
        return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}

    async def fake_send_text(runtime, ws, state, text, **kw):
        sent.append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_dispense_recommended_amount(None, None, state, "盐放多少", "建议放 3 克盐")
    )
    assert any("手动放" in t for t in sent)


# ---------- parse_dispense_command：显式下料指令确定性拦截 ----------
# 实测（2026-09-04 19:57）："下3克盐"在 cooking_enabled=False 时落普通问答，
# 模型回复"无法控制下料设备"——显式指令改为确定性拦截，不依赖 LLM/做饭开关。

def test_cmd_verb_grams_seasoning():
    """语序A：动词+克数+调料。"""
    assert cooking_core.parse_dispense_command("下3克盐") == 3.0
    assert cooking_core.parse_dispense_command("放5g白糖") == 5.0
    assert cooking_core.parse_dispense_command("下3克的盐") == 3.0
    assert cooking_core.parse_dispense_command("下3克盐吧") == 3.0
    assert cooking_core.parse_dispense_command("先放3克盐") == 3.0
    assert cooking_core.parse_dispense_command("请帮我放5克糖") == 5.0
    assert cooking_core.parse_dispense_command("放生抽5克") == 5.0


def test_cmd_bare_and_dispense_word():
    """裸指令（无调料词）与"下料"指令。"""
    assert cooking_core.parse_dispense_command("帮我下3克") == 3.0
    assert cooking_core.parse_dispense_command("下3克") == 3.0
    assert cooking_core.parse_dispense_command("下料3克") == 3.0


def test_cmd_seasoning_first_order():
    """语序B：调料+克数。"""
    assert cooking_core.parse_dispense_command("下盐3克") == 3.0


def test_cmd_seasoning_verb_grams_order():
    """语序C（2026-09-05 用户表达）：调料+动词+克数，如"盐巴下3克"。"""
    assert cooking_core.parse_dispense_command("盐巴下3克") == 3.0
    assert cooking_core.parse_dispense_command("盐巴下三克") == 3.0
    assert cooking_core.parse_dispense_command("白糖加5克") == 5.0
    assert cooking_core.parse_dispense_command("生抽倒10克") == 10.0
    assert cooking_core.parse_dispense_command("帮我盐巴下3克吧") == 3.0
    # 非调料主语/带宾语的句子不触发
    assert cooking_core.parse_dispense_command("水中下3克") is None
    assert cooking_core.parse_dispense_command("糖加30克水") is None
    assert cooking_core.parse_dispense_command("配方里加3克糖") is None
    assert cooking_core.parse_dispense_command("一般放2克盐") is None


def test_cmd_verb_dao():
    """动词"倒"覆盖（"倒3克盐"）。"""
    assert cooking_core.parse_dispense_command("倒3克盐") == 3.0


def test_cmd_chinese_numerals():
    """语音 STT 常输出中文数字。"""
    assert cooking_core.parse_dispense_command("下三克盐") == 3.0
    assert cooking_core.parse_dispense_command("放两克糖") == 2.0
    assert cooking_core.parse_dispense_command("下十五克盐") == 15.0
    assert cooking_core.parse_dispense_command("下半克盐") == 0.5
    assert cooking_core.parse_dispense_command("下三点五克盐") == 3.5


def test_cmd_range_boundary():
    """0.5~100g 含边界（与 extract_dispense_grams 一致），界外不触发。"""
    assert cooking_core.parse_dispense_command("放100克盐") == 100.0
    assert cooking_core.parse_dispense_command("放150克盐") is None
    assert cooking_core.parse_dispense_command("放0.3克盐") is None


def test_cmd_non_commands_miss():
    """非指令/讨论式语句/疑问句一律不拦截，交回问答链路。"""
    assert cooking_core.parse_dispense_command("加100克水") is None  # 水不可投放
    assert cooking_core.parse_dispense_command("放3克油") is None  # 裸"油"不在调料词表
    assert cooking_core.parse_dispense_command("要放3克盐吗") is None  # 疑问语气
    assert cooking_core.parse_dispense_command("盐放多了怎么办") is None
    assert cooking_core.parse_dispense_command("配方里加3克糖") is None  # 讨论式非命令
    assert cooking_core.parse_dispense_command("一般放2克盐") is None
    assert cooking_core.parse_dispense_command("放一点盐") is None  # 无克数
    assert cooking_core.parse_dispense_command("重量是500克") is None
    assert cooking_core.parse_dispense_command("") is None
    assert cooking_core.parse_dispense_command(None) is None


def test_explicit_cmd_runs_dispense(monkeypatch):
    """显式指令执行端：start_dispense 收到克数并播报确认语。"""
    captured = {"target_g": None, "sent": []}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True, "target_g": target_g}

    async def fake_send_text(runtime, ws, state, text, **kw):
        captured["sent"].append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    state = SessionState()  # IDLE 态也要能响应（不依赖做饭模式开关）
    asyncio.run(
        cooking_module.handle_explicit_dispense_command(None, None, state, 3.0, request_id="lat-x")
    )
    assert captured["target_g"] == 3.0
    assert any("自动下料 3 克" in t for t in captured["sent"])


def test_explicit_cmd_failure_announces(monkeypatch):
    """显式指令启动失败（串口未就绪）：播报检查提示，不崩溃。"""
    sent = []

    async def fake_start_dispense(*a, **k):
        return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}

    async def fake_send_text(runtime, ws, state, text, **kw):
        sent.append(text)

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
    asyncio.run(cooking_module.handle_explicit_dispense_command(None, None, SessionState(), 3.0))
    assert any("没有就绪" in t for t in sent)


# ---------- 端到端：handle_cooking_question 收尾自动接通下料 ----------

class _Harness:
    def __init__(self, monkeypatch, llm_answer):
        self.sent_texts = []

        async def fake_send_text(runtime, ws, state, text, **kw):
            self.sent_texts.append(text)

        async def fake_llm_response(*args, **kwargs):
            return llm_answer

        monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)
        from app.ws import llm_handler as _llm
        monkeypatch.setattr(_llm, "handle_llm_response", fake_llm_response)


def test_e2e_cooking_question_triggers_dispense(monkeypatch):
    """handle_cooking_question 端到端：问"盐放多少" → AI 建议克数 → start_dispense 被调用。"""
    captured = {"target_g": None}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True}

    async def fake_send_text(runtime, ws, state, text, **kw):
        pass

    harness = _Harness(monkeypatch, "菜谱里没写的话，建议放 3 克盐。")
    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)

    state = SessionState()
    state.session_id = "unit-e2e-dispense"
    state.cooking_enabled = True
    state.cooking_state = "COOKING"
    state.current_recipe = {
        "name": "清炒土豆丝",
        "steps": [{"title": "调味", "content": "加入适量盐调味"}],
    }
    state.current_step = 1
    # 真实链路里 handle_user_text 会先 record_user_message；直接调问答收尾需手工配对
    state.context_engine.record_user_message(session_id=state.session_id, content="盐放多少")
    asyncio.run(
        cooking_module.handle_cooking_question(
            harness, None, state, "盐放多少", "", None, None, None, None, None
        )
    )
    assert captured["target_g"] == 3.0


def test_e2e_cooking_question_no_dispense_for_plain_question(monkeypatch):
    """端到端回归：普通问句（为什么要焯水）不触发下料。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    async def fake_send_text(runtime, ws, state, text, **kw):
        pass

    harness = _Harness(monkeypatch, "焯水可以去除土豆表面的淀粉，口感更爽脆。")
    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    monkeypatch.setattr(cooking_module.cooking_core, "send_cooking_text_with_tts", fake_send_text)

    state = SessionState()
    state.session_id = "unit-e2e-plain"
    state.cooking_enabled = True
    state.cooking_state = "COOKING"
    state.current_recipe = {
        "name": "清炒土豆丝",
        "steps": [{"title": "焯水", "content": "土豆丝焯水 30 秒"}],
    }
    state.current_step = 1
    state.context_engine.record_user_message(session_id=state.session_id, content="为什么要焯水")
    asyncio.run(
        cooking_module.handle_cooking_question(
            harness, None, state, "为什么要焯水", "", None, None, None, None, None
        )
    )
    assert called["n"] == 0
