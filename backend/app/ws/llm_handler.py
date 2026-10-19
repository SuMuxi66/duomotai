"""
LLM处理模块
负责LLM流式响应、场景检测、用户文本处理
"""
from __future__ import annotations

import asyncio
import base64
import re
import time

# 2026-09-16 工具漏调用兜底：模型承接放盐上下文、收到用户"确认"后，可能只回一句文字
# 而一次 tool_call 都不发——它没撒谎，但也没干活，用户会干等。这里记住"上一轮明确向
# 用户要确认"的会话，下一轮收到确认词却零工具调用时主动挑明。
_PENDING_CONFIRM: dict[str, float] = {}
_PENDING_CONFIRM_TTL_S = 300.0
_ASK_CONFIRM_RE = re.compile(
    r"(说一声我就|说一声我(?:就|再)|确认[^。！？\n]{0,10}我就|我就打开|我就放)"
)
_USER_CONFIRM_RE = re.compile(
    r"^\s*(确认|确认了|好的?|好了|来吧|放吧|开始吧?|可以|行吧?|嗯+|ok|OK|Yes|YES)"
    r"[\s。！？!?.，,~]*$"
)
MISSED_TOOL_NOTICE = (
    "（系统提醒：刚才这一步没有真正调用下料工具，调料出口没有打开。"
    "请再说一次「放盐」，我会重新调用。）"
)
import traceback
from typing import Any

from fastapi import WebSocket

from ..config import settings
from ..file_parser import is_image_file, parse_document
from ..llm_tools import (
    TOOL_PREAMBLE_HINT,
    SALT_FLOW_HINT,
    UNFULFILLED_ACTION_NOTICE,
    find_unfulfilled_action_claim,
    get_tool_setup,
)
from ..logger import backend_log
from ..providers.errors import ModelProviderError
from ..providers.unified import UnifiedOpenAIProvider
from ..runtime_providers import can_stream_tts
from ..session import SessionState
try:
    from ..stt_corrector import correct_stt_text
except ImportError:  # stt_corrector 为队友本地文件（尚未提交到仓库），缺失时 STT 纠错降级为原样返回
    def correct_stt_text(text: str) -> str:
        return text
from . import (
    ws_send,
    send_latency,
    get_tts_api_key,
    get_tts_model,
    get_tts_voice,
    apply_tts_update,
    AppRuntime,
)
from .tts_handler import generate_tts, _StreamingTTSRelay
from .. import cooking_tools


