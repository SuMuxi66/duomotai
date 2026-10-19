from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from fastapi import WebSocket

from .. import ws as transport_ws
from .. import cooking_core
from ..timer_manager import timer_manager, create_countdown
from ..services.recipe_service import get_recipe_service
from ..session import SessionState
from ..config import settings
from .. import dispense as dispense_module
from ..logger import backend_log


async def _resolve_recipe_from_recent_text(runtime: Any, state: SessionState) -> dict[str, Any] | None:
    """确认步骤式引导后，从最近用户消息解析具体菜谱（dict 形式）

    优先级：本地精确匹配 → 本地模糊匹配（含"我的做法"等残缺候选）→ 远程 LLM 生成。
    提取不到明确菜名候选时返回 None（由调用方回退为询问菜名）。
    目的：用户已提过菜名（如"教我做番茄炒鸡蛋"）时，确认后直接进入准备流程，
    避免再次询问菜名（此前该场景会落入 LLM 输出 [COOKING:START] 重新询问）。
    """
    recipe_service = get_recipe_service()
    candidates: list[str] = []
    try:
        candidates.extend(reversed(list(getattr(state, "recent_user_texts", []))))
    except Exception:
        pass
    try:
        turns = state.context_engine.get_recent_turns(getattr(state, "session_id", "default"), n=3)
        candidates.extend(turn.user_message for turn in reversed(turns) if getattr(turn, "user_message", ""))
        # 用户回复"需要/好"确认的往往是 AI 上一条提议的菜（如"好的，那我们做【西红柿炒鸡蛋】…"），
        # 因此最近的 AI 消息也纳入候选
        candidates.extend(
            turn.assistant_message for turn in reversed(turns) if getattr(turn, "assistant_message", "")
        )
    except Exception as exc:
        backend_log(f"[做饭] 读取最近对话历史失败: {exc}")

    def _local_hit(recipe: Any) -> dict[str, Any] | None:
        name = getattr(recipe, "name", "") or ""
        if name and not cooking_core._is_generic_cooking_entry_candidate(name):
            return cooking_core.recipe_to_dict(recipe)
        return None

    # 最高优先级：AI 提议菜名的两种写法——【菜名】全角括号（旧模板遗留）与
    # Markdown 标题（system.md 现硬性要求 "## 菜品名称"），用户回复"需要"确认的就是它；
    # 仅做本地匹配，避免回退到用确认语远程胡编菜谱
    for cand in candidates:
        dish_names = re.findall(r"【([^【】]{1,20})】", cand)
        dish_names.extend(
            m.strip() for m in re.findall(r"^#{1,3}\s*(\S[^\n]{0,30})$", cand, re.MULTILINE)
        )
        for dish in dish_names:
            dish = dish.strip()
            if not dish or cooking_core._is_generic_cooking_entry_candidate(dish):
                continue
            try:
                for recipe in recipe_service.search_by_name(dish, limit=1):
                    hit = _local_hit(recipe)
                    if hit:
                        return hit
            except Exception:
                continue

    # 1) 本地精确匹配
    for user_msg in candidates:
        if not user_msg or not user_msg.strip():
            continue
        try:
            matches = cooking_core._find_direct_recipe_matches(recipe_service, user_msg)
        except Exception:
            continue
        for m in matches:
            hit = _local_hit(m)
            if hit:
                return hit

    # 2) 本地模糊匹配（提取菜名候选后搜索）
    for user_msg in candidates:
        candidate = cooking_core._extract_recipe_name_candidate(user_msg)
        if not candidate or cooking_core._is_generic_cooking_entry_candidate(candidate):
            continue
        try:
            fuzzy = recipe_service.search_by_name(candidate, limit=3)
        except Exception:
            continue
        for m in fuzzy:
            hit = _local_hit(m)
            if hit:
                return hit

    # 3) 远程 LLM 生成（兜底：本地库没有该菜名时，如"番茄炒鸡蛋"）。
    # 仅对带菜谱特征（炒/煮/食材名等）的候选发起，避免"需要/炒菜吧"这类
    # 确认语、口语词被当菜名远程胡编出一道内容错乱的菜谱
    for user_msg in candidates:
        candidate = cooking_core._extract_recipe_name_candidate(user_msg)
        if not candidate or cooking_core._is_generic_cooking_entry_candidate(candidate):
            continue
        if not cooking_core._looks_like_recipe_request(candidate):
            continue
        try:
            remote = await cooking_core.lookup_remote_recipe(runtime, candidate)
        except Exception as exc:
            backend_log(f"[做饭] 远程菜谱生成失败: {exc}")
            remote = None
        if remote:
            return remote
    return None


# Bug4：视为"进行中"的状态——处于这些状态且已有菜谱时，control_start_cooking 不再无条件重置
# （AWAITING_SERVINGS 已随份数问答一起移除：菜谱确定后直接进入 COOKING）
COOKING_ACTIVE_STATES = ("COOKING", "STEP_WAITING")
# 明确要求重开/换菜/退出的意图词：命中才允许在流程进行中重置状态机
COOKING_RESTART_INTENT_KEYWORDS = ("重新开始", "重来", "重做", "换个菜", "换一道", "换一个", "再来一道", "退出")


async def _start_cooking_immediately(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    recipe: dict[str, Any],
    intro: str,
    request_id: str | None = None,
) -> None:
    """菜谱确定后跳过份数询问与食材用量计算，直接进入 COOKING 第 1 步并播报。

    （2026-09-06 需求：移除智能计算食材功能，不询问几人份，说菜名即开做。）
    三处入口共用：control_select_recipe（推荐/面板选菜）、_apply_remote_recipe（远程生成/
    步骤引导确认）、菜名请求直查分支。首步播报前照常走自动下料检测。
    """
    state.current_recipe = recipe
    state.current_step = 1
    state.current_servings = None
    state.awaiting_servings = False
    state.awaiting_ingredients_ready = False
    state.cooking_enabled = True
    state.cooking_state = "COOKING"
    state.completed_steps = []
    await cooking_core.send_cooking_status(ws, state)
    first_step_text = await _maybe_auto_dispense(
        state,
        f"{intro}\n\n" + cooking_core.build_step_guidance_text(state),
    )
    await cooking_core.send_cooking_text_with_tts(
        runtime,
        ws,
        state,
        first_step_text,
        request_id=request_id,
    )

async def _maybe_auto_dispense(state: SessionState, guidance_text: str) -> str:
    """进入新步骤时，若引导文本含明确的 'N 克/g' 调料投放量则自动启动下料机。

    返回可能追加了下料提示的文本（未触发/启动失败原样返回）。
    仅在 COOKING 态生效；启动超时/串口未就绪只记日志，不打断步骤播报。
    克数提取交给 cooking_core.extract_dispense_grams（遍历全部匹配 + 调料词相邻优先 +
    食材量排除），修复了原 search() 只取第一个匹配、后续调料克数被漏的缺口。
    """
    if state.cooking_state != "COOKING":
        return guidance_text
    grams = cooking_core.extract_dispense_grams(guidance_text or "")
    if grams is None:
        return guidance_text
    try:
        # start_dispense 内部等串口就绪最长 15s——包超时避免卡住步骤播报
        result = await asyncio.wait_for(dispense_module.start_dispense(grams), timeout=4.0)
    except Exception as exc:
        backend_log(f"[做饭] 步骤自动下料启动: {exc}")
        return guidance_text
    if result.get("success"):
        backend_log(f"[做饭] 步骤自动下料已启动: {grams:g}g")
        return f"{guidance_text}\n\n（已启动自动下料 {grams:g} 克，够了我自动停。）"
    backend_log(f"[做饭] 步骤自动下料未启动: {result.get('error')}")
    return guidance_text


