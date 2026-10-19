# -*- coding: utf-8 -*-
"""
单元测试：cooking_core 纯函数
覆盖：关键词判断、菜名提取、份数解析、倒计时解析、食材解析、引导文本构建
（不依赖 ws / LLM / 网络，仅使用 RecipeService 读取本地菜谱库）
"""
import pytest

from app import cooking_core
from app.cooking_core import (
    _wants_step_guide,
    _declines_step_guide,
    _accepts_step_guide,
    _looks_like_step_guide_ask,
    _looks_like_cooking_question,
    _looks_like_more_recommendation_request,
    _extract_recipe_name_candidate,
    _is_generic_cooking_entry_candidate,
    parse_servings_from_text,
    parse_original_servings_from_recipe,
    parse_timer_request,
    format_timer_duration,
    parse_ingredient_text,
    build_step_guidance_text,
    build_preparation_text,
    build_servings_confirmation_text,
    _multiply_amount_in_line,
    _seasoning_converged_multiplier,
    recipe_to_dict,
    _normalize_generated_recipe,
)
from app.services.recipe_service import get_recipe_service
from app.session import SessionState


# ============ 关键词判断 ============

class TestWantsStepGuide:
    def test_exact_keyword_matches(self):
        assert _wants_step_guide("我要步骤式引导我做菜") is True

    def test_embedded_keyword_matches(self):
        assert _wants_step_guide("可以步骤式教我做番茄炒蛋吗") is True

    def test_synonym_stepwise_matches(self):
        # 修复9：同义引导表达也直接进预设（写死入口，不再依赖 LLM 输出标记）
        for text in ("一步一步教我做", "教我一步步做", "手把手带我", "你要一步一步来"):
            assert _wants_step_guide(text) is True, f"应命中: {text}"

    def test_decline_guide_word_priority(self):
        # 拒绝优先：避免"不需要一步一步"被误判为引导请求
        assert _wants_step_guide("不需要一步一步") is False
        assert _wants_step_guide("先不用，算了") is False

    def test_synonym_without_keyword_not_match(self):
        # 设计如此：仅做饭意图表达不命中写死入口（走普通问答由 AI 收集信息）
        assert _wants_step_guide("教我做番茄炒蛋") is False
        assert _wants_step_guide("怎么做菜") is False

    def test_empty_text(self):
        assert _wants_step_guide("") is False


class TestDeclinesStepGuide:
    def test_decline_words(self):
        for text in ("不用了", "先不用", "算了，我就问问", "不需要步骤式"):
            assert _declines_step_guide(text) is True

    def test_normal_cooking_text_not_decline(self):
        assert _declines_step_guide("我要做番茄炒蛋") is False

    def test_confirm_word_not_decline(self):
        assert _declines_step_guide("可以") is False


class TestAcceptsStepGuide:
    def test_short_confirmations(self):
        for text in ("可以", "好", "好的", "嗯", "要", "开始吧", "行"):
            assert _accepts_step_guide(text) is True, f"应命中: {text}"

    def test_needs_confirmation(self):
        # 修复8：用户对 AI 询问回复"需要/需要你"也是明确确认
        for text in ("需要", "需要你", "需要啊"):
            assert _accepts_step_guide(text) is True, f"应命中: {text}"

    def test_long_text_not_misjudged(self):
        # 长度 >4 不当作确认，避免"我要做红烧肉"里的"要"误判
        assert _accepts_step_guide("我要做红烧肉") is False
        assert _accepts_step_guide("可以的，我们开始吧") is False

    def test_open_ended_question_not_confirm(self):
        assert _accepts_step_guide("有什么推荐吗") is False


class TestLooksLikeStepGuideAsk:
    def test_ai_asks_text(self):
        for text in (
            "需要我一步一步带你做这道菜吗",
            "需要我一步步教您吗",
            "要我带你做吗",
            "要不要我一步一步教你",
            "是否要我一步步做",
        ):
            assert _looks_like_step_guide_ask(text) is True, f"应命中: {text}"

    def test_unrelated_text(self):
        assert _looks_like_step_guide_ask("番茄要炒出红汁") is False