async def stream_llm_response(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    prompt: str,
    text: str,
    jpeg_b64: str,
    tts_provider: str | None,
    tts_model: str | None,
    tts_api_key: str | None,
    request_id: str | None = None,
    tts_relay: Any | None = None,
    system_prompt: str | None = None,
    history: list | None = None,
    file_b64: list[dict] | None = None,
) -> tuple[str, bool, bool]:
    """流式LLM响应，支持多模态和TTS中继"""
    use_multimodal_once = False
    image_bytes: bytes | None = None
    if jpeg_b64:
        try:
            image_bytes = base64.b64decode(jpeg_b64)
            use_multimodal_once = hasattr(runtime.vlm, "stream_chat_with_image")
        except Exception as exc:
            backend_log(f"[视觉] 图片解码失败: {exc}")

    # Process file uploads: split images (send to VLM) and documents (parse to text)
    file_images: list[tuple[bytes, str]] | None = None
    document_texts: list[str] = []
    if file_b64 and isinstance(file_b64, list) and len(file_b64) > 0:
        file_images = []
        for f in file_b64:
            try:
                f_b64 = f.get("b64", "")
                f_type = f.get("type", "image/png")
                f_name = f.get("name", "unknown")
                if not f_b64:
                    continue
                raw_bytes = base64.b64decode(f_b64)
                if is_image_file(f_type, f_name):
                    file_images.append((raw_bytes, f_type))
                    use_multimodal_once = hasattr(runtime.vlm, "stream_chat_with_image")
                else:
                    doc_text = parse_document(f_name, f_type, raw_bytes)
                    if doc_text:
                        document_texts.append(f"--- 文件: {f_name} ---\n{doc_text}")
            except Exception as exc:
                backend_log(f"[文件] 文件处理失败 ({f.get('name', 'unknown')}): {exc}")

    # 文档文本合并进 prompt
    effective_prompt = prompt
    if document_texts:
        docs_section = "\n\n".join(document_texts)
        effective_prompt = f"{prompt}\n\n[上传的参考文档]\n{docs_section}"

    # 当用户主动上传图片时，不再叠加实时视频帧，避免视觉输入冲突
    vision_frame_bytes = None if (file_images and len(file_images) > 0) else image_bytes

    # function calling 工具层：11 个工具（温度/重量/去皮/下料启停与状态/菜谱/风扇）
    # 与 mcp_server.py 同构；dispense_start 带确认门（跨轮预约 + 用户确认词放行，
    # 详见 llm_tools.py——text 为用户原始消息，用于识别"模型口头问过、用户刚答确认"的放行路径）。
    # 确定性拦截链路（显式下料/盐量脉冲等）在进入这里之前已优先执行。
    # 仅统一 OpenAI 兼容 provider 支持；mock 等其他实现自动降级为无工具。
    tool_specs, tool_executor = get_tool_setup(state, text, runtime)
    if tool_specs and not isinstance(runtime.vlm, UnifiedOpenAIProvider):
        tool_specs, tool_executor = None, None

    # 统计本轮真正执行过几次工具：模型可能口播"我调用了XX工具"却一次 tool_call 都没发，
    # 流结束后用它判断文本里的"已打开/已放完"到底是真做了还是编的。
    tool_calls_made = 0
    if tool_specs and tool_executor is not None:
        _raw_tool_executor = tool_executor

        async def _counting_tool_executor(name: str, arguments_json: str) -> str:
            nonlocal tool_calls_made
            tool_calls_made += 1
            return await _raw_tool_executor(name, arguments_json)

        tool_executor = _counting_tool_executor
    if tool_specs:
        # 工具决策轮实测有 3~28s 的无输出空档，要求模型"边播报边调工具"给用户即时反馈
        # （播报与工具调用的执行续答由 unified._stream_with_tool_loop 串起来）
        system_prompt = f"{system_prompt or ''}\n\n{TOOL_PREAMBLE_HINT}\n\n{SALT_FLOW_HINT}".strip()

    if use_multimodal_once and (vision_frame_bytes is not None or (file_images and len(file_images) > 0)):
        stream_iter = runtime.vlm.stream_chat_with_image(
            effective_prompt,
            vision_frame_bytes,  # vision frame (may be None)
            file_images=file_images,  # uploaded files (may be None)
            system_prompt=system_prompt,
            history=history,
            enable_thinking=state.llm_enable_thinking,
            tools=tool_specs,
            tool_executor=tool_executor,
        )
    else:
        stream_iter = runtime.vlm.stream_chat(
            effective_prompt,
            system_prompt=system_prompt,
            history=history,
            temperature=state.llm_temperature,
            max_tokens=state.llm_max_tokens,
            top_p=state.llm_top_p,
            n=state.llm_sample_count,
            frequency_penalty=state.llm_frequency_penalty,
            response_format=state.llm_response_format,
            enable_thinking=state.llm_enable_thinking,
            tools=tool_specs,
            tool_executor=tool_executor,
        )

    buffer: list[str] = []
    reasoning_text = ""
    first_delta_sent = False
    streaming_tts_started = tts_relay is not None
    tts_text_buffer = ""
    # 跨 chunk 尚未闭合的 cooking 标记尾巴：等闭合后再剥离放行，避免半截标记泄漏到前端
    cooking_mark_pending = ""
    # 硬件完成态断言（"出口已打开/盐已放完"）先压住不进 TTS：模型允许"边播报边调工具"，
    # 播报必然早于 tool_call，此时无法判断真假；等本轮结束后按 tool_calls_made 决定
    # 补播（真执行过）还是丢弃（纯口播）。
    held_tts_sentences: list[str] = []
    # 检出 [COOKING:...] 交接标记后：LLM 剩余文本不再展示/播报，
    # 后续引导由 cooking 预设状态机接管，避免“模型自由发挥 + 系统开场白”双重回复
    cooking_marker_seen = False
    provider_error_message: str | None = None

    async def _guarded_stream():
        # provider 层的 ModelProviderError（HTTP 非 200 / 网络异常重试用尽）在此收敛：
        # 记录用户可读文案后正常结束迭代，错误文本只上屏不进 TTS（见循环后处理）
        nonlocal provider_error_message
        try:
            async for item in stream_iter:
                yield item
        except ModelProviderError as exc:
            provider_error_message = exc.user_message
            backend_log(f"[LLM] provider 调用失败: {exc.provider} status={exc.status} detail={exc.detail[:200]}")

    async for delta in _guarded_stream():
        reasoning_delta = ""
        if isinstance(delta, tuple):
            delta, reasoning_delta = delta
        if reasoning_delta:
            reasoning_text += reasoning_delta
            await ws_send(ws, "assistant_reasoning_delta", {"reasoning": reasoning_delta, "request_id": request_id})
        if state.interrupted:
            await ws_send(ws, "interrupted", {"message": "AI 生成已被打断"})
            return "", use_multimodal_once, streaming_tts_started
        if not delta:
            continue
        # buffer 保留原始 delta（含标记）：final 阶段依赖原始文本解析 [COOKING:...] 标记
        buffer.append(delta)
        # 显示通道：过滤 cooking 工具标记。先把滞留尾巴与当前块合并再剥离；
        # 若剥离后行尾仍残留未闭合的 [COOKING... ，继续滞留（不发送），避免半截标记泄漏到前端
        combined_pending = cooking_mark_pending + delta
        if not cooking_marker_seen and cooking_tools.parse_cooking_tool(combined_pending):
            cooking_marker_seen = True
        stripped_delta = cooking_tools.strip_cooking_tools(combined_pending)
        unclosed_marker = re.search(r"\[COOKING[^\]]*$", stripped_delta, re.IGNORECASE)
        if unclosed_marker:
            cooking_mark_pending = unclosed_marker.group(0)
            display_delta = stripped_delta[: unclosed_marker.start()]
        else:
            cooking_mark_pending = ""
            display_delta = stripped_delta
        if not display_delta:
            continue
        if cooking_marker_seen:
            # 交接标记已出现：后续 LLM 文本全部静默（不进聊天窗、不进 TTS）
            continue
        # 剥离风扇协议标记 [FAN_ON]/[FAN_OFF]/[FAN_STATUS]——避免显示和 TTS 念出协议字符
        display_delta = re.sub(r'\[FAN_(?:ON|OFF|STATUS)\]', '', display_delta).strip()
        if not display_delta:
            continue
        await ws_send(ws, "assistant_delta", {"delta": display_delta, "request_id": request_id})
        if not first_delta_sent:
            first_delta_sent = True
            await send_latency(ws, request_id, "llm_first_delta")

        if tts_relay is not None and not state.interrupted:
            tts_text_buffer += display_delta
            while True:
                sentence_match = re.search(r".*?[。！？!?]\s*", tts_text_buffer)
                if not sentence_match:
                    break
                sentence = sentence_match.group(0).strip()
                if sentence:
                    if find_unfulfilled_action_claim(sentence):
                        held_tts_sentences.append(sentence)  # 先压住，流结束后再定夺
                    else:
                        await tts_relay.enqueue(sentence)
                tts_text_buffer = tts_text_buffer[sentence_match.end():]

    if provider_error_message:
        # 错误文案上屏聊天流并随 final 进历史（与正常回复一致的收尾），但不进 TTS 队列——
        # 原实现把 provider 错误字符串当 delta 播报，语音用户会听到整段 JSON 错误页
        buffer.append(provider_error_message)
        await ws_send(ws, "assistant_delta", {"delta": provider_error_message, "request_id": request_id})
        tts_text_buffer = ""

    if tts_relay is not None and tts_text_buffer.strip() and not cooking_marker_seen:
        await tts_relay.enqueue(tts_text_buffer.strip())

    # 硬件动作断言兜底（2026-09-16）：本轮一次工具都没真正执行，文本却声称
    # "出口已打开/盐已放完"——提示词已收紧措辞，这里拦住最后一道，避免把编造的
    # 执行结果当成既成事实念给用户、误导后续操作（演示时尤其致命）。
    if held_tts_sentences or find_unfulfilled_action_claim("".join(buffer)):
        if tool_calls_made == 0:
            backend_log(
                f"[工具断言兜底] 本轮无工具执行却出现硬件完成态描述，"
                f"丢弃 {len(held_tts_sentences)} 句未播报并追加纠正（request_id={request_id}）"
            )
            held_tts_sentences.clear()
            tts_text_buffer = ""
            buffer.append(UNFULFILLED_ACTION_NOTICE)
            await ws_send(ws, "assistant_delta", {"delta": UNFULFILLED_ACTION_NOTICE, "request_id": request_id})
            if tts_relay is not None and not cooking_marker_seen:
                await tts_relay.enqueue(UNFULFILLED_ACTION_NOTICE)
        elif tts_relay is not None and not cooking_marker_seen:
            # 确实执行过工具：被压住的句子是真结果的播报，补播
            for _held in held_tts_sentences:
                await tts_relay.enqueue(_held)
            held_tts_sentences.clear()

    final = "".join(buffer).strip()
    # 解析并剥离 cooking 工具标记（标记用于驱动预设状态机，不展示给用户）
    cooking_tool = cooking_tools.parse_cooking_tool(final)
    final = cooking_tools.strip_cooking_tools(final).strip()

    fan_action = None
    if "[FAN_ON]" in final:
        fan_action = "on"
        final = final.replace("[FAN_ON]", "").strip()
    elif "[FAN_OFF]" in final:
        fan_action = "off"
        final = final.replace("[FAN_OFF]", "").strip()
    elif "[FAN_STATUS]" in final:
        fan_action = "status"
        final = final.replace("[FAN_STATUS]", "").strip()

    if cooking_marker_seen:
        # LLM 文本已静默：final 置空，最终消息与记录以预设状态机推送的引导文本为准
        final = ""

    # 该调用却没调用（2026-09-16）：上一轮明确请求用户确认、这一轮用户回了确认词，
    # 结果一次工具都没调用——它没撒谎，但也没干活。这里主动挑明，别让用户干等。
    session_key = getattr(state, "session_id", None) or "default"
    pending_since = _PENDING_CONFIRM.get(session_key)
    if (
        tool_calls_made == 0
        and pending_since is not None
        and time.time() - pending_since < _PENDING_CONFIRM_TTL_S
        and _USER_CONFIRM_RE.match(text or "")
    ):
        _PENDING_CONFIRM.pop(session_key, None)
        backend_log(
            f"[工具漏调用兜底] 会话在等待确认后收到确认词，本轮却零工具调用，"
            f"已追加提醒（request_id={request_id}）"
        )
        buffer.append(MISSED_TOOL_NOTICE)
        final = (final + MISSED_TOOL_NOTICE).strip()
        await ws_send(ws, "assistant_delta", {"delta": MISSED_TOOL_NOTICE, "request_id": request_id})
        if tts_relay is not None and not cooking_marker_seen:
            await tts_relay.enqueue(MISSED_TOOL_NOTICE)
    elif tool_calls_made > 0:
        _PENDING_CONFIRM.pop(session_key, None)
    elif _ASK_CONFIRM_RE.search(final):
        # 本轮在向用户要确认：记下来，等下一轮的答复
        _PENDING_CONFIRM[session_key] = time.time()

    await ws_send(ws, "assistant_final", {"text": final, "request_id": request_id, "reasoning": reasoning_text})
    await send_latency(ws, request_id, "llm_final")

    if fan_action:
        await ws_send(ws, "fan_control", {"action": fan_action})
        backend_log(f"[风扇控制] 检测到指令: {fan_action}")

    cooking_handled = False
    if cooking_tool:
        try:
            cooking_handled = await cooking_tools.dispatch_cooking_tool(
                runtime, ws, state, cooking_tool, request_id
            )
        except Exception as exc:
            backend_log(f"[cooking_tools] 执行失败: {exc}\n{traceback.format_exc()}")

    if cooking_handled:
        # cooking 预设状态机已推送引导文本并 TTS，不再把 LLM 文本作为最终回复
        return "", use_multimodal_once, streaming_tts_started

    if use_multimodal_once:
        state.add_observation(final[:300])
        if file_images and len(file_images) > 0:
            await ws_send(ws, "vision_observation", {"text": "本次已使用您上传的图片与问题进行一次联合推理。", "user_question": text})
        elif image_bytes is not None:
            await ws_send(ws, "vision_observation", {"text": "本次已使用当前画面与问题进行一次联合推理。", "user_question": text})
    return final, use_multimodal_once, streaming_tts_started