async def _maybe_dispense_recommended_amount(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    question: str,
    answer_text: str,
) -> None:
    """用量问答自动下料：菜谱没写清调料用量、用户询问后 AI 给出克数建议 → 捕获克数接通下料机。

    触发条件（全部满足）：COOKING/STEP_WAITING 进行中 + 用户问的是调料用量 +
    AI 回答含调料克数建议 + 当前没有下料在进行（不打断进行中的投放）。
    须在 handle_cooking_question 的问答气泡定稿后调用——确认语走独立的 cook- 气泡，
    不复用问答 request_id（该气泡已 assistant_final 定稿）。
    """
    if state.cooking_state not in ("COOKING", "STEP_WAITING"):
        return
    if not cooking_core.looks_like_seasoning_amount_question(question):
        return
    grams = cooking_core.extract_recommended_dispense_grams(answer_text or "")
    if grams is None:
        return
    try:
        if dispense_module.get_dispense_status().get("active"):
            backend_log("[做饭] 用量问答自动下料跳过：已有下料进行中")
            return
    except Exception:
        pass
    try:
        # start_dispense 内部等串口就绪最长 15s——包超时避免卡住问答收尾
        result = await asyncio.wait_for(dispense_module.start_dispense(grams), timeout=4.0)
    except Exception as exc:
        backend_log(f"[做饭] 用量问答自动下料启动: {exc}")
        result = {"success": False, "error": "下料机响应超时"}
    if result.get("success"):
        backend_log(f"[做饭] 用量问答自动下料已启动: {grams:g}g（问答建议）")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"已按建议启动自动下料 {grams:g} 克，够了我自动停。",
        )
    else:
        backend_log(f"[做饭] 用量问答自动下料未启动: {result.get('error')}")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"下料机没有就绪：{result.get('error') or '未知原因'}，你可以按刚才说的克数手动放。",
        )


async def handle_explicit_dispense_command(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    grams: float,
    request_id: str | None = None,
) -> None:
    """显式下料指令执行端（"下3克盐/放5克糖"）：直接驱动下料机，不走 LLM/工具标记。

    由 handle_user_text 的确定性拦截层调用（parse_dispense_command 命中），不依赖
    做饭模式开关——下料机是独立硬件，IDLE 态也要能响应。确认语复用用户消息的
    request_id，前端归到同一条用户消息的回复气泡。
    """
    try:
        # start_dispense 内部等串口就绪最长 15s——包超时避免卡住回复链
        result = await asyncio.wait_for(dispense_module.start_dispense(grams), timeout=6.0)
    except Exception as exc:
        backend_log(f"[做饭] 显式下料指令启动: {exc}")
        result = {"success": False, "error": "下料机响应超时"}
    if result.get("success"):
        backend_log(f"[做饭] 显式下料指令已启动: {grams:g}g")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"好的，开始自动下料 {grams:g} 克，够了我自动停。",
            request_id=request_id,
        )
    else:
        backend_log(f"[做饭] 显式下料指令未启动: {result.get('error')}")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"下料机没有就绪：{result.get('error') or '未知原因'}，请检查下料机连接。",
            request_id=request_id,
        )


# ===== 盐量脉冲链路（2026-09-05，独立于用量问答/显式指令） =====
# 用户问"需要放多少盐"→按秤上食材克重静默计算（不播报过程）→提示放置锅铲
# →用户"好了"→回复"调料出口已开启"并通电 3 秒脉冲出料→断电后播报"调料出口已关闭"。
# 盐量经验比例：食材重量的 0.3%~0.5%（用户指定），计算取中值 0.4%（300g 食材≈1.2g 盐），
# 结果按 0.5~100g 截断。
# ⚠️ 以下盐量脉冲常量/函数**已于 2026-09-16 停用**（用户要求：放盐整条链路改由大模型调用
# MCP 工具完成）。`llm_handler` 里的触发点与"好了"确认门都已删除，"要放多少盐 / 我准备放
# 调料了"现在落到普通 LLM 问答 → 模型调 get_weight 算量 → 用户确认后调
# dispense_start(pulse_seconds=3) + dispense_stop 出料（见 llm_tools.SALT_FLOW_HINT）。
# 这里保留定义只是为了让既有单测（tests/test_salt_pulse.py）继续可用，无任何生产调用路径。
SALT_RATIO_MIN = 0.003
SALT_RATIO_MAX = 0.005
SALT_RATIO_OF_INGREDIENT_WEIGHT = 0.004  # 0.3%~0.5% 取中值
SALT_PULSE_SECONDS = 3.0
SALT_PULSE_CONFIRM_EXPIRE_S = 600.0  # 确认门 10 分钟过期，防陈旧标志劫持后续"好了"
# 短确认词：整句或 ≤6 字包含即确认（"好了"）
SALT_PULSE_CONFIRM_WORDS = ("好", "好了", "好的", "好啦", "行", "行吧", "可以", "确认",
                            "嗯", "嗯嗯", "对", "是", "ok", "okay")
# 长确认短语（2026-09-05 用户脚本"准备好了，给我放盐巴"）：子串命中即确认（前置否定词守卫在门逻辑里）
SALT_PULSE_CONFIRM_PHRASES = ("准备好了", "给我放盐巴", "放盐巴", "放盐吧", "开始放盐", "放吧", "放调料吧")
SALT_PULSE_CANCEL_WORDS = ("不", "不用", "不要", "取消", "算了", "别", "暂不", "先不")
# 缓一词（"还没准备好/等一下"）：保持确认门继续等待，不清标志
SALT_PULSE_WAIT_WORDS = ("还没", "没好", "没准备", "等一下", "等一会", "稍等", "等等")
_SALT_CALC_CUES = ("多少", "几克", "几g", "用量", "计算")


def looks_like_salt_calc_request(text: str) -> bool:
    """用户是否想触发盐量脉冲出料。两类说法（2026-09-05 用户场景）：
    ①问盐量："需要放多少盐/帮我计算需要多少克盐巴"（盐 + 用量 cue）；
    ②备调料："我准备放调料了请帮我准备一下调料"（调料 + 准备/帮我/放调料，
      排除"为什么要放调料/怎么配调料"类知识问句）。
    带明确克数的显式指令（"下3克盐"）由更高优先级的 parse_dispense_command 处理。
    """
    normalized = (text or "").strip()
    if not normalized:
        return False
    if "调料" in normalized and any(k in normalized for k in ("准备", "帮我", "放调料")):
        if not any(q in normalized for q in ("为什么", "怎么", "吗", "么", "呢", "嘛", "？", "?")):
            return True
    if "盐" not in normalized:
        return False
    return any(c in normalized for c in _SALT_CALC_CUES)


async def handle_salt_calc_request(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    request_id: str | None = None,
) -> None:
    """盐量脉冲第一步：读秤计算 → 按脚本播报（含食材克重与配比盐量）→ 进入"好了"确认门。"""
    from ..weight import get_weight
    try:
        data = await asyncio.to_thread(get_weight)
    except Exception as exc:
        backend_log(f"[盐量脉冲] 读秤失败: {exc}")
        data = None
    weight = float((data or {}).get("weight") or 0.0)
    grams = round(min(100.0, max(0.5, weight * SALT_RATIO_OF_INGREDIENT_WEIGHT)), 1)
    state.awaiting_salt_pulse_confirmation = True
    state.salt_pulse_grams = grams
    state.salt_pulse_requested_at = time.time()
    backend_log(f"[盐量脉冲] 秤上食材 {weight:g}g → 计算盐量 {grams:g}g（0.3%~0.5% 取中值 {SALT_RATIO_OF_INGREDIENT_WEIGHT:.1%}），等待锅铲就位确认")
    await cooking_core.send_cooking_text_with_tts(
        runtime, ws, state,
        f"根据现在计算的食材 {weight:g} 克，计算出来需配比盐 {grams:g} 克，请把锅铲放到出料口，准备放盐巴",
        request_id=request_id,
    )