class TestLooksLikeCookingQuestion:
    def test_cooking_questions(self):
        for text in ("现在火候怎么控制", "要炒几分钟", "温度多高合适", "盐放多少"):
            assert _looks_like_cooking_question(text) is True, f"应命中: {text}"

    def test_unrelated(self):
        assert _looks_like_cooking_question("你好") is False


# ============ 菜名提取 ============

class TestExtractRecipeNameCandidate:
    def test_prefix_stripping(self):
        for text, expect in (
            ("我要做番茄炒蛋", "番茄炒蛋"),
            ("我想做红烧肉", "红烧肉"),
            ("帮我做青椒炒肉", "青椒炒肉"),
            ("做个番茄炒蛋", "番茄炒蛋"),
            ("我想吃番茄炒蛋", "番茄炒蛋"),
        ):
            assert _extract_recipe_name_candidate(text) == expect, f"{text} -> {expect}"

    def test_suffix_stripping(self):
        assert _extract_recipe_name_candidate("番茄炒蛋怎么做") == "番茄炒蛋"

    def test_suffix_zuofa_expected(self):
        # 修复#1：后缀按长度降序，"的做法" 优先于 "做法"
        assert _extract_recipe_name_candidate("红烧肉的做法") == "红烧肉"

    def test_missing_prefix_not_stripped(self):
        # 修复10：引导/动词语现已纳入前缀表（"教我做"等），剥离后得到干净菜名
        assert _extract_recipe_name_candidate("教我做番茄炒鸡蛋") == "番茄炒鸡蛋"
        assert _extract_recipe_name_candidate("一步一步教我做番茄炒鸡蛋") == "番茄炒鸡蛋"
        assert _extract_recipe_name_candidate("手把手教我做红烧肉") == "红烧肉"

    def test_generic_entry(self):
        assert _is_generic_cooking_entry_candidate("饭") is True
        assert _is_generic_cooking_entry_candidate("做菜") is True
        assert _is_generic_cooking_entry_candidate("番茄炒蛋") is False


# ============ 份数解析 ============

class TestParseServings:
    def test_arabic_with_unit(self):
        assert parse_servings_from_text("2人份") == 2
        assert parse_servings_from_text("3 个人") == 3
        assert parse_servings_from_text("4份") == 4

    def test_cn_numbers(self):
        assert parse_servings_from_text("三人") == 3
        assert parse_servings_from_text("两个人") == 2
        assert parse_servings_from_text("十人") == 10
        assert parse_servings_from_text("一") == 1

    def test_pure_digit(self):
        assert parse_servings_from_text("5") == 5

    def test_invalid(self):
        assert parse_servings_from_text("随便") is None
        assert parse_servings_from_text("0人") is None
        assert parse_servings_from_text("") is None


class TestParseOriginalServings:
    def test_extract(self):
        assert parse_original_servings_from_recipe({"servings_note": "一份正好够 3 个人吃"}) == 3

    def test_default(self):
        assert parse_original_servings_from_recipe({"servings_note": ""}) == 2
        assert parse_original_servings_from_recipe({}) == 2


# ============ 倒计时解析 ============

class TestParseTimer:
    def test_no_keyword(self):
        assert parse_timer_request("帮我炒个菜") is None

    def test_hours_minutes_seconds(self):
        assert parse_timer_request("定时1小时")["seconds"] == 3600
        assert parse_timer_request("倒计时2分钟")["seconds"] == 120
        assert parse_timer_request("计时90秒")["seconds"] == 90
        assert parse_timer_request("定时1小时30分钟")["seconds"] == 5400

    def test_bare_number_defaults_minutes(self):
        req = parse_timer_request("定时5")
        assert req is not None
        assert req["seconds"] == 300

    def test_cn_number(self):
        assert parse_timer_request("倒计时两小时")["seconds"] == 7200

    def test_cn_half_hour_with_prefix(self):
        # 修复#4：触发词前缀 + 「半小时」
        assert parse_timer_request("倒计时半小时")["seconds"] == 1800

    def test_cn_number_with_naozhong(self):
        # 修复#2：中文数字 + 闹钟表述
        assert parse_timer_request("定个闹钟五分钟")["seconds"] == 300

    def test_upper_bound_24h(self):
        assert parse_timer_request("定时 25 小时")["seconds"] == 86400

    def test_description_extraction(self):
        req = parse_timer_request("定时5分钟煮鸡蛋")
        assert req["seconds"] == 300
        assert "鸡蛋" in req["description"]

    def test_naozhong_word_order(self):
        # 修复#3：乱序表述「定个X分钟的闹钟」也能命中
        assert parse_timer_request("定个5分钟的煮鸡蛋闹钟") is not None