async def stream_scene_text(ws: WebSocket, text: str, request_id: str | None = None) -> None:
    """流式输出场景文本"""
    for char in text:
        await ws_send(ws, "assistant_delta", {"delta": char, "request_id": request_id})
        await asyncio.sleep(0.02)
    await ws_send(ws, "assistant_final", {"text": text, "request_id": request_id})


async def handle_scene_response(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    tts_provider: str | None,
    tts_model: str | None,
    tts_api_key: str | None,
    request_id: str | None = None,
) -> bool:
    """处理场景响应（关键词匹配）"""
    from .. import prompts as app_prompts

    scene_response = app_prompts.detect_scene(text)
    if not scene_response:
        return False

    backend_log(f"[场景检测] 匹配成功: {scene_response}")
    await send_latency(ws, request_id, "llm_start")
    await send_latency(ws, request_id, "llm_first_delta")

    effective_provider = tts_provider or state.tts_provider or settings.tts_provider
    effective_api_key = get_tts_api_key(effective_provider, tts_api_key or state.tts_api_key)
    tts_relay: Any | None = None
    if effective_api_key and can_stream_tts(effective_provider, runtime.tts):
        voice = state.tts_voice or get_tts_voice(effective_provider)
        tts_relay = _StreamingTTSRelay(
            ws,
            state,
            effective_provider,
            runtime.tts,
            voice,
            request_id,
            log_prefix="场景TTS",
        )
        await tts_relay.enqueue(scene_response)

    await stream_scene_text(ws, scene_response, request_id=request_id)
    await send_latency(ws, request_id, "llm_final")
    if tts_relay is not None:
        await tts_relay.close()
    else:
        await generate_tts(
            runtime,
            ws,
            state,
            scene_response,
            provider=tts_provider,
            model=tts_model,
            api_key=tts_api_key,
            log_prefix="场景TTS",
            request_id=request_id,
        )
    return True