async def handle_salt_pulse_confirm(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    request_id: str | None = None,
) -> None:
    """盐量脉冲第二步："好了"确认 → 回复"调料出口已开启"+通电 3 秒→断电→播报"调料出口已关闭"。

    脉冲计时用独立任务承载：send_cooking_text_with_tts 因 TTS 队列背压可能阻塞，
    不能让 1 秒定时跟着播报被拖长（见 gs-tts-send-blocks-state-race 同款竞态教训）。
    """
    grams = getattr(state, "salt_pulse_grams", 0.0)
    state.awaiting_salt_pulse_confirmation = False
    # 互斥预检（2026-09-15）：下料机被闭环下料占用时直接拒绝并说明，不能先播报
    # "调料出口已打开"——那和实际没打开矛盾（旧实现还会把对方的下料无声掐断）。
    busy = dispense_module.dispense_busy_reason()
    if busy:
        backend_log(f"[盐量脉冲] 拒绝出料：{busy}")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state, busy, request_id=request_id,
        )
        return
    pulse_task = asyncio.create_task(dispense_module.pulse_dispense(SALT_PULSE_SECONDS))
    await cooking_core.send_cooking_text_with_tts(
        runtime, ws, state,
        "调料出口已打开",
        request_id=request_id,
    )
    result = await pulse_task
    if result.get("success"):
        backend_log(f"[盐量脉冲] 通电 {SALT_PULSE_SECONDS:g}s 出料完成（计算盐量 {grams:g}g），调料出口已关闭")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            "调料出口已关闭",
        )
    else:
        backend_log(f"[盐量脉冲] 脉冲失败: {result.get('error')}")
        await cooking_core.send_cooking_text_with_tts(
            runtime, ws, state,
            f"调料出口打开失败：{result.get('error') or '未知原因'}，请检查下料机连接。",
        )


async def control_start_cooking(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    payload = payload or {}
    # Bug4：语音误触（含"做饭"二字的无关语句也会触发 cooking start）不应清空进行中的流程。
    # 仅当用户明确表达重开/换菜/退出意图，或消息方显式要求强制重置时才重置。
    restart_intent = bool(payload.get("force") or payload.get("restart"))
    raw_text = str(payload.get("text") or "")
    if not restart_intent and raw_text:
        restart_intent = any(kw in raw_text for kw in COOKING_RESTART_INTENT_KEYWORDS)

    if (
        not restart_intent
        and state.current_recipe
        and state.cooking_state in COOKING_ACTIVE_STATES
    ):
        recipe_name = (state.current_recipe or {}).get("name") or "当前菜品"
        resume_text = (
            f"我们正在做「{recipe_name}」，咱们接着做。\n\n"
            + cooking_core.build_step_guidance_text(state)
        )
        backend_log(
            f"[做饭] 流程进行中收到 start，保留进度不重置: state={state.cooking_state}, "
            f"recipe={recipe_name}, step={state.current_step}"
        )
        await cooking_core.send_cooking_status(ws, state)
        await cooking_core.send_cooking_text_with_tts(runtime, ws, state, resume_text)
        return

    cooking_core._reset_cooking_session_state(state)
    await cooking_core.send_cooking_status(ws, state)
    await cooking_core.send_cooking_text_with_tts(
        runtime,
        ws,
        state,
        "好的，我们开始步骤式引导做饭，请问您有什么想做的，或者菜品名称是什么？还是说您有什么菜？",
        request_id=None,
    )


async def control_stop_cooking(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    del payload
    from ..oil_watch import stop_oil_watch
    stop_oil_watch(state)  # 退出做饭时同步停止油温监测
    state.cooking_enabled = False  # 这是做饭模式的总开关。设为 False 后，后端所有地方检测到这个值都会跳过做饭逻辑，回到普通问答
    state.cooking_state = "IDLE"  # ← 状态机切换到"空闲"  ⭐ 关键点
    state.current_recipe = None  # ← 清空之前选过的菜谱  ⭐ 关键点
    state.current_step = 0  # ← 游标设为 0
    state.completed_steps = []  # ← 已完成清单清空
    state.user_ingredients = []  # 用户之前告诉系统的食材（如"我有土豆和鸡蛋"）清空。下次开做要重新收集
    state.last_recommended_recipe_names = []  # ← 上次推荐过的菜名清空（避免重复推）
    state.current_servings = None  # ← 份数清空（还没告诉系统几人份）
    state.awaiting_servings = False  # ← 关掉"等份数"标记
    state.awaiting_cooking_exit_confirmation = False  # ← 关掉"等退出确认"标记
    state.awaiting_step_guide_confirmation = False  # ← 关掉"等步骤引导确认"标记
    # Bug2：退出时清零食材就绪等待标志，避免退出后一句含"好"的话被"等食材确认"分支劫持
    state.awaiting_ingredients_ready = False
    state.pending_guide_recipe = None

    # 只取消本会话由做饭助手创建的倒计时（此前全局取消会清掉其他会话的定时器）
    timer_manager.cancel_all_timers(category="cooking", session_id=getattr(state, "session_id", "default"))


    await cooking_core.send_cooking_status(ws, state)


async def control_add_ingredients(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    ingredients = payload.get("ingredients", [])
    state.user_ingredients.extend(ingredients)
    state.user_ingredients = list(dict.fromkeys(state.user_ingredients))
    recipe_service = get_recipe_service()
    recipes_payload = cooking_core._build_related_recipe_recommendations(recipe_service, state, limit=12)
    state.cooking_state = "RECOMMENDING" if recipes_payload else "COLLECTING_INGREDIENTS"
    await cooking_core.send_cooking_status(ws, state, recipes=recipes_payload)
    if recipes_payload:
        cooking_core._store_last_recommended_recipe_names(state, recipes_payload)
        recipe_names = "、".join(item["name"] for item in recipes_payload[:5])
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            f"收到，您现在有：{', '.join(state.user_ingredients)}。\n\n我先给您推荐几道适合的菜：{recipe_names}。\n\n如果您已经想好了，直接说菜名就可以，我马上带你一步一步做。",
        )
    elif len(ingredients) > 0:
        state.last_recommended_recipe_names = []
        top_recipes = cooking_core.get_top_recipes(10)
        top_recipes_payload = [cooking_core.recipe_search_result_to_dict(item) for item in top_recipes]
        state.cooking_state = "RECOMMENDING"
        cooking_core._store_last_recommended_recipe_names(state, top_recipes_payload)
        await cooking_core.send_cooking_status(ws, state, recipes=top_recipes_payload)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "我暂时没匹配到和这些食材直接对应的菜谱，先给你推荐前十个评分比较高的菜谱。你可以直接选一个。",
        )


async def control_select_recipe(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    recipe_name = payload.get("recipe_name", "")
    recipe_service = get_recipe_service()
    recipe = cooking_core.recipe_to_dict(recipe_service.get_recipe(recipe_name))
    if recipe:
        # 不再询问几人份/智能计算食材，选中即直接开始第 1 步
        await _start_cooking_immediately(
            runtime,
            ws,
            state,
            recipe,
            f"好的，我们开始做「{recipe['name']}」！",
        )
    else:
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            f"抱歉，没有找到名为「{recipe_name}」的菜谱，请重新选择。",
        )


async def control_confirm_step(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    del payload
    if not state.current_recipe:# current_recipe 为空，说明用户还没有选择菜谱
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "您还没有选择菜谱呢，请先告诉我您想做什么菜！",
        )
        return

    steps = state.current_recipe.get("steps", [])#已经有了菜谱，直接获取步骤
    if state.current_step >= len(steps):#current_step是当前步骤的索引，大于等于步骤数量，说明所有步骤都已完成
        await cooking_core.send_cooking_text_with_tts(runtime, ws, state, "恭喜！所有步骤都已完成！🎉")
        return

    if state.current_step not in state.completed_steps:# 如果当前步骤不在已完成清单中，说明是第一次确认这一步
        # 把这一步添加到已完成清单中
        state.completed_steps.append(state.current_step)

    next_step = state.current_step + 1# 下一步的索引是当前步骤的索引加 1

    if next_step >= len(steps):# 如果下一步的索引大于等于步骤数量，说明所有步骤都已完成
        state.cooking_state = "FINISHED"
        await cooking_core.send_cooking_status(ws, state)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            f"太棒了！🎉 您的 {state.current_recipe['name']} 做好了！\n\n✨ 小贴士：\n- 趁热吃风味更佳\n- 如果吃不完可以放冰箱保存\n\n感谢使用做饭助手！可以继续教您做其他菜哦~",
        )
    else:
        state.current_step = next_step# 更新当前步骤为下一步
        await cooking_core.send_cooking_status(ws, state)
        guidance_text = await _maybe_auto_dispense(
            state,
            cooking_core.build_step_guidance_text(
                state, intro="太棒了！🎉 这一步完成。", include_vision_observation=False
            ),
        )
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            guidance_text,
        )