class TestFormatTimerDuration:
    def test_levels(self):
        assert format_timer_duration(5400) == "1小时30分钟"
        assert format_timer_duration(3600) == "1小时"
        assert format_timer_duration(125) == "2分5秒"
        assert format_timer_duration(60) == "1分钟"
        assert format_timer_duration(45) == "45秒"


# ============ 食材解析 ============

class TestParseIngredientText:
    def test_comma_split(self):
        ingredients = parse_ingredient_text("我有番茄、鸡蛋、盐")
        assert "番茄" in ingredients and "鸡蛋" in ingredients and "盐" in ingredients

    def test_english_comma_split(self):
        ingredients = parse_ingredient_text("我有番茄,鸡蛋")
        assert "番茄" in ingredients and "鸡蛋" in ingredients

    def test_stop_words_filtered(self):
        ingredients = parse_ingredient_text("我想做饭")
        assert ingredients == [] or "我想做饭" not in ingredients

    def test_phrase_removal(self):
        # "做饭/做菜"等引导词被替换为空格，不应作为食材出现
        ingredients = parse_ingredient_text("我有番茄和鸡蛋，做饭")
        assert "做饭" not in ingredients

    def test_empty(self):
        assert parse_ingredient_text("") == []


# ============ 引导文本构建 ============

class TestBuildStepGuidanceText:
    def _state_with_recipe(self):
        state = SessionState()
        state.current_recipe = {
            "name": "番茄炒蛋",
            "steps": [
                {"title": "打蛋", "content": "把鸡蛋打散"},
                {"title": "炒蛋", "content": "热锅倒油炒蛋"},
            ],
            "tips": "糖不能省",
        }
        state.current_step = 1
        return state

    def test_first_step(self):
        text = build_step_guidance_text(self._state_with_recipe())
        assert "番茄炒蛋" not in text  # 步骤提示不再重复菜谱名
        assert "正在制作" not in text
        # 播报不再带"第几步/共几步"（步骤进度由前端面板展示）
        assert "第1步" not in text
        assert "共2步" not in text
        assert "打蛋" in text
        assert "告诉我" in text

    def test_unclean_title_falls_back_to_content(self):
        state = self._state_with_recipe()
        state.current_recipe["steps"] = [{"title": "步骤1", "content": "鸡腿洗净剁块"}]
        state.current_step = 1
        text = build_step_guidance_text(state)
        # 标题"步骤1"被清洗后直接用内容描述，且播报不带步骤序号
        assert "鸡腿洗净剁块" in text
        assert "步骤1" not in text
        assert "第1步" not in text

    def test_last_step_includes_tips(self):
        state = self._state_with_recipe()
        state.current_step = 2
        text = build_step_guidance_text(state)
        assert "小贴士" in text and "糖不能省" in text

    def test_servings_not_shown_in_step(self):
        state = self._state_with_recipe()
        state.current_servings = 2
        text = build_step_guidance_text(state)
        assert "人份" not in text

    def test_no_steps(self):
        state = SessionState()
        state.current_recipe = {"name": "空菜谱", "steps": []}
        text = build_step_guidance_text(state)
        assert text  # 不抛异常


class TestBuildPreparationText:
    def test_prep_and_servings(self):
        recipe = {
            "name": "番茄炒蛋",
            "servings_note": "一份正好够 3 个人吃",
            "per_serving_lines": ["鸡蛋：3 个", "番茄：2 个"],
            "prep_items": ["不粘锅", "碗"],
        }
        text = build_preparation_text(recipe)
        assert "准备" in text and "几人份" in text