async def handle_llm_response(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    prompt: str,
    text: str,
    jpeg_b64: str,
    tts_provider: str | None,
    tts_model: str | None,
    tts_api_key: str | None,
    request_id: str | None = None,
    system_prompt: str | None = None,
    history: list | None = None,
    file_b64: list[dict] | None = None,
) -> str:
    """处理LLM响应，包括TTS中继和历史记录。返回最终文本"""
    effective_tts_provider = tts_provider or state.tts_provider or settings.tts_provider
    effective_tts_api_key = get_tts_api_key(effective_tts_provider, tts_api_key or state.tts_api_key)
    tts_relay: Any | None = None
    if effective_tts_api_key and can_stream_tts(effective_tts_provider, runtime.tts):
        apply_tts_update(runtime, effective_tts_provider, state.tts_model or get_tts_model(effective_tts_provider, tts_model), effective_tts_api_key)
        voice = state.tts_voice or get_tts_voice(effective_tts_provider)
        tts_relay = _StreamingTTSRelay(
            ws,
            state,
            effective_tts_provider,
            runtime.tts,
            voice,
            request_id,
            log_prefix="TTS",
        )

    final = ""
    streaming_tts_started = False
    try:
        await send_latency(ws, request_id, "llm_start")
        final, streaming_tts_started, _ = await stream_llm_response(
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
            tts_relay=tts_relay,
            system_prompt=system_prompt,
            history=history,
            file_b64=file_b64,
        )
    except asyncio.CancelledError:
        backend_log("[中断] 当前生成任务已取消")
        if tts_relay is not None:
            await tts_relay.close()
        await ws_send(ws, "interrupted", {"message": "AI 生成已被打断"})
        raise
    except Exception as exc:
        backend_log(f"[LLM] 失败: {exc}\n{traceback.format_exc()}")
        if tts_relay is not None:
            await tts_relay.close()
        await ws_send(ws, "error", {"message": f"LLM错误: {exc}"})
        return ""

    # 在 stream_llm_response 返回后（assistant_final 已发出）立即检测：
    # 若 AI 询问了"是否需要一步一步带你做"，进入待确认状态。
    # 放在此处而非 handle_user_text，可避免后续 TTS 生成期间任务被
    # 新消息打断（cancel）导致状态丢失——用户听完问句即答"可以"的场景。
    if final:
        try:
            from .. import cooking_core as _cc
            if _cc._looks_like_step_guide_ask(final):
                state.awaiting_step_guide_confirmation = True
                backend_log("[做饭] AI 已询问是否步骤式引导，进入待确认状态")
        except Exception:
            pass

    if tts_relay is not None:
        await tts_relay.close()
    elif final and not streaming_tts_started:
        await generate_tts(
            runtime,
            ws,
            state,
            final,
            provider=tts_provider,
            model=tts_model,
            api_key=tts_api_key,
            log_prefix="TTS",
            request_id=request_id,
        )

    return final