async def control_skip_step(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    del payload
    if not state.current_recipe:
        return
    steps = state.current_recipe.get("steps", [])
    next_step = state.current_step + 1
    if next_step <= len(steps):
        state.current_step = next_step
        await cooking_core.send_cooking_status(ws, state)
        guidance_text = await _maybe_auto_dispense(
            state,
            cooking_core.build_step_guidance_text(state, intro="好的，这一步先跳过。"),
        )
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            guidance_text,
        )


async def control_start_timer(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    timer_req: dict,
    request_id: str | None = None,
) -> None:
    """启动一个倒计时"""
    seconds = int(timer_req.get("seconds", 60))
    description = timer_req.get("description", "烹饪定时")

    backend_log(f"[control_start_timer] 启动倒计时: description={description}, seconds={seconds}, request_id={request_id}")

    # 使用统一的 TimerManager 创建倒计时（挂到本会话名下，避免跨会话串台）
    timer_id = create_countdown(
        description=description,
        seconds=seconds,
        category="cooking",
        metadata={"request_id": request_id},
        on_complete=lambda timer: asyncio.create_task(
            _on_timer_complete_callback(runtime, ws, state, timer)
        ),
        session_id=getattr(state, "session_id", "default"),
    )

    await cooking_core.send_cooking_status(ws, state)

    duration_text = cooking_core.format_timer_duration(seconds)
    await cooking_core.send_cooking_text_with_tts(
        runtime,
        ws,
        state,
        f"好的，{duration_text}后提醒您「{description}」。",
        request_id=request_id,
    )

async def _on_timer_complete_callback(runtime: Any, ws: WebSocket, state: SessionState, timer: Any) -> None:
    """定时器完成时的回调函数"""
    # 连接已断开（页面关闭/刷新/断网）：整条播报链路直接放弃。
    # 不检查的话会走完 TTS 合成、再逐块撞上"WS 忽略"刷几十条日志，
    # 还白白烧一次 TTS 配额；倒计时不做跨断线补送（session_id 共享，不能乱取消）。
    # 注意：WebSocketState 在 starlette.websockets 里，不在 fastapi 顶层（fastapi 只 re-export WebSocket）
    from starlette.websockets import WebSocketState

    if getattr(ws, "client_state", None) != WebSocketState.CONNECTED:
        backend_log(f"[Timer] 倒计时 {timer.id} 到点但连接已断开，跳过播报")
        return

    # 状态与清理事件先行：前端倒计时卡片立即消失，不受播报缓冲影响
    await cooking_core.send_cooking_status(ws, state)

    message = f"⏰ 定时器提醒：{timer.description} 时间到了！"
    backend_log(f"[Timer] 倒计时完成: {timer.id} - {timer.description}")

    # 发送通知
    await transport_ws.ws_send(ws, "timer_completed", {
        "timer": timer.to_dict(),
        "message": message
    })

    # 前端 useWebSocket 只监听 timer_done 做本地清理（timer_completed 不在其分发表），
    # 补发一份避免倒计时卡片滞留在 00:00
    await transport_ws.ws_send(ws, "timer_done", {
        "id": timer.id,
        "description": timer.description,
        "total_seconds": timer.total_seconds,
    })

    # 播报前缓冲 2 秒：与正在进行的其他 TTS 播报拉开间隔，避免播报冲突
    await asyncio.sleep(2.0)

    # 播放语音提醒
    # interrupted 残留无需在此显式清除：send_cooking_text_with_tts 入口已统一
    # state.interrupted = False（主动播报即新指令周期），本回调是 fire-and-forget
    # 任务、不经过 handle_user_text 的复位路径，入口清法正好覆盖；即便用户在上方
    # 2 秒缓冲期打断过 AI，本次到点提醒仍能正常发出（播报进行中打断则靠函数内
    # 逐段检查停止，不受影响）
    await cooking_core.send_cooking_text_with_tts(
        runtime,
        ws,
        state,
        message,
    )

    # 播报完成后再缓冲 2 秒，才放行后续任务（如步骤引导）的 TTS 播报
    await asyncio.sleep(2.0)


async def control_cancel_timer(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    """取消本会话由做饭助手创建的倒计时"""
    del payload

    # 在 TimerManager 中取消本会话对应的定时器（不影响其他会话）
    count = timer_manager.cancel_all_timers(
        category="cooking",
        session_id=getattr(state, "session_id", "default"),
    )

    if count == 0:
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "当前没有正在进行的倒计时哦。",
        )
        return

    await cooking_core.send_cooking_status(ws, state)
    await cooking_core.send_cooking_text_with_tts(
        runtime,
        ws,
        state,
        f"好的，已取消全部 {count} 个倒计时。",
    )


async def handle_cooking_question(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    jpeg_b64: str,
    temperature: dict | None,
    weight: dict | None,
    tts_provider: str | None,
    tts_model: str | None,
    tts_api_key: str | None,
    request_id: str | None = None,
) -> None:
    recipe = state.current_recipe or {}
    steps = recipe.get("steps") or []
    current_step = max(state.current_step, 1)
    current_step_data = steps[current_step - 1] if 0 < current_step <= len(steps) else {}
    custom_prompt_block = f"\n个性化提示词：\n{state.custom_prompt.strip()}\n" if state.custom_prompt.strip() else ""
    
    # 使用上下文引擎构建压缩后的对话历史
    chat_history = state.context_engine.build_history(
        session_id=getattr(state, 'session_id', 'default'),
        max_tokens=1500,  # 做饭场景留更多token给状态信息
        scene_type="cooking",
        extra_context={
            "recipe": recipe,
            "step": current_step,
            "total_steps": len(steps),
            "servings": state.current_servings,
        }
    )
    
    # 构建已完成步骤信息
    completed_steps_info = ""
    if state.completed_steps:
        completed_steps_info = "已完成的步骤：\n"
        for idx, step_num in enumerate(state.completed_steps[:10], 1):  # 最多显示10步
            if 0 < step_num <= len(steps):
                step_data = steps[step_num - 1]
                completed_steps_info += f"  第{step_num}步：{step_data.get('title', '未命名')}\n"

    prompt = (
        f"{cooking_core.COOKING_SYSTEM_PROMPT}\n\n"
        f"{custom_prompt_block}"
        f"当前菜谱：{recipe.get('name', '未选择')}\n"
        f"当前步骤：{current_step}/{len(steps) or 0}\n"
        f"当前份数：{state.current_servings or '未确认'}\n"
        f"步骤标题：{current_step_data.get('title') or '未命名步骤'}\n"
        f"步骤说明：{current_step_data.get('content') or '暂无'}\n"
        f"{completed_steps_info}"
        f"温度数据：{temperature or '暂无'}\n"
        f"重量数据：{weight or '暂无'}\n\n"
        f"请围绕当前做饭步骤回答用户问题，给出简洁、可执行的中文指导。\n"
        f"若用户在询问某味调料或食材的用量，而当前菜谱步骤没有给出明确克数，"
        f"你必须直接给出明确、单一的克数建议（例如：建议放 3 克盐），不要只回答\"适量\"或给多个区间。\n"
        f"用户问题：{text}\n\n"
        f"请直接回答："
    )
    
    # 调用LLM并获取回复
    final = await transport_ws.handle_llm_response(
        runtime,
        ws,
        state,
        prompt,
        text,
        jpeg_b64,
        tts_provider,
        tts_model,
        tts_api_key,
        request_id=request_id,
        history=chat_history,
    )
    
    # 记录助手回复到上下文引擎
    if final:
        final_text = final[0] if isinstance(final, tuple) else final
        if final_text:
            state.context_engine.record_assistant_message(
                session_id=getattr(state, 'session_id', 'default'),
                content=final_text
            )
            # 用量问答自动下料：用户问调料用量、AI 给出克数建议 → 捕获克数接通下料机
            # （放在最后：确认语 TTS 有队列背压可能阻塞，不能挡住前面的回复链路）
            try:
                await _maybe_dispense_recommended_amount(runtime, ws, state, text, final_text)
            except Exception as exc:
                backend_log(f"[做饭] 用量问答自动下料异常: {exc}")