class TestServingsConfirmation:
    def test_multiply(self):
        # 注：数字与单位间的空格会被正则吞掉（低严重格式问题，已在报告记录）
        assert _multiply_amount_in_line("鸡蛋 3 个", 2.0) == "鸡蛋 6个"
        # 盐属于调味料，2倍时按对数收敛 ≈ 5*1.42 = 7.1克（设计如此，非缺陷）
        assert _multiply_amount_in_line("盐 5 克", 2.0) == "盐 7.1克"

    def test_seasoning_convergence(self):
        # 调味料大幅增加时只线性增长 (multiplier<=1) 或对数收敛（上限 2.5）
        assert _seasoning_converged_multiplier("盐 5 克", 1.0) == 1.0
        converged = _seasoning_converged_multiplier("盐 5 克", 4.0)
        assert 1.0 < converged <= 2.5
        assert _seasoning_converged_multiplier("鸡蛋 3 个", 4.0) == 4.0  # 主料线性放大

    def test_confirmation_text(self):
        recipe = {"per_serving_lines": ["鸡蛋：3 个", "盐：5 克"]}
        text = build_servings_confirmation_text(recipe, 4, 2)
        assert "4 人份" in text
        assert "6" in text  # 3*2
        assert "盐" in text


# ============ 菜谱对象转换 ============

class TestRecipeToDict:
    def test_pydantic_model(self):
        from app.services.recipe_service import Recipe, RecipeStep

        recipe = Recipe(
            name="番茄炒蛋",
            difficulty="★★☆",
            ingredients=["番茄", "鸡蛋"],
            steps=[RecipeStep(step_number=1, title="打蛋", content="打散")],
        )
        data = recipe_to_dict(recipe)
        assert data["name"] == "番茄炒蛋"
        assert data["steps"][0]["title"] == "打蛋"

    def test_plain_dict(self):
        data = recipe_to_dict({"name": "红烧肉", "steps": [{"title": "焯水", "content": "焯水"}]})
        assert data["name"] == "红烧肉"

    def test_none(self):
        assert recipe_to_dict(None) is None


class TestNormalizeGeneratedRecipe:
    def test_valid(self):
        data = _normalize_generated_recipe({
            "name": "创意菜",
            "steps": ["第一步洗菜", "第二步切菜"],
        }, fallback_name="fallback")
        assert data is not None
        assert data["name"] == "创意菜"
        assert len(data["steps"]) == 2

    def test_missing_required(self):
        assert _normalize_generated_recipe({}) is None
        assert _normalize_generated_recipe({"name": "无步骤", "steps": []}) is None

    def test_step_number_prefix_stripped(self):
        from app.cooking_core import _normalize_step_title
        assert _normalize_step_title("第1步：打蛋") == "打蛋"
        assert _normalize_step_title("步骤 2：炒蛋") == "炒蛋"
        assert _normalize_step_title("3. 出锅") == "出锅"


class TestRecipeLookupPrompt:
    """修复10：RECIPE_LOOKUP_PROMPT 模板占位符与调用参数一致，format 不抛 KeyError"""

    def test_format_no_keyerror(self):
        from app.cooking_core import _build_recipe_lookup_prompt

        prompt = _build_recipe_lookup_prompt("番茄炒鸡蛋")
        assert "番茄炒鸡蛋" in prompt
        assert "{dish_name}" not in prompt
        assert "{user_input}" not in prompt


# ============ 真实菜谱库匹配 ============

class TestRecipeServiceIntegration:
    @pytest.fixture(scope="class")
    def service(self):
        return get_recipe_service()

    def test_direct_match_tomato_egg(self, service):
        matches = cooking_core._find_direct_recipe_matches(service, "番茄炒蛋")
        names = [getattr(m, "name", "") for m in matches]
        assert "番茄炒蛋" in names

    def test_direct_match_hongshaorou(self, service):
        matches = cooking_core._find_direct_recipe_matches(service, "我要做红烧肉")
        names = [getattr(m, "name", "") for m in matches]
        assert "红烧肉" in names

    def test_direct_match_not_found(self, service):
        matches = cooking_core._find_direct_recipe_matches(service, "云朵泡泡汤")
        assert "云朵泡泡汤" not in [getattr(m, "name", "") for m in matches]