async def handle_user_text(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    jpeg_b64: str = "",
    vision_requested: bool = False,
    has_vision: bool = False,
    temperature: dict | None = None,
    weight: dict | None = None,
    vlm_model: str | None = None,
    tts_model: str | None = None,
    tts_provider: str | None = None,
    tts_api_key: str | None = None,
    request_id: str | None = None,
    file_b64: list[dict] | None = None,
) -> None:
    """处理用户文本输入"""
    del vlm_model
    text = correct_stt_text(text)
    state.interrupted = False
    await send_latency(ws, request_id, "server_received")

    def looks_like_weight_question(t):
        return any(kw in t for kw in ['重量', '多重', '称重', '分量', '克', 'kg', '千克', 'g', '克数'])

    def looks_like_temperature_question(t):
        return any(kw in t for kw in ['温度', '多少度', '热度', '冷不冷', '烫不烫', '温', '气温', '体温', '°C', '摄氏'])

    def looks_like_cooking_question(t):
        from .. import cooking_core
        # 更精确：包含做饭意图词（做饭/菜/烹饪/菜谱/食材/想吃/做菜/下厨）或做法类问题
        return any(kw in t for kw in cooking_core.COOKING_START_KEYWORDS) or any(
            kw in t for kw in ['菜谱', '食材', '烹饪', '做菜', '炒菜', '怎么做', '做法', '菜怎么做']
        )

    use_weight_context = looks_like_weight_question(text)
    use_temp_context = looks_like_temperature_question(text)
    use_cooking_context = looks_like_cooking_question(text)

    if weight is None and use_weight_context:
        try:
            from ..weight import get_weight
            weight = await asyncio.get_event_loop().run_in_executor(None, get_weight)
        except Exception as e:
            backend_log(f"[重量] 获取重量数据失败: {e}")
            weight = None

    if temperature is None and use_temp_context:
        try:
            from ..temperature import get_temperature
            temperature = await asyncio.get_event_loop().run_in_executor(None, get_temperature)
        except Exception as e:
            backend_log(f"[温度] 获取温度数据失败: {e}")
            temperature = None

    backend_log(
        f"[用户请求] text={text} image={bool(jpeg_b64)} files={len(file_b64) if file_b64 else 0} temperature={bool(temperature)} weight={bool(weight)} "
        f"tts_model={tts_model or 'default'} tts_provider={tts_provider or 'default'} observations={len(state.vision_observations)}"
    )

    normalized_text = text.strip()
    backend_log(f"[倒计时检测] text={normalized_text}, request_id={request_id}")

    from ..ws_handlers.cooking import control_start_timer, control_cancel_timer
    from .. import cooking_core
    from ..timer_manager import timer_manager

    if any(kw in normalized_text for kw in cooking_core.TIMER_CANCEL_KEYWORDS):
        backend_log(f"[倒计时检测] 检测到取消倒计时请求")
        await control_cancel_timer(runtime, ws, state, {})
        return

    timer_req = cooking_core.parse_timer_request(normalized_text)
    if timer_req:
        now = time.time()
        desc = timer_req["description"]
        # 只对本会话的定时器做重复检测（全局列表会把其他会话同描述定时器误判为重复）
        all_timers = timer_manager.get_all_timers(session_id=getattr(state, "session_id", "default"))
        dup_timer = any(t.description == desc and now - t.created_at < 3 for t in all_timers)
        if dup_timer:
            backend_log(f"[倒计时检测] 跳过重复倒计时请求: {timer_req}")
            return
        backend_log(f"[倒计时检测] 检测到设置倒计时请求: {timer_req}")
        await control_start_timer(runtime, ws, state, timer_req, request_id)
        return

    # 取消倒计时：确定性拦截，不依赖 LLM 输出工具标记
    # （实测 LLM 会只口头说"已为您取消倒计时"而不输出 COOKING:CANCEL_TIMER，
    # 导致定时器残留、前端卡片不消失）。与上方设置检测同层，命中即直接取消本会话倒计时。
    if re.search(r"(取消|清掉|清理|关掉|停掉|停止|移除|删掉)", normalized_text) and re.search(
        r"(倒计时|计时|定时|闹钟)", normalized_text
    ):
        backend_log(f"[倒计时检测] 检测到取消倒计时请求: {normalized_text}")
        await control_cancel_timer(runtime, ws, state, {})
        return

    # 烟雾 → 风扇询问待响应：拦截口语确认（"好"/"开启"/"要"/"开"等）直接开风扇，不走 LLM
    if state.smoke_pending_fan_response:
        from ..ws_handlers.control import control_smoke_fan_response
        CONFIRM_KWS = ("好", "好的", "好啊", "要", "开", "开启", "打开", "打开风扇", "开风扇",
                      "开排气扇", "开排风扇", "开吧", "可以", "行", "嗯", "对", "是", "开启吧", "打开吧")
        CANCEL_KWS = ("不要", "不", "不用", "暂不", "不开启", "不开", "别开", "算了")
        if any(kw in normalized_text for kw in CONFIRM_KWS) and len(normalized_text) <= 8:
            backend_log(f"[烟雾警报] 拦截口语确认: {normalized_text}")
            await control_smoke_fan_response(runtime, ws, state, {"response": "yes"})
            return
        if any(kw in normalized_text for kw in CANCEL_KWS) and len(normalized_text) <= 8:
            backend_log(f"[烟雾警报] 拦截口语拒绝: {normalized_text}")
            await control_smoke_fan_response(runtime, ws, state, {"response": "no"})
            return

    # 盐量脉冲确认门已于 2026-09-16 移除：用户说"准备好了"现在由大模型判断并调用
    # dispense_start(pulse_seconds=3) + dispense_stop 出料（见 SALT_FLOW_HINT），
    # 后端不再用 awaiting_salt_pulse_confirmation 拦截这一轮输入。

    # 显式下料指令确定性拦截（"下3克盐/放5克糖/帮我下3g/下料3克"）：直接驱动下料机不走 LLM。
    # 实测（2026-09-04）非做饭模式下该指令落普通问答，模型回复"无法控制下料设备，请手动添加"
    # ——显式指令不能依赖 LLM 输出 [COOKING:DISPENSE] 工具标记的合规性，
    # 与上方倒计时/油温/温度快答同款确定性拦截，做饭模式开与关都生效。
    dispense_grams = cooking_core.parse_dispense_command(normalized_text)
    if dispense_grams is not None:
        from ..ws_handlers.cooking import handle_explicit_dispense_command
        backend_log(f"[下料指令] 命中显式下料指令: {normalized_text} -> {dispense_grams:g}g")
        await handle_explicit_dispense_command(runtime, ws, state, dispense_grams, request_id=request_id)
        return

    # 盐量脉冲链路已于 2026-09-16 移除（用户要求：放盐整条链路改由大模型调用 MCP 工具完成）。
    # 现在"要放多少盐 / 我准备放调料了"落到下面的普通 LLM 问答：模型先调 get_weight 读秤、
    # 按食材×0.4% 算量并按固定话术回复；用户说"准备好了"后模型调
    # dispense_start(pulse_seconds=3) + dispense_stop 完成出料。此处不再做确定性拦截。

    # 油温盯守确认门：上一轮油温快答 fallback 文案发出"我帮你盯着油温"提议后，
    # 等用户短确认（"需要，帮我盯着吧"）→ 启动 OilWatch 监测；取消（"不用了"）→ 回复放弃；
    # 其他内容 → 清标志放行原路由。一次性监听（参照盐量脉冲确认门）。
    # 必须在普通 LLM 问答之前拦截，否则"帮我盯着"会被 LLM 自由发挥成 [FAN_ON] 开风扇。
    if state.awaiting_oil_watch_confirm:
        from ..oil_watch import (
            OIL_WATCH_CONFIRM_EXPIRE_S, is_oil_watch_confirm_text, is_oil_watch_cancel_text,
            start_oil_watch, FALLBACK_TARGET,
        )
        if time.time() - getattr(state, "oil_watch_proposed_at", 0.0) > OIL_WATCH_CONFIRM_EXPIRE_S:
            state.awaiting_oil_watch_confirm = False
            backend_log("[油温盯守] 确认超时（10 分钟），标志清除")
        elif is_oil_watch_cancel_text(normalized_text):
            state.awaiting_oil_watch_confirm = False
            backend_log(f"[油温盯守] 用户取消: {normalized_text}")
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state, "好的，需要的时候随时叫我。", request_id=request_id,
            )
            return
        elif is_oil_watch_confirm_text(normalized_text):
            target = getattr(state, "oil_watch_proposed_target", None) or FALLBACK_TARGET
            # 状态须在 TTS 播报前清掉（send_cooking_text_with_tts 因 TTS 队列背压可阻塞数十秒）
            state.awaiting_oil_watch_confirm = False
            backend_log(f"[油温盯守] 用户确认启动: {normalized_text} -> target={target}")
            await cooking_core.send_cooking_text_with_tts(
                runtime, ws, state,
                f"好的，正在监测油温，达到 {target}°C 我会主动提醒你下食材。",
                request_id=request_id,
            )
            await start_oil_watch(runtime, ws, state, target)
            return
        else:
            state.awaiting_oil_watch_confirm = False
            backend_log(f"[油温盯守] 等待确认期间收到其他内容，清标志放行: {normalized_text[:30]}")

    # 油温快答（2026-09-12 起默认关闭，OIL_TEMP_FAST_ANSWER_ENABLED=true 才启用）：
    # 旧的确定性模板快答（意图关键词表 + 固定文案）被 LLM 工具链路取代——
    # 油温/下锅时机问句落入普通 LLM 问答，由模型调 get_temperature 工具取实测后
    # 结合菜品油温知识回答（约束见 prompts/system.md 油温小节）；盯守请求仍走
    # [COOKING:OIL_WATCH] 工具标记。开关保留是为了演示出问题时可一键切回旧快答。
    if settings.oil_temp_fast_answer_enabled:
        from ..oil_watch import answer_oil_temp_query, looks_like_oil_temp_query
        if looks_like_oil_temp_query(normalized_text, state):
            if await answer_oil_temp_query(runtime, ws, state, normalized_text):
                return

    # 温度快答：与油温快答同款——"现在温度多少度"类查询直接用传感器实测回答。
    # GLM 等模型即使 prompt 带了实测数据仍可能对齐拒答"无法获取实时温度"，故走确定性快答。
    if looks_like_temperature_question(text):
        from ..temperature import answer_temperature_query, looks_like_general_temp_query
        if looks_like_general_temp_query(normalized_text):
            if await answer_temperature_query(runtime, ws, state, normalized_text):
                return

    # 工具层激活时不再把温度/重量实测注入 prompt 上下文：上下文里已有数据会让模型
    # 跳过 get_temperature / get_weight 工具调用直接作答（2026-09-12 用户实测：模型
    # 直接读上下文温度快照回答，没有发起 MCP 工具调用）。工具层是实测数据的唯一
    # 来源，prompt 注入只作为无工具 provider（mock 等）的兜底。做饭问答链路
    # （handle_cooking_question 的"温度数据：…""重量数据：…"字段）同样不再注入。
    tools_active = (
        settings.llm_tools_enabled
        and isinstance(getattr(runtime, "vlm", None), UnifiedOpenAIProvider)
    )
    prompt_temperature = None if tools_active else (temperature if use_temp_context else None)
    prompt_weight = None if tools_active else (weight if use_weight_context else None)

    from ..ws_handlers.cooking import handle_cooking_user_text

    if await handle_cooking_user_text(
        runtime, ws, state, text, jpeg_b64,
        prompt_temperature, prompt_weight,
        tts_provider, tts_model, tts_api_key, request_id=request_id,
    ):
        return

    if await handle_scene_response(runtime, ws, state, text, tts_provider, tts_model, tts_api_key, request_id=request_id):
        return

    # 2026-09-07 用户决策：视觉分析开关开启时前端为每条消息附带实时帧，
    # 任何问题都注入画面，不再按关键词挑拣（曾因关键词门把"看得见画面吗"
    # 的帧剥掉，模型如实回答"未收到任何图片"）。开关关闭时前端不采帧，
    # jpeg_b64 为空自然不注入。
    use_vision_context = bool(jpeg_b64)

    from .. import prompts as app_prompts

    prompt, sys_prompt = app_prompts.build_chat_prompt(
        text,
        state.vision_observations if (use_vision_context or use_cooking_context) else [],
        prompt_temperature,
        prompt_weight,
        cooking_context=use_cooking_context,
    )

    state.context_engine.record_user_message(
        session_id=getattr(state, 'session_id', 'default'),
        content=text,
        scene_type="cooking" if use_cooking_context else ("vision" if use_vision_context else "general"),
        metadata={"temperature": temperature, "weight": weight, "vision_enabled": use_vision_context},
    )

    chat_history = await state.context_engine.build_history_async(
        session_id=getattr(state, 'session_id', 'default'),
        max_tokens=2000,
        scene_type="cooking" if use_cooking_context else "general"
    )

    # 思考模式已全局写死关闭（providers/unified.THINKING_HARD_DISABLED），
    # 原先"做饭场景临时关思考、结束恢复 True"的临时开关已无意义且会把状态改回 True，故移除。
    final = await handle_llm_response(
        runtime, ws, state, prompt, text, jpeg_b64 if use_vision_context else "", tts_provider, tts_model, tts_api_key,
        request_id=request_id, system_prompt=sys_prompt, history=chat_history, file_b64=file_b64,
    )

    if final:
        state.context_engine.record_assistant_message(session_id=getattr(state, 'session_id', 'default'), content=final)