async def _apply_remote_recipe(runtime: Any, ws: WebSocket, state: SessionState, recipe: dict[str, Any], request_id: str | None, is_creative: bool = False) -> None:
    # 不再询问几人份/智能计算食材，菜谱生成后直接开始第 1 步
    intro = f"好的，我们开始做「{recipe.get('name') or '这道菜'}」！"
    if is_creative:
        intro = f"【创意生成菜谱】\n\n{intro}"
    await _start_cooking_immediately(runtime, ws, state, recipe, intro, request_id=request_id)


# ===== AI 意图分类兜底 =====
# 位置：handle_cooking_user_text() 所有硬编码分支走完、即将调用 handle_cooking_question()
# 之前。只在关键词匹配全失败时触发一次轻量 LLM 分类（正常路径零额外延迟），AI 只负责
# "听懂人话"，执行仍走代码已有的 control_* 函数；分类失败/低置信度自动放行原问答行为，
# 不会比没有兜底更差。settings.ai_intent_fallback_enabled 可一键关闭。

AI_INTENT_FALLBACK_THRESHOLD = 0.7
# 实测分类延迟随服务波动在 1~5+ 秒（5s 窗口曾出现压线超时静默失效），放宽到 8s；
# 超时只是回退原问答行为，不影响正确性，宁可多等也不要静默丢兜底
AI_INTENT_FALLBACK_TIMEOUT = 8.0

# 插入点可达状态：cooking_enabled=True、current_recipe 存在、各 awaiting_* 标志已被
# 上游分支消费（各自提前 return），因此退出确认、步骤引导确认、换一批等意图在此
# 结构性不可达，不放进分类空间，避免模型往不可达意图上输出。
AI_INTENT_FALLBACK_INTENTS = (
    "confirm_step", "skip_step", "exit_request",
    "start_cooking", "select_recipe", "add_ingredients",
    "ask_question", "unknown",
)


def _build_intent_classification_prompt(state: SessionState, text: str) -> str:
    """构建意图分类提示词：注入当前状态快照 + 限定输出空间 + 给足示例。"""
    recipe = state.current_recipe or {}
    steps = recipe.get("steps") or []
    status_snapshot = "\n".join(
        (
            f"- cooking_enabled: {state.cooking_enabled}",
            f"- cooking_state: {state.cooking_state}（COOKING=正在按步骤做菜；STEP_WAITING=步骤等待中；"
            f"FINISHED=全部步骤已完成；RECOMMENDING=推荐选菜中）",
            f"- current_recipe: {recipe.get('name') or '无'}",
            f"- 当前进度: 第 {state.current_step}/{len(steps)} 步",
        )
    )
    return f"""你是一个厨房语音助手的意图分类器。系统的关键词匹配没有识别出下面这句话的意图，请你判断它最可能是哪种意图。

## 当前系统状态
{status_snapshot}

## 用户输入
"{text}"

## 可选意图（只能从中选一个）
- confirm_step：用户表示当前步骤已完成、想进行下一步。示例："好了"、"搞定了"、"这步完事了"、"这步OK了"、"差不多可以了"、"完工了"
- skip_step：用户要求跳过当前步骤。示例："这步算了"、"先不弄这个"、"这步先不做了"、"家里没这个做不了"
- exit_request：用户想退出做饭引导模式。示例："先到这吧"、"今天就做这么多"、"做不动了"、"先这样吧"
- start_cooking：用户想重新开始或再做一道菜。示例："再来一个"、"重新做"、"我还想再做道菜"
- select_recipe：用户点名了另一道想做的菜。parsed_value 填菜名。示例："换成红烧肉"、"我想改做鱼香肉丝"
- add_ingredients：用户在补充说明自己有/买了什么食材。示例："我这还有点葱花"、"我又买了斤排骨"
- ask_question：用户在问烹饪相关的问题（做法、火候、调料、时间等），需要 AI 回答。示例："为什么要焯水"、"火开多大"、"盐放多了怎么办"
- unknown：无法判断，或以上都不符合

## 输出要求
严格输出 JSON，不要任何其他文字、不要 markdown 代码块：
{{"intent": "意图名称", "confidence": 0.0到1.0的置信度, "parsed_value": "如果意图需要参数（如菜名）填这里，否则填空字符串"}}

## 注意
1. 必须结合当前系统状态判断
2. 用户输入如果明显是在问问题，优先分类为 ask_question
3. 问句里带"好了/行了"的（如"这步要几分钟才好"）是提问，不是确认
4. 不确定时 confidence 给低一点，不要强行分类
"""


async def classify_cooking_intent_ai(
    runtime: Any,
    state: SessionState,
    text: str,
    timeout: float = AI_INTENT_FALLBACK_TIMEOUT,
) -> dict | None:
    """AI 意图分类兜底。代码关键词匹配全失败时调用。

    复用 cooking_core._collect_llm_text（关闭思考、带超时）+ _extract_json_object，
    返回 {"intent": ..., "confidence": 0.xx, "parsed_value": ...}；超时/失败/格式错误返回 None。
    """
    prompt = _build_intent_classification_prompt(state, text)
    try:
        raw = await asyncio.wait_for(
            cooking_core._collect_llm_text(
                runtime,
                prompt,
                system_prompt="你是意图分类器，严格只输出 JSON，不要思考过程。",
                enable_thinking=False,
                timeout_seconds=timeout,
            ),
            timeout=timeout + 1.0,
        )
    except Exception as exc:
        backend_log(f"[AI兜底] 意图分类失败: {exc}")
        return None
    if not raw:
        backend_log(f"[AI兜底] 意图分类超时或无输出，放行普通问答: '{text[:30]}'")
        return None
    parsed = cooking_core._extract_json_object(raw)
    if not parsed or not isinstance(parsed.get("intent"), str):
        backend_log(f"[AI兜底] 分类结果无法解析为 JSON: {raw[:200]}")
        return None
    try:
        parsed["confidence"] = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.0
    parsed["intent"] = parsed["intent"].strip()
    parsed["parsed_value"] = str(parsed.get("parsed_value") or "").strip()
    return parsed