# ============ 回归：份数数字误当菜名 + 确认后从 AI 提议选菜 ============

class TestPureDigitCandidateGuard:
    """纯数字（如份数回答"2"）不应被当菜名，避免模糊命中 B52轰炸机导致重选死循环"""

    def test_extract_candidate_rejects_pure_digits(self):
        assert _extract_recipe_name_candidate("2") == ""
        assert _extract_recipe_name_candidate("12") == ""

    def test_direct_matches_reject_pure_digits(self):
        class _BoomService:
            def search_by_name(self, term, limit=5):
                raise AssertionError("纯数字输入不应触发菜谱搜索")

        assert cooking_core._find_direct_recipe_matches(_BoomService(), "2") == []
        assert cooking_core._find_direct_recipe_matches(_BoomService(), " 2 ") == []


class TestEverydayPhrasesNotRecipeRequest:
    """回归（2026-09-03）：日常语句被菜名请求分支劫持进做饭预设。
    根因：COOKING_RECIPE_HINTS 含单字（面/锅/蛋…）子串匹配命中日常词（"画面"→面、"锅里"→锅），
    叠加疑问句未拦截（"看得见画面吗"）。"""

    def test_single_char_hints_not_matched_in_everyday_phrases(self):
        for text in (
            "看得见画面吗",
            "你看一下这个画面",
            "界面怎么卡住了",
            "前面还有几步",
            "帮我看下锅里的状态",
        ):
            assert cooking_core._looks_like_recipe_request(text) is False, text

    def test_question_particle_candidate_rejected(self):
        # 疑问语气词结尾=提问而非报菜名
        assert _extract_recipe_name_candidate("看得见画面吗") == ""
        assert _extract_recipe_name_candidate("红烧肉好吃吗") == ""
        assert _extract_recipe_name_candidate("番茄炒蛋呢") == ""
        assert _extract_recipe_name_candidate("你最爱什么") == ""

    def test_real_dish_requests_still_detected(self):
        # 真实菜名请求不受影响（多字提示词保留 / 菜谱库匹配分支兜底）
        assert _extract_recipe_name_candidate("番茄炒蛋怎么做") == "番茄炒蛋"
        assert _extract_recipe_name_candidate("教我做红烧肉") == "红烧肉"
        assert cooking_core._looks_like_recipe_request("教我做红烧肉") is True
        assert cooking_core._looks_like_recipe_request("做个番茄炒蛋") is True


class TestResolveFromAssistantProposal:
    """用户回复"需要"确认时，应选中 AI 上文用【菜名】提议的菜（本地库存在时）"""

    async def test_assistant_proposal_dish_selected(self):
        from types import SimpleNamespace
        from app.ws_handlers import cooking as ch

        engine = SimpleNamespace(
            get_recent_turns=lambda session_id, n: [
                SimpleNamespace(
                    user_message="炒菜吧",
                    assistant_message="好的，那我们做【西红柿炒鸡蛋】。家里有鸡蛋吗？需要我一步一步带你做这道菜吗？",
                )
            ]
        )
        state = SimpleNamespace(recent_user_texts=["炒菜吧"], context_engine=engine)
        recipe = await ch._resolve_recipe_from_recent_text(runtime=None, state=state)
        assert recipe is not None
        name = recipe.get("name", "")
        assert ("西红柿" in name or "番茄" in name) and "蛋" in name

    async def test_confirmed_word_only_falls_back_to_none(self):
        """候选里只有确认语/口语（无【菜名】、无菜谱特征）时不应远程胡编菜谱"""
        from types import SimpleNamespace
        from app.ws_handlers import cooking as ch

        engine = SimpleNamespace(
            get_recent_turns=lambda session_id, n: [
                SimpleNamespace(user_message="需要", assistant_message="需要我一步一步带你做这道菜吗？")
            ]
        )
        state = SimpleNamespace(recent_user_texts=["需要"], context_engine=engine)
        recipe = await ch._resolve_recipe_from_recent_text(runtime=None, state=state)
        # "需要"无菜谱特征 → 不触发远程生成，本地也无匹配 → 返回 None 走询问菜名
        assert recipe is None