def _start_user_text_task(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    jpeg_b64: str,
    vision_requested: bool = False,
    has_vision: bool = False,
    temperature: dict | None = None,
    weight: dict | None = None,
    vlm_model: str | None = None,
    tts_model: str | None = None,
    tts_provider: str | None = None,
    tts_api_key: str | None = None,
    request_id: str | None = None,
    file_b64: list[dict] | None = None,
) -> None:
    """启动用户文本处理任务"""
    if state.current_task and not state.current_task.done():
        state.interrupted = True
        state.current_task.cancel()

    state.current_task = asyncio.create_task(
        handle_user_text(
            runtime, ws, state, text, jpeg_b64, vision_requested, has_vision, temperature, weight, vlm_model, tts_model, tts_provider, tts_api_key, request_id, file_b64=file_b64,
        )
    )

    def _clear_task(fut: asyncio.Task) -> None:
        if state.current_task is fut:
            state.current_task = None
        try:
            fut.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            backend_log(f"[Task] 用户请求任务失败: {exc}\n{traceback.format_exc()}")

    state.current_task.add_done_callback(_clear_task)


def _persist_frontend_model_config(provider: str, model: str, model_type: str) -> None:
    """把前端选择的模型/provider 写入 .env.local 作为默认配置（值变化才写并重载）。

    custom 等无固定环境变量前缀的 provider 不暂存。只暂存 provider/模型名——
    API key 不经 hello 回写落盘（hello 属被动通道，会把前端残留的旧/错 key 覆盖进
    .env.local 造成串 key）；key 只能经 set_model_provider / set_stt_provider /
    set_tts_provider 的显式确认通道由策略层 persist_api_key 落盘。
    同时持久化当前选中的 provider（VLM_PROVIDER），刷新网页/重启后端后继续使用。
    """
    from ..config import PROVIDER_ENV_PREFIX, persist_env_value, reload_settings

    prefix = PROVIDER_ENV_PREFIX.get(provider)
    if prefix is None:
        return
    changed = False
    if provider:
        changed |= persist_env_value("VLM_PROVIDER", provider)
    if model:
        changed |= persist_env_value(f"{prefix}_{model_type.upper()}_MODEL", model)
    if changed:
        reload_settings()