async def _ai_intent_fallback(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    normalized_text: str,
    request_id: str | None = None,
) -> bool:
    """AI 意图兜底 dispatch：命中高置信度控制意图才接管执行。

    返回 True 表示已接管（调用方应 return True）；False 表示放行 handle_cooking_question（原行为）。
    """
    result = await classify_cooking_intent_ai(runtime, state, normalized_text)
    if not result:
        return False
    intent = result["intent"]
    confidence = result["confidence"]
    parsed_value = result.get("parsed_value", "")
    log_prefix = f"[AI兜底] text='{normalized_text}' -> {intent} (conf={confidence:.2f})"

    if intent not in AI_INTENT_FALLBACK_INTENTS or confidence < AI_INTENT_FALLBACK_THRESHOLD:
        backend_log(f"{log_prefix} [未执行，低置信度/未知意图]")
        return False

    # 状态守卫：做菜流程进行中不允许 add_ingredients 接管——control_add_ingredients 会把
    # cooking_state 切成 RECOMMENDING 并重新推荐菜，直接打断正在进行的步骤引导
    if intent == "add_ingredients" and state.cooking_state in ("COOKING", "STEP_WAITING", "AWAITING_SERVINGS"):
        backend_log(f"{log_prefix} [未执行，流程进行中不允许 add_ingredients]")
        return False

    backend_log(f"{log_prefix} [执行]")

    if intent == "confirm_step":
        await control_confirm_step(runtime, ws, state, {})
        return True
    if intent == "skip_step":
        await control_skip_step(runtime, ws, state, {})
        return True
    if intent == "exit_request":
        state.awaiting_cooking_exit_confirmation = True
        await cooking_core.send_cooking_status(ws, state)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "你是想退出做饭引导吗？如果确定要退出，请回复'是 / 对 / 确认'；如果不退出，请回复'继续 / 不用 / 取消'。",
            request_id=request_id,
        )
        return True
    if intent == "start_cooking":
        # 带上原文让 control_start_cooking 的重开意图关键词检测（Bug4 保护）继续生效
        await control_start_cooking(runtime, ws, state, {"text": normalized_text})
        return True
    if intent == "select_recipe":
        # parsed_value 是 AI 提取的菜名，代码再走菜谱库查证；查不到不放行换菜
        recipe_name = parsed_value or normalized_text
        recipe_service = get_recipe_service()
        matches = cooking_core._find_direct_recipe_matches(recipe_service, recipe_name)
        if not matches:
            matches = list(recipe_service.search_by_name(recipe_name, limit=1))
        if matches:
            await control_select_recipe(runtime, ws, state, {"recipe_name": getattr(matches[0], "name", "")})
            return True
        backend_log(f"[AI兜底] select_recipe 未在菜谱库找到「{recipe_name}」，放行问答")
        return False
    if intent == "add_ingredients":
        # parsed_value 只做参考，食材仍用现有解析器从原文提取验证
        ingredients = cooking_core.parse_ingredient_text(normalized_text)
        if ingredients:
            await control_add_ingredients(runtime, ws, state, {"ingredients": ingredients})
            return True
        backend_log("[AI兜底] add_ingredients 未从原文解析出食材，放行问答")
        return False
    # ask_question / unknown 落到这里：放行问答
    return False


