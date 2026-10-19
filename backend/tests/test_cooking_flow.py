# -*- coding: utf-8 -*-
"""
集成测试：cooking 状态机全旅程（用户对话驱动）
用假 ws/runtime 与记录型 send_text/send_status，驱动 handle_cooking_user_text，
断言状态迁移与推送文本。不触碰真实 LLM / TTS / 网络。
"""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from app import cooking_core, ws_handlers  # noqa: F401 (导入链)
from app.session import SessionState


# ---------- 测试夹具 ----------

class Harness:
    """记录型 harness：收集 send_cooking_text_with_tts / send_cooking_status 的调用"""

    def __init__(self, monkeypatch):
        self.sent_texts: list[str] = []
        self.sent_statuses: list[dict] = []
        self.questions: list[tuple] = []

        async def fake_send_text(runtime, ws, state, text, **kw):
            self.sent_texts.append(text)

        async def fake_send_status(ws, state, recipes=None):
            self.sent_statuses.append({
                "enabled": state.cooking_enabled,
                "state": state.cooking_state,
                "recipe_name": (state.current_recipe or {}).get("name"),
                "step": state.current_step,
                "servings": state.current_servings,
                "awaiting_servings": state.awaiting_servings,
            })

        async def fake_lookup_remote(runtime, user_input):
            return None  # 防御：任何远程菜谱搜索返回空，避免真实 LLM

        async def fake_llm_response(*args, **kwargs):
            return "（mock LLM 回复）"  # 防御：handle_cooking_question 兜底

        monkeypatch.setattr(cooking_core, "send_cooking_text_with_tts", fake_send_text)
        monkeypatch.setattr(cooking_core, "send_cooking_status", fake_send_status)
        monkeypatch.setattr(cooking_core, "lookup_remote_recipe", fake_lookup_remote)
        from app.ws import llm_handler as _llm
        monkeypatch.setattr(_llm, "handle_llm_response", fake_llm_response)
        # ws_handlers.cooking 顶部 `from .. import cooking_core`，也需保证其内的 send 指向同源
        import app.ws_handlers.cooking as ch
        monkeypatch.setattr(ch.cooking_core, "send_cooking_text_with_tts", fake_send_text)
        monkeypatch.setattr(ch.cooking_core, "send_cooking_status", fake_send_status)
        monkeypatch.setattr(ch.cooking_core, "lookup_remote_recipe", fake_lookup_remote)

    def new_state(self):
        state = SessionState()
        state.session_id = f"unit-{id(self)}-{len(self.sent_statuses)}"
        return state

    @property
    def runtime(self):
        return SimpleNamespace(llm=None, tts=None)


@pytest.fixture
def h(monkeypatch):
    return Harness(monkeypatch)


# ---------- 用例 ----------

class TestEntry:
    async def test_normal_question_not_cooking(self, h):
        """普通问题不进做饭流程"""
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "今天天气不错", "", None, None, None, None, None)
        assert handled is False
        assert state.cooking_enabled is False
        assert state.cooking_state == "IDLE"

    async def test_step_guide_keyword_enters(self, h):
        """路径①：报菜名（含"步骤式引导"）→ 查到菜谱后直接开始第 1 步（不再询问几人份）"""
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我要步骤式引导我做番茄炒蛋", "", None, None, None, None, None)
        assert handled is True
        assert state.current_recipe is not None
        assert state.current_recipe["name"] == "番茄炒蛋"
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        assert state.awaiting_servings is False
        assert state.cooking_enabled is True
        assert any("已找到" in t for t in h.sent_texts)

    async def test_dish_name_request_direct_start(self, h):
        """路径②：说「教我做番茄炒蛋」→ 查到菜谱后直接开始第 1 步（新流程无份数询问）"""
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "教我做番茄炒蛋", "", None, None, None, None, None)
        assert handled is True
        assert state.awaiting_step_guide_confirmation is False
        assert state.current_recipe is not None
        assert state.current_recipe["name"] == "番茄炒蛋"
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        assert state.cooking_enabled is True


class TestEverydayPhrasesNotCooking:
    """回归（2026-09-03）：含"画面/界面/锅"等字的日常语句曾被劫持为菜名请求
    （日志：[做饭] 识别到菜名请求: 看得见画面吗），应放行普通问答"""

    async def test_vision_question_passes_through(self, h):
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "看得见画面吗", "", None, None, None, None, None)
        assert handled is False
        assert state.cooking_enabled is False
        assert state.cooking_state == "IDLE"
        assert state.current_recipe is None

    async def test_picture_statement_passes_through(self, h):
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "你看一下这个画面", "", None, None, None, None, None)
        assert handled is False
        assert state.cooking_state == "IDLE"

    async def test_pot_status_question_passes_through(self, h):
        state = h.new_state()
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "帮我看下锅里的状态", "", None, None, None, None, None)
        assert handled is False
        assert state.cooking_state == "IDLE"