def apply_hello_settings(runtime: Any, state: SessionState, payload: dict) -> None:
    """应用前端发送的配置"""
    from . import set_session_tts, get_tts_voice, refresh_model_runtime, apply_stt_update, get_stt_runtime_key
    from ..runtime_providers import normalize_tts_voice

    custom_prompt = str(payload.get("custom_prompt") or "").strip()
    vision = payload.get("vision", {})
    state.vision_enabled = bool(vision.get("enabled", False))
    state.vision_fps = float(vision.get("fps", 0.5))
    stt = payload.get("stt", {})
    state.audio_sample_rate = int(stt.get("sampleRate", 16000))
    if custom_prompt:
        state.custom_prompt = custom_prompt

    state.llm_temperature = float(payload.get("llm_temperature", state.llm_temperature))
    state.llm_max_tokens = int(payload.get("llm_max_tokens", state.llm_max_tokens))
    state.llm_top_p = float(payload.get("llm_top_p", state.llm_top_p))
    state.llm_sample_count = max(1, int(payload.get("llm_sample_count", state.llm_sample_count)))
    state.llm_frequency_penalty = float(payload.get("llm_frequency_penalty", state.llm_frequency_penalty))
    response_format = str(payload.get("llm_response_format") or state.llm_response_format or "md").strip().lower()
    state.llm_response_format = response_format if response_format in {"md", "json"} else "md"
    # 🔒 思考模式写死关闭（2026-09-15 用户指令）：前端「启用深度思考」不再生效。
    # 权威收口在 providers/unified.THINKING_HARD_DISABLED，这里只是让会话状态
    # 与 /config 回显保持一致，避免界面显示"已思考"却实际没思考。
    state.llm_enable_thinking = False

    state.danger_alert_enabled = bool(payload.get("danger_alert_enabled", state.danger_alert_enabled))
    state.oil_smoke_alert_enabled = bool(payload.get("oil_smoke_alert_enabled", state.oil_smoke_alert_enabled))

    vlm_provider = payload.get("vlm_provider", settings.vlm_provider)
    vlm_model = payload.get("vlm_model", "")
    api_key = payload.get("api_key", "")
    provider_api_key = payload.get("provider_api_key", api_key)
    stt_provider = payload.get("stt_provider", "browser")
    stt_model = payload.get("stt_model", "")
    stt_api_key = payload.get("stt_api_key", "")
    tts_provider_val = payload.get("tts_provider", "browser")
    tts_model = payload.get("tts_model", "")
    tts_api_key = payload.get("tts_api_key", "")
    # 前端上送音色先按 provider 白名单清洗（小米对非法 voice 直接 400，
    # 前端 localStorage 历史脏值如 default_zh 会让该 session 播报全灭）；
    # 清洗为 None（空值/其余 provider 原样放行为空）再落该 provider 默认音色
    tts_voice = normalize_tts_voice(tts_provider_val, payload.get("tts_voice")) or get_tts_voice(tts_provider_val)

    # 仅修改 session 级别配置，不修改全局 settings
    state.vlm_provider = vlm_provider
    if provider_api_key:
        state.llm_api_key = provider_api_key
    elif api_key:
        state.llm_api_key = api_key
    if vlm_model:
        state.vlm_model = vlm_model
    state.stt_provider = stt_provider
    if stt_model and stt_provider != "browser":
        state.stt_model = stt_model
    if stt_api_key:
        state.stt_api_key = stt_api_key

    set_session_tts(state, tts_provider_val, None, tts_api_key, tts_voice)

    # 模型/provider 的落盘只发生在面板显式路径（set_model / set_model_provider），
    # hello 不再经 _persist_frontend_model_config 把前端值写入 .env.local——
    # 前端 localStorage 残留的旧/脏模型名会污染 env 权威值。state.vlm_provider /
    # state.vlm_model 赋值仍保留（custom 回退取值与上下文记录用）。

    cooking_payload = payload.get("cooking", {}) or {}
    if cooking_payload.get("enabled"):
        state.cooking_enabled = True
        state.cooking_state = cooking_payload.get("state") or "COLLECTING_INGREDIENTS"
        state.current_recipe = cooking_payload.get("recipe") or state.current_recipe
        state.current_step = int(cooking_payload.get("step") or 0)
        state.current_servings = cooking_payload.get("servings")
        state.awaiting_servings = bool(cooking_payload.get("awaiting_servings", False))
        state.awaiting_cooking_exit_confirmation = bool(cooking_payload.get("awaiting_cooking_exit_confirmation", False))
        state.user_ingredients = list(cooking_payload.get("ingredients") or state.user_ingredients)
        state.completed_steps = list(cooking_payload.get("completed_steps") or state.completed_steps)

    refresh_model_runtime(runtime, state)
    if stt_provider == "browser":
        runtime.stt = None
        state.stt_runtime_key = get_stt_runtime_key("browser", stt_model, stt_api_key)
    else:
        apply_stt_update(runtime, state, stt_provider, stt_model, stt_api_key)

    # state.tts_api_key 在 set_session_tts 时已经 resolve_tts_api_key 做过 settings 回退；
    # 缺 key 时 build_tts_runtime 内部回退 DummyTTS（浏览器端合成），各 provider 行为统一
    apply_tts_update(runtime, state.tts_provider, state.tts_model, state.tts_api_key)