async def handle_cooking_user_text(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    jpeg_b64: str,
    temperature: dict | None,
    weight: dict | None,
    tts_provider: str | None,
    tts_model: str | None,
    tts_api_key: str | None,
    request_id: str | None = None,
) -> bool:
    recipe_service = get_recipe_service()
    normalized_text = text.strip()
    if not normalized_text:
        return False

    # 记录最近用户消息（用于确认步骤式引导时回找菜名；即使被新消息打断未配对也不丢失）
    try:
        state.recent_user_texts.append(normalized_text)
    except Exception:
        pass

    # 使用上下文引擎记录用户消息（全局对话历史）
    state.context_engine.record_user_message(
        session_id=getattr(state, 'session_id', 'default'),
        content=normalized_text,
        scene_type="cooking",
        metadata={
            "cooking_state": state.cooking_state,
            "current_recipe": state.current_recipe.get('name') if state.current_recipe else None,
            "current_step": state.current_step,
        }
    )

    # 菜名请求检测（不区分是否已开启做饭模式）：
    # 用户明确报出菜名（如"教我做番茄炒蛋"/"番茄炒蛋怎么做"）时，
    # 统一进入"查菜谱+固定文案确认引导"流程；未开启做饭模式也不放行给 LLM 自由发挥。
    direct_recipe_candidate = cooking_core._extract_recipe_name_candidate(normalized_text)
    # 排除「饭/菜/做饭/烹饪」等通用进入语，避免「我要做饭」被误当成菜名「饭」而直接选中米饭
    if direct_recipe_candidate and cooking_core._is_generic_cooking_entry_candidate(direct_recipe_candidate):
        direct_recipe_candidate = ""
    direct_recipe_matches = cooking_core._find_direct_recipe_matches(recipe_service, normalized_text) if direct_recipe_candidate else []
    direct_recipe_request = bool(
        direct_recipe_candidate
        and (
            direct_recipe_matches
            or cooking_core._looks_like_recipe_request(normalized_text)
        )
    )

    if state.cooking_enabled:
        ingredient_list = cooking_core.parse_ingredient_text(normalized_text)
        matched_recipes = recipe_service.search_by_name(normalized_text, limit=5)
    else:
        ingredient_list = []
        matched_recipes = []

    # 流程短语误解析甄别：parse_ingredient_text 对不在黑名单里的 ≤12 字短语会整句当食材
    # （实测"这步完事了"→['这步完事了']），使 should_handle_as_cooking 恒为 True，
    # 关键词漏网的流程指令永远到不了下方 AI 意图兜底门控。步骤进行中（COOKING/STEP_WAITING）
    # 若"整句=单一解析结果"且不含食材清单表达特征（有/和/买），视为误解析剔除。
    if (
        state.cooking_state in ("COOKING", "STEP_WAITING")
        and state.current_recipe
        and len(ingredient_list) == 1
        and cooking_core.normalize_utterance(ingredient_list[0]) == cooking_core.normalize_utterance(normalized_text)
        and not any(kw in normalized_text for kw in ("有", "和", "买"))
    ):
        backend_log(f"[做饭] 进行中短句被误解析为食材，剔除参与流程判断: {ingredient_list}")
        ingredient_list = []

    # 问句甄别：非做饭语境的提问（"你现在手上有什么工具""现在几点了"）不是食材清单，
    # 剔除后 should_handle_as_cooking 为 False 落普通问答——工具层 function calling
    # 会让 LLM 如实回答"你有什么工具"这类元问题，做饭状态不受影响。
    # （实测 2026-09-11：收集态该问句被拆成 ['你现在手上','什么工具'] 添加并误推荐）
    if ingredient_list and cooking_core.looks_like_meta_question(normalized_text):
        backend_log(f"[做饭] 问句被误解析为食材，剔除改走问答: {ingredient_list}")
        ingredient_list = []

    backend_log(f"[调试] 输入: {normalized_text}, cooking_enabled={state.cooking_enabled}, 解析出的食材: {ingredient_list}")
    wants_cooking = any(keyword in normalized_text for keyword in cooking_core.COOKING_START_KEYWORDS)
    wants_unsure_help = any(keyword in normalized_text for keyword in cooking_core.COOKING_UNSURE_KEYWORDS)
    wants_more_recommendations = cooking_core._looks_like_more_recommendation_request(normalized_text, state)

    # 如果文本以"我有"开头且有食材列表，优先走食材添加逻辑
    has_ingredient_prefix = normalized_text.startswith("我有") or normalized_text.startswith("我还有")

    if state.awaiting_cooking_exit_confirmation:
        if cooking_core._looks_like_cooking_exit_confirmation(normalized_text):
            await control_stop_cooking(runtime, ws, state, {})
            await cooking_core.send_cooking_text_with_tts(runtime, ws, state, "好的，已退出做饭引导。需要时随时再叫我。", request_id=request_id)
            return True

        if cooking_core._looks_like_cooking_exit_cancellation(normalized_text):
            state.awaiting_cooking_exit_confirmation = False
            await cooking_core.send_cooking_status(ws, state)
            await cooking_core.send_cooking_text_with_tts(
                runtime,
                ws,
                state,
                "好的，那我们继续做饭引导。你可以直接告诉我食材、菜名，或者回复‘下一步’继续。",
                request_id=request_id,
            )
            return True

        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "如果要退出做饭引导，请回复‘是 / 对 / 确认 / 好’；如果不退出，可以回复‘继续 / 不用 / 取消’，或者直接告诉我食材和菜名。",
            request_id=request_id,
        )
        return True

    # AI 主动询问"是否需要一步一步带你做"后的确认处理：
    # 用户回复"可以/好/要/嗯"等 → 进入步骤式引导；回复"不用/算了"等 → 保持普通问答
    if state.awaiting_step_guide_confirmation:
        if cooking_core._accepts_step_guide(normalized_text):
            state.awaiting_step_guide_confirmation = False
            backend_log(f"[做饭] 用户确认开始步骤式引导: {normalized_text}")
            # 优先使用菜名请求直查到的 pending 菜谱（避免二次解析，且保证引导与播报一致）；
            # 无 pending（AI 主动询问路径）才回退从最近对话中提取菜名，本地匹配不到时远程生成。
            cooking_core._reset_cooking_session_state(state)
            resolved_recipe = state.pending_guide_recipe
            state.pending_guide_recipe = None
            if resolved_recipe is None:
                resolved_recipe = await _resolve_recipe_from_recent_text(runtime, state)
            if resolved_recipe:
                await _apply_remote_recipe(runtime, ws, state, resolved_recipe, request_id)
            else:
                await control_start_cooking(runtime, ws, state, {})
            return True

        if cooking_core._declines_step_guide(normalized_text):
            state.awaiting_step_guide_confirmation = False
            state.pending_guide_recipe = None
            backend_log(f"[做饭] 用户拒绝步骤式引导，保持普通问答: {normalized_text}")
            return False

        # 待确认状态下的其他回复（如给出菜名/食材/份数）也保持普通问答，
        # 由 AI 继续收集信息，避免吞掉用户输入。
        return False

    # 优先检查退出关键词
    if any(keyword in normalized_text for keyword in cooking_core.COOKING_EXIT_KEYWORDS):
        state.awaiting_cooking_exit_confirmation = True
        await cooking_core.send_cooking_status(ws, state)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "你是想退出做饭引导吗？如果确定要退出，请回复'是 / 对 / 确认'；如果不退出，请回复'继续 / 不用 / 取消'。",
            request_id=request_id,
        )
        return True

    # 菜名请求统一入口（不区分是否已开启做饭模式）：
    # 用户明确报出菜名/做法请求（如"教我做番茄炒蛋"、"番茄炒蛋怎么做"）时，
    # 直接到菜谱库查到成熟菜谱，广播固定文案"已找到《XX》菜谱，需要我教您一步步做吗？"，
    # 进入待确认状态；用户确认后再进入步骤式引导（保留份数确认）。
    # 不输出完整菜谱长文，避免 AI 自由发挥的菜谱与后端引导不一致。
    if direct_recipe_request:
        backend_log(f"[做饭] 识别到菜名请求: {normalized_text}")
        pending_recipe = None
        if direct_recipe_matches:
            pending_recipe = cooking_core.recipe_to_dict(direct_recipe_matches[0])

        if pending_recipe is None and direct_recipe_candidate:
            remote_recipe = await cooking_core.lookup_remote_recipe(runtime, normalized_text)
            if remote_recipe:
                pending_recipe = remote_recipe

        if pending_recipe is None:
            await cooking_core.send_cooking_text_with_tts(
                runtime,
                ws,
                state,
                f"我暂时没找到「{direct_recipe_candidate}」这个菜谱，您可以换个菜名试试，或者直接告诉我您手头有什么食材。",
                request_id=request_id,
            )
            return True

        dish_name = pending_recipe.get("name") or direct_recipe_candidate or "这道菜"
        # 直接开始做饭（不再询问几人份/智能计算食材）：重置状态 → 设为当前菜谱 → 进入 COOKING 第 1 步
        cooking_core._reset_cooking_session_state(state)
        await _start_cooking_immediately(
            runtime,
            ws,
            state,
            pending_recipe,
            f"已找到《{dish_name}》菜谱，我们马上开始！",
            request_id=request_id,
        )
        return True

    # 做饭模式下不再重复检测倒计时（已在 handle_user_text 中统一检测）
    # if state.cooking_enabled:
    #     # 检查取消倒计时
    #     if any(kw in normalized_text for kw in cooking_core.TIMER_CANCEL_KEYWORDS):
    #         await control_cancel_timer(runtime, ws, state, {})
    #         return True
    #
    #     # 检查设置倒计时
    #     timer_req = cooking_core.parse_timer_request(normalized_text)
    #     if timer_req:
    #         await control_start_timer(runtime, ws, state, timer_req, request_id)
    #         return True

    # 只有在 cooking_enabled 为 True 时才处理做饭场景逻辑
    should_handle_as_cooking = state.cooking_enabled and (
        direct_recipe_request
        or wants_more_recommendations
        or wants_unsure_help
        or wants_cooking
        or bool(ingredient_list)
        or any(keyword in normalized_text for keyword in cooking_core.COOKING_EXIT_KEYWORDS)
        or cooking_core.matches_cooking_confirmation(normalized_text)
        or any(keyword in normalized_text for keyword in cooking_core.COOKING_SKIP_KEYWORDS)
        or (state.current_recipe is not None and cooking_core._looks_like_cooking_question(normalized_text))
        or cooking_core._looks_like_ingredient_request(normalized_text, ingredient_list)
        or (bool(matched_recipes) and cooking_core._looks_like_recipe_request(normalized_text))
    )

    backend_log(
        f"[调试] cooking_enabled={state.cooking_enabled}, should_handle_as_cooking={should_handle_as_cooking}, "
        f"ingredient_list={ingredient_list}, awaiting_ingredients_ready={state.awaiting_ingredients_ready}, "
        f"cooking_state={state.cooking_state}, current_recipe={bool(state.current_recipe)}, "
        f"current_servings={state.current_servings}, awaiting_servings={state.awaiting_servings}"
    )

    if state.cooking_enabled and not should_handle_as_cooking:
        # AI 意图分类兜底：流程进行中（已有菜谱）的短句常是"这步完事了/先到这吧/这步算了"
        # 这类关键词漏网的流程指令——不含确认/退出/跳过任何关键词，should_handle_as_cooking
        # 为 False，直接放行普通问答会答非所问。先让 AI 分类一次，高置信度命中才接管。
        # awaiting 状态各有专门分支在后面处理，兜底不抢；问句（含问句 cue）在上面
        # should_handle_as_cooking 已判 True 不会到这里；>15 字按闲聊直接放行，控制成本。
        if (
            settings.ai_intent_fallback_enabled
            and state.current_recipe
            and len(normalized_text) <= 15
            and await _ai_intent_fallback(runtime, ws, state, normalized_text, request_id=request_id)
        ):
            return True
        backend_log(f"[调试] 因 should_handle_as_cooking=False 而返回 False")
        return False

    if cooking_core._looks_like_more_recommendation_request(normalized_text, state):
        if not state.cooking_enabled:
            await control_start_cooking(runtime, ws, state, {})
        related_recipes = cooking_core._build_related_recipe_recommendations(recipe_service, state, limit=10)
        state.cooking_state = "RECOMMENDING" if related_recipes else state.cooking_state
        await cooking_core.send_cooking_status(ws, state, recipes=related_recipes)
        if related_recipes:
            cooking_core._store_last_recommended_recipe_names(state, related_recipes)
            recipe_names = "、".join(item["name"] for item in related_recipes[:5])
            await cooking_core.send_cooking_text_with_tts(
                runtime,
                ws,
                state,
                f"可以，我再给您换一批更相关的菜谱：{recipe_names}。如果想看别的方向，也可以继续告诉我。",
                request_id=request_id,
            )
            return True

        top_recipes = cooking_core.get_top_recipes(10)
        top_recipes_payload = [cooking_core.recipe_search_result_to_dict(item) for item in top_recipes]
        cooking_core._store_last_recommended_recipe_names(state, top_recipes_payload)
        state.cooking_state = "RECOMMENDING"
        await cooking_core.send_cooking_status(ws, state, recipes=top_recipes_payload)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "我先给您换一批评分比较高、也比较常见的菜谱，您可以继续挑一个。",
            request_id=request_id,
        )
        return True

    # 如果有食材前缀且有食材列表，优先走食材添加逻辑
    if has_ingredient_prefix and ingredient_list:
        await control_add_ingredients(runtime, ws, state, {"ingredients": ingredient_list})
        return True

    # 份数问答/智能计算食材功能已移除（2026-09-06）：菜谱确定即直接开做，
    # 不再等待份数答复，也不再有 servings_calc 卡片与"食材准备好了吗"确认门。

    # 菜名请求已在函数开头统一处理（查菜谱+直接开做），此处不再走"立即上屏完整菜谱"

    # 如果正在做饭（COOKING状态），不处理食材列表
    if state.current_recipe and state.cooking_state == "COOKING":
        pass  # 跳过后续食材处理
    elif ingredient_list:
        await control_add_ingredients(runtime, ws, state, {"ingredients": ingredient_list})
        return True

    if not state.cooking_enabled:
        # 用户未明确要求步骤式引导（如只说"我想做番茄炒蛋"/"我有土豆"）时，
        # 不直接进入 cooking 流程，返回 False 走普通 LLM 问答，由 AI 收集信息后
        # 自然询问"是否需要步骤式引导做饭"。只有出现"怎么做/开始做/一步一步"等
        # 明确引导词才直接进入步骤引导流程。
        wants_step_guide = cooking_core._wants_step_guide(normalized_text)
        if not wants_cooking and not wants_step_guide:
            return False

        # 明确要求步骤引导（一步一步/手把手/步骤式...）：直接进入
        if cooking_core._wants_step_guide(normalized_text):
            # 先尝试从本条消息/最近对话解析菜谱（本地精确 → 本地模糊 → 远程 LLM 生成），
            # 成功则直接进入准备流程，避免"一步一步教我做番茄炒鸡蛋"仍被重新询问菜名。
            cooking_core._reset_cooking_session_state(state)
            resolved_recipe = await _resolve_recipe_from_recent_text(runtime, state)
            if resolved_recipe:
                await _apply_remote_recipe(runtime, ws, state, resolved_recipe, request_id)
                return True

            await control_start_cooking(runtime, ws, state, {})

            if wants_unsure_help:
                top_recipes = cooking_core.get_top_recipes(10)
                top_recipes_payload = [cooking_core.recipe_search_result_to_dict(item) for item in top_recipes]
                state.cooking_state = "RECOMMENDING"
                cooking_core._store_last_recommended_recipe_names(state, top_recipes_payload)
                await cooking_core.send_cooking_status(ws, state, recipes=top_recipes_payload)
                await cooking_core.send_cooking_text_with_tts(
                    runtime,
                    ws,
                    state,
                    "我先给你推荐前十个评分比较高、也比较常见的菜谱。你可以直接挑一个，我再按步骤带你做。",
                    request_id=request_id,
                )
                return True

            if matched_recipes and not wants_cooking:
                await control_select_recipe(runtime, ws, state, {"recipe_name": matched_recipes[0].name})
                return True

            if ingredient_list:
                await control_add_ingredients(runtime, ws, state, {"ingredients": ingredient_list})
                return True

            return True

        # 未明确要求步骤引导（wants_cooking=True 但不含引导词）：
        # 若直接匹配到唯一菜谱且用户明确说了菜名（如"我要做番茄炒蛋"后接确认），
        # 仍保持普通问答，让 AI 收集信息后询问。这里统一返回 False。
        backend_log(f"[调试] 用户表达做饭意图但未明确要求步骤引导，交普通问答处理: {normalized_text}")
        return False

    if not state.current_recipe:
        if wants_more_recommendations:
            related_recipes = cooking_core._build_related_recipe_recommendations(recipe_service, state, limit=10)
            state.cooking_state = "RECOMMENDING" if related_recipes else state.cooking_state
            await cooking_core.send_cooking_status(ws, state, recipes=related_recipes)
            if related_recipes:
                cooking_core._store_last_recommended_recipe_names(state, related_recipes)
                recipe_names = "、".join(item["name"] for item in related_recipes[:5])
                await cooking_core.send_cooking_text_with_tts(
                    runtime,
                    ws,
                    state,
                    f"可以，我再给您换一批更相关的菜谱：{recipe_names}。如果想看别的方向，也可以继续告诉我。",
                    request_id=request_id,
                )
            else:
                top_recipes = cooking_core.get_top_recipes(10)
                top_recipes_payload = [cooking_core.recipe_search_result_to_dict(item) for item in top_recipes]
                cooking_core._store_last_recommended_recipe_names(state, top_recipes_payload)
                state.cooking_state = "RECOMMENDING"
                await cooking_core.send_cooking_status(ws, state, recipes=top_recipes_payload)
                await cooking_core.send_cooking_text_with_tts(
                    runtime,
                    ws,
                    state,
                    "我先给您换一批评分比较高、也比较常见的菜谱，您可以继续挑一个。",
                    request_id=request_id,
                )
            return True

        if matched_recipes:
            await control_select_recipe(runtime, ws, state, {"recipe_name": matched_recipes[0].name})
            return True

        if ingredient_list:
            await control_add_ingredients(runtime, ws, state, {"ingredients": ingredient_list})
            return True

        if wants_unsure_help:
            top_recipes = cooking_core.get_top_recipes(10)
            top_recipes_payload = [cooking_core.recipe_search_result_to_dict(item) for item in top_recipes]
            state.cooking_state = "RECOMMENDING"
            cooking_core._store_last_recommended_recipe_names(state, top_recipes_payload)
            await cooking_core.send_cooking_status(ws, state, recipes=top_recipes_payload)
            await cooking_core.send_cooking_text_with_tts(
                runtime,
                ws,
                state,
                "我先给你推荐前十个评分比较高的菜谱。你可以直接挑一个，我再按步骤带你做。",
                request_id=request_id,
            )
            return True

        # 检查是否像菜谱请求（本地未匹配到时进行远程搜索）
        is_recipe_request = cooking_core._looks_like_recipe_request(normalized_text)
        if is_recipe_request and not wants_cooking:
            backend_log(f"[做饭] 本地未匹配到菜谱，尝试远程搜索: {normalized_text}")
            try:
                remote_recipe = await asyncio.wait_for(
                    cooking_core.lookup_remote_recipe(runtime, normalized_text),
                    timeout=10.0
                )
                if remote_recipe:
                    await _apply_remote_recipe(runtime, ws, state, remote_recipe, request_id, is_creative=True)
                    return True
            except asyncio.TimeoutError:
                backend_log(f"[做饭] 远程菜谱搜索超时: {normalized_text}")
            except Exception as e:
                backend_log(f"[做饭] 远程菜谱搜索失败: {e}")

        # 如果是做饭请求但远程搜索失败或不是菜谱请求，给出引导提示
        if wants_cooking:
            await cooking_core.send_cooking_text_with_tts(
                runtime,
                ws,
                state,
                "好的，我们开始步骤式引导做饭，请问您有什么想做的，或者菜品名称是什么？还是说您有什么菜？",
                request_id=request_id,
            )
            return True

        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "请直接告诉我你有什么食材，或者直接说你想做什么菜。",
            request_id=request_id,
        )
        return True

    if any(keyword in normalized_text for keyword in cooking_core.COOKING_EXIT_KEYWORDS):
        state.awaiting_cooking_exit_confirmation = True
        await cooking_core.send_cooking_status(ws, state)
        await cooking_core.send_cooking_text_with_tts(
            runtime,
            ws,
            state,
            "你是想退出做饭引导吗？如果确定要退出，请回复'是 / 对 / 确认'；如果不退出，请回复'继续 / 不用 / 取消'。",
            request_id=request_id,
        )
        return True

    if any(keyword in normalized_text for keyword in cooking_core.COOKING_SKIP_KEYWORDS):
        await control_skip_step(runtime, ws, state, {})
        return True

    if cooking_core.matches_cooking_confirmation(normalized_text):
        await control_confirm_step(runtime, ws, state, {})
        return True

    await handle_cooking_question(
        runtime,
        ws,
        state,
        normalized_text,
        jpeg_b64,
        temperature,
        weight,
        tts_provider,
        tts_model,
        tts_api_key,
        request_id=request_id,
    )
    return True


async def handle_cooking_event(runtime: Any, ws: WebSocket, state: SessionState, payload: dict) -> None:
    action = payload.get("action", "")
    if action == "start":
        await control_start_cooking(runtime, ws, state, payload)
    elif action == "stop":
        await control_stop_cooking(runtime, ws, state, payload)
    elif action == "add_ingredients":
        await control_add_ingredients(runtime, ws, state, payload)
    elif action == "select_recipe":
        await control_select_recipe(runtime, ws, state, payload)
    elif action == "confirm_step":
        await control_confirm_step(runtime, ws, state, payload)
    elif action == "skip_step":
        await control_skip_step(runtime, ws, state, payload)
    elif action == "cancel_timer":
        await control_cancel_timer(runtime, ws, state, payload)
    else:
        await transport_ws.ws_send(ws, "error", {"message": f"Unknown cooking action: {action}"})