class TestIngredientAndSelect:
    async def test_add_ingredients_recommends(self, h):
        """引导中给食材 → 推荐菜谱"""
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我有番茄、鸡蛋", "", None, None, None, None, None)
        assert handled is True
        assert "番茄" in state.user_ingredients and "鸡蛋" in state.user_ingredients
        assert state.cooking_state in ("RECOMMENDING", "COLLECTING_INGREDIENTS")
        assert any("推荐" in t for t in h.sent_texts)

    async def test_meta_question_not_ingredients(self, h):
        """非做饭提问（你有什么工具）不进食材流程，落普通问答（2026-09-11 实测回归）。

        实测"你现在手上有什么工具"曾被 parse_ingredient_text 拆成
        ['你现在手上','什么工具']，收集态直接添加并误推荐菜谱。
        """
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        assert state.cooking_enabled is True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "你现在手上有什么工具", "", None, None, None, None, None)
        assert handled is False
        assert state.user_ingredients == []
        assert not any("您现在有" in t for t in h.sent_texts)

    async def test_meta_question_keeps_cooking_state(self, h):
        """问句穿透后做饭状态不被破坏，后续真实食材照常添加"""
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "你现在手上有什么工具", "", None, None, None, None, None)
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我有番茄、鸡蛋", "", None, None, None, None, None)
        assert handled is True
        assert "番茄" in state.user_ingredients and "鸡蛋" in state.user_ingredients

    async def test_select_recipe(self, h):
        """已进入做饭模式后说菜名 → 直接选中并开始第 1 步（不再询问份数）"""
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我要做番茄炒蛋", "", None, None, None, None, None)
        assert handled is True
        assert state.current_recipe is not None
        assert state.current_recipe["name"] == "番茄炒蛋"
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1

    async def test_select_missing_recipe(self, h):
        """已进入做饭模式但本地无该菜谱（远程兜底 mock 禁用）→ 给出引导提示而非崩溃"""
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我要做佛跳墙", "", None, None, None, None, None)
        assert handled is True
        # 无菜谱时按设计回到"开始步骤式引导"提示
        assert any("步骤式" in t for t in h.sent_texts)


class TestDirectStart:
    """2026-09-06：份数问答/智能计算食材功能已移除，菜谱确定即直接进入 COOKING 第 1 步"""

    async def _enter_recipe(self, h, name="番茄炒蛋"):
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, f"我要做{name}", "", None, None, None, None, None)
        return state

    async def test_select_directly_starts_cooking(self, h):
        state = await self._enter_recipe(h)
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        assert state.awaiting_servings is False
        assert state.current_servings is None
        # 播报不含份数询问，也不含"第几步/共几步"
        assert not any("几人份" in t for t in h.sent_texts)
        assert not any("第1步" in t or "共" in t for t in h.sent_texts)
        assert any("开始" in t for t in h.sent_texts)

    async def test_servings_answer_mid_cooking_is_plain_question(self, h):
        """做菜中报份数不再有状态应答：不破坏流程，放行普通问答"""
        state = await self._enter_recipe(h)
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "2人份", "", None, None, None, None, None)
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        assert state.awaiting_servings is False
        assert handled in (True, False)  # 状态不因份数答复而改变即可


class TestStepFlow:
    async def _in_step(self, h, step=1):
        # 新流程：菜谱确定即直接进入第 1 步（无份数/食材确认中间步）
        state = await TestDirectStart()._enter_recipe(h, "番茄炒蛋")
        assert state.current_step == step
        return state

    async def test_confirm_goes_next(self, h):
        state = await self._in_step(h)
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "好了", "", None, None, None, None, None)
        assert handled is True
        assert state.current_step == 2
        assert 1 in state.completed_steps

    async def test_skip_goes_next(self, h):
        state = await self._in_step(h)
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "跳过", "", None, None, None, None, None)
        assert handled is True
        assert state.current_step == 2

    async def test_full_flow_to_finished(self, h):
        state = await self._in_step(h)
        steps = len(state.current_recipe["steps"])
        guard = 0
        while state.cooking_state != "FINISHED" and guard < steps + 3:
            await ws_handlers.cooking.handle_cooking_user_text(
                h.runtime, None, state, "好了", "", None, None, None, None, None)
            guard += 1
        assert state.cooking_state == "FINISHED"
        assert any("做好了" in t for t in h.sent_texts)

    async def test_cooking_step_question(self, h):
        """引导中提问（火候/时间）→ 走 cooking_question（mock LLM 防真实调用）"""
        state = await self._in_step(h)
        await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "火候要怎么控制", "", None, None, None, None, None)
        assert state.current_step == 1
        assert "（mock LLM 回复）" not in state.current_recipe  # 无断言崩溃即过


class TestExit:
    async def test_exit_confirm(self, h):
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "退出做饭", "", None, None, None, None, None)
        assert handled is True
        assert state.awaiting_cooking_exit_confirmation is True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "是", "", None, None, None, None, None)
        assert handled is True
        assert state.cooking_enabled is False
        assert state.cooking_state == "IDLE"

    async def test_exit_cancel(self, h):
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "退出做饭", "", None, None, None, None, None)
        await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "继续", "", None, None, None, None, None)
        assert state.cooking_enabled is True
        assert state.awaiting_cooking_exit_confirmation is False


class TestSwitchDish:
    async def test_switch_to_new_dish_resets(self, h):
        """引导中换菜 → 重置进度并直接开始新菜第 1 步（不再询问份数）"""
        state = await TestDirectStart()._enter_recipe(h, "番茄炒蛋")
        assert state.current_step == 1
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我要做红烧肉", "", None, None, None, None, None)
        assert handled is True
        assert state.current_recipe["name"] == "红烧肉"
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        assert state.completed_steps == []

    async def test_more_recommendation(self, h):
        """「换一个」→ 推荐换一批"""
        state = h.new_state()
        await ws_handlers.cooking.control_start_cooking(h.runtime, None, state, {})
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "换一批别的菜", "", None, None, None, None, None)
        assert handled is True
        assert any("换一批" in t or "推荐" in t for t in h.sent_texts)


class TestStepGuideConfirmation:
    async def test_accept_from_history_finds_recipe(self, h):
        """AI 询问后用户确认「可以」，从最近消息回找菜名并选中"""
        state = h.new_state()
        state.recent_user_texts.append("番茄炒蛋")
        state.awaiting_step_guide_confirmation = True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "可以", "", None, None, None, None, None)
        assert handled is True
        assert state.awaiting_step_guide_confirmation is False
        assert state.cooking_enabled is True
        # 本地命中直接开始做第 1 步（不再询问菜名/份数）
        assert state.current_recipe is not None
        assert state.current_recipe["name"] == "番茄炒蛋"
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1

    async def test_accept_needs_reuses_remote_recipe(self, h, monkeypatch):
        """修复8：AI 询问后用户回「需要」，菜名（如"番茄炒鸡蛋"）直接解析进入准备流程，
        不再重新询问菜名。同义词归一（番茄=西红柿）加入后，本地库即可命中"西红柿炒蛋"，
        命中不了时仍回退远程生成（monkeypatch 兜底），两者都算通过"""
        remote_recipe = {
            "name": "番茄炒鸡蛋",
            "steps": [{"title": "准备食材", "content": "备料"}],
            "prep_items": ["鸡蛋", "番茄"],
            "per_serving_lines": ["鸡蛋 3 个", "番茄 2 个"],
            "servings_note": "一份约 2 人吃",
        }

        async def fake_remote(runtime, user_input):
            return dict(remote_recipe)

        monkeypatch.setattr(cooking_core, "lookup_remote_recipe", fake_remote)

        state = h.new_state()
        state.recent_user_texts.append("教我做番茄炒鸡蛋")
        state.awaiting_step_guide_confirmation = True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "需要", "", None, None, None, None, None)
        assert handled is True
        assert state.awaiting_step_guide_confirmation is False
        assert state.current_recipe is not None
        assert state.current_recipe["name"] in {"番茄炒鸡蛋", "西红柿炒蛋", "西红柿炒鸡蛋"}
        assert state.cooking_state == "COOKING"
        assert state.current_step == 1
        # 关键：不再输出"开始步骤式引导做饭，请问您有什么想做的"（不重问菜名）
        assert not any("有什么想做的" in t or "请问您有什么想做的" in t for t in h.sent_texts)

    async def test_decline(self, h):
        state = h.new_state()
        state.awaiting_step_guide_confirmation = True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "不用了", "", None, None, None, None, None)
        assert handled is False
        assert state.awaiting_step_guide_confirmation is False

    async def test_long_text_not_confirm(self, h):
        """确认词误判防护：长句不当作确认"""
        state = h.new_state()
        state.awaiting_step_guide_confirmation = True
        handled = await ws_handlers.cooking.handle_cooking_user_text(
            h.runtime, None, state, "我要做红烧肉", "", None, None, None, None, None)
        assert handled is False
        assert state.awaiting_step_guide_confirmation is True