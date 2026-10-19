"""
视觉处理模块
负责视频帧处理、危险检测、油烟检测
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import traceback
from typing import Any

from fastapi import WebSocket

from ..camera_rotate import rotation_angle, rotate_jpeg_bytes
from ..logger import backend_log
from ..session import SessionState
from . import (
    ws_send,
    send_latency,
    generate_tts,
    UNIFIED_DANGER_PROMPT,
    OIL_SMOKE_ANALYSIS_PROMPT,
    DANGER_ALERT_COOLDOWN,
    OIL_SMOKE_THRESHOLD,
    OIL_SMOKE_ALERT_COOLDOWN,
)


async def handle_video_frame(runtime: Any, ws: WebSocket, state: SessionState, jpeg_b64: str, frame_id: int, timestamp: int) -> None:
    """处理视频帧：入队并触发异步处理"""
    if state.video_frame_queue.full():
        try:
            state.video_frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    await state.video_frame_queue.put({
        "runtime": runtime,
        "ws": ws,
        "state": state,
        "jpeg_b64": jpeg_b64,
        "frame_id": frame_id,
        "timestamp": timestamp,
    })

    if not state.video_frame_processing:
        state.video_frame_processing = True
        asyncio.create_task(process_video_frame_queue(state, ws))


async def check_and_alert_danger(ws: WebSocket, state: SessionState, runtime: Any, analysis: str) -> None:
    """统一危险检测：解析AI分析结果，判断是否有烟雾、锅盖跳动或泡沫溢出危险，并发送警报"""
    has_danger = False
    danger_types = []
    description = ""
    severity = "medium"
    
    try:
        json_match = re.search(r'\{[^{}]*\}', analysis, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            has_danger = result.get("has_danger", False)
            danger_types = result.get("danger_types", [])
            description = result.get("description", "")
            severity = result.get("severity", "medium")
        else:
            danger_keywords = ["烟雾", "火情", "火焰", "冒烟", "起火", "着火",
                            "锅盖跳动", "锅盖晃动", "锅盖抖动", "锅盖剧烈", "锅盖飞", "锅盖震", 
                            "锅盖弹", "锅盖跳", "锅盖摇", "锅盖晃", "锅盖颠", "锅盖振",
                            "泡沫溢出", "泡沫飞溅", "泡沫涌出", "泡沫沸腾", "白沫溢出", "白沫飞溅",
                            "泡沫喷出", "白沫涌出", "泡沫翻滚", "沸腾溢出", "沸腾飞溅", "白沫喷",
                            "溢锅", "扑锅", "沸锅"]
            has_danger = any(kw in analysis for kw in danger_keywords)
            if has_danger:
                if any(kw in analysis for kw in ["明火", "火焰", "起火", "着火", "火情"]):
                    danger_types.append("明火")
                if any(kw in analysis for kw in ["烟雾", "冒烟"]):
                    danger_types.append("烟雾")
                if any(kw in analysis for kw in ["锅盖跳动", "锅盖晃动", "锅盖抖动", "锅盖剧烈", "锅盖飞", "锅盖震", "锅盖弹", "锅盖跳", "锅盖摇", "锅盖晃", "锅盖颠", "锅盖振"]):
                    danger_types.append("锅盖跳动")
                if any(kw in analysis for kw in ["泡沫溢出", "泡沫飞溅", "泡沫涌出", "泡沫沸腾", "白沫溢出", "白沫飞溅", "泡沫喷出", "白沫涌出", "泡沫翻滚", "沸腾溢出", "沸腾飞溅", "白沫喷", "溢锅", "扑锅", "沸锅"]):
                    danger_types.append("泡沫溢出")
            description = analysis[:100]
    except (json.JSONDecodeError, AttributeError) as e:
        backend_log(f"[统一危险检测] 解析AI结果失败: {e}，回退到关键词检测")
        danger_keywords = ["烟雾", "火情", "火焰", "冒烟", "起火", "着火",
                         "锅盖跳动", "锅盖晃动", "锅盖抖动", "锅盖剧烈", "锅盖飞", "锅盖震",
                         "锅盖弹", "锅盖跳", "锅盖摇", "锅盖晃", "锅盖颠", "锅盖振",
                         "泡沫溢出", "泡沫飞溅", "泡沫涌出", "泡沫沸腾", "白沫溢出", "白沫飞溅",
                         "泡沫喷出", "白沫涌出", "泡沫翻滚", "沸腾溢出", "沸腾飞溅", "白沫喷",
                         "溢锅", "扑锅", "沸锅"]
        has_danger = any(kw in analysis for kw in danger_keywords)
        if has_danger:
            if any(kw in analysis for kw in ["明火", "火焰", "起火", "着火", "火情"]):
                danger_types.append("明火")
            if any(kw in analysis for kw in ["烟雾", "冒烟"]):
                danger_types.append("烟雾")
            if any(kw in analysis for kw in ["锅盖跳动", "锅盖晃动", "锅盖抖动", "锅盖剧烈", "锅盖飞", "锅盖震", "锅盖弹", "锅盖跳", "锅盖摇", "锅盖晃", "锅盖颠", "锅盖振"]):
                danger_types.append("锅盖跳动")
            if any(kw in analysis for kw in ["泡沫溢出", "泡沫飞溅", "泡沫涌出", "泡沫沸腾", "白沫溢出", "白沫飞溅", "泡沫喷出", "白沫涌出", "泡沫翻滚", "沸腾溢出", "沸腾飞溅", "白沫喷", "溢锅", "扑锅", "沸锅"]):
                danger_types.append("泡沫溢出")
        description = analysis[:100]

    # 危险类型归一化：火焰/明火/起火/火情 统一为"明火"，不再并入"烟雾"
    backend_log(f"[统一危险检测] VLM原始 danger_types={danger_types}, analysis前200字={analysis[:200]}")
    normalized_types = []
    for dt in danger_types:
        dt = str(dt)
        if any(kw in dt for kw in ["明火", "火焰", "起火", "着火", "火情"]):
            if "明火" not in normalized_types:
                normalized_types.append("明火")
        elif dt == "烟雾":
            if "烟雾" not in normalized_types:
                normalized_types.append("烟雾")
        elif dt:
            if dt not in normalized_types:
                normalized_types.append(dt)
    danger_types = normalized_types
    backend_log(f"[统一危险检测] 归一化后 danger_types={danger_types}")

    if not has_danger:
        return
    
    current_time = time.time()
    if current_time - state.last_danger_alert_time < DANGER_ALERT_COOLDOWN:
        return
    
    state.last_danger_alert_time = current_time
    state.last_smoke_alert_time = current_time
    state.last_pot_alert_time = current_time
    
    if not danger_types:
        if any(kw in description for kw in ["明火", "火焰", "起火", "着火"]):
            danger_types.append("明火")
        elif "烟雾" in description or "烟" in description:
            danger_types.append("烟雾")
        if "锅盖" in description and ("跳动" in description or "晃动" in description or "抖动" in description):
            danger_types.append("锅盖跳动")
        if "泡沫" in description or "沫" in description or "溢" in description:
            danger_types.append("泡沫溢出")

    # 烟雾→排气扇联动（2026-09-20 用户指令）：排气扇已开就不做弹窗检测——
    # 烟正被排着，再弹窗/播报只会打断演示。仅"纯烟雾"（不含明火等其他危险，
    # 明火无论风扇开关都必须报警）且风扇确认开启时整体跳过：弹窗/语音/风扇
    # 询问都不发。风扇关着或状态查不到（miio 失败）一律按未开启处理，照常报警。
    fan_already_on = False
    fan_queried = False
    if danger_types == ["烟雾"]:
        try:
            from ..fan import fan_status as get_fan_status
            fan_result = await asyncio.to_thread(get_fan_status)
            fan_queried = bool(fan_result.get("success"))
            fan_already_on = fan_queried and bool(fan_result.get("is_on"))
            backend_log(
                f"[统一危险检测] 烟雾警报前查询排气扇: "
                f"success={fan_result.get('success')} is_on={fan_result.get('is_on')}"
            )
        except Exception as e:
            backend_log(f"[统一危险检测] 烟雾警报查询排气扇失败（按未开启处理）: {e}")
        if fan_already_on:
            backend_log("[统一危险检测] 排气扇已开启，跳过烟雾弹窗检测（弹窗/语音/询问均不发）")
            return

    # 危险类型 → 播报文案（弹窗与语音统一用自然表述）
    spoken_type_map = {
        "明火": "火焰过大",
        "烟雾": "烟雾",
        "锅盖跳动": "锅盖剧烈跳动",
        "泡沫溢出": "泡沫溢出",
    }
    spoken_types = [spoken_type_map.get(dt, dt) for dt in danger_types] or ["危险情况"]
    alert_text = f"检测到{'、'.join(spoken_types)}，请注意安全"

    danger_type_text = "、".join(spoken_types)
    # 仅锅盖跳动/泡沫溢出为高能危险（警笛+确认弹窗）；明火走普通警报条+语音播报
    is_high_energy = any(dt in danger_types for dt in ["锅盖跳动", "泡沫溢出"])
    
    if is_high_energy:
        await ws_send(ws, "danger_detected", {
            "message": f"⚠️ 高能警告：{alert_text}",
            "danger_types": danger_types,
            "description": description,
            "severity": severity,
            "is_high_energy": True,
            "timestamp": int(current_time * 1000),
        })
        backend_log(f"[统一危险检测] {alert_text}，已发送高能警报")
    else:
        await ws_send(ws, "danger_detected", {
            "message": alert_text,
            "danger_types": danger_types,
            "description": description,
            "severity": severity,
            "is_high_energy": False,
            "timestamp": int(current_time * 1000),
        })
        backend_log(f"[统一危险检测] {alert_text}，已发送警报")

    # 安全警报为系统高优先级播报：清除上一轮交互残留的打断状态——
    # 否则用户曾打断过 AI（或回声误触发打断）会让此后所有警报语音被静默跳过
    if state.interrupted:
        state.interrupted = False
        backend_log("[统一危险检测] 清除遗留打断状态，确保警报语音播报")
    # 独立 request_id：避免每轮警报续写进同一个聊天气泡
    alert_rid = f"smoke_alert_{int(current_time * 1000)}"

    # 语音播报危险警报（与警报条同时给出声音提醒，正常走 TTS 管道）
    try:
        await generate_tts(runtime, ws, state, alert_text, log_prefix="危险警报语音")
    except Exception as e:
        backend_log(f"[统一危险检测] 危险警报语音播报失败: {e}")

    if "烟雾" in danger_types or "明火" in danger_types:
        backend_log(f"[统一危险检测] 检测到烟雾/明火，准备询问是否开风扇... danger_types={danger_types}")
        try:
            if not fan_queried:
                from ..fan import fan_status as get_fan_status
                fan_status_result = get_fan_status()
                backend_log(f"[统一危险检测] 风扇状态查询结果: {fan_status_result}")
                fan_queried = bool(fan_status_result.get("success"))
                fan_already_on = fan_queried and bool(fan_status_result.get("is_on"))
            # 检测失败 → 不确定风扇是否开着，仍应询问用户
            # 检测成功且风扇已开 → 无需询问
            if fan_already_on:
                backend_log("[统一危险检测] 风扇已开启，跳过询问")
            else:
                fan_question_text = "检测到烟雾，是否开启排风扇？"
                # 设置待响应状态（供口语确认拦截，无超时——用户不回复就不开）
                state.smoke_pending_fan_response = True
                # 只发聊天消息 + TTS 语音，不发 smoke_fan_question（那是前端弹窗事件）
                await ws_send(ws, "assistant_delta", {"delta": fan_question_text, "request_id": alert_rid})
                await ws_send(ws, "assistant_final", {"text": fan_question_text, "request_id": alert_rid})
                await generate_tts(runtime, ws, state, fan_question_text, log_prefix="烟雾警报")
                backend_log("[烟雾警报] 已语音询问是否开风扇，等待用户语音回复（无超时）")
        except Exception as e:
            backend_log(f"[统一危险检测] 询问风扇时发生异常: {e}")


async def check_and_alert_oil_smoke(ws: WebSocket, state: SessionState, runtime: Any, analysis: str) -> None:
    """解析AI分析结果，判断油烟浓度是否超标，并询问用户是否开启排气扇"""
    if state.oil_smoke_pending_response:
        return
    
    oil_smoke_level = "无/低"
    confidence = 0.0
    description = ""
    
    try:
        json_match = re.search(r'\{[^{}]*\}', analysis, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            oil_smoke_level = result.get("oil_smoke_level", "无/低")
            confidence = result.get("confidence", 0.0)
            description = result.get("description", "")
        else:
            has_negative = any(word in analysis for word in ["无", "没有", "不明显", "少"])
            if "浓度低" in analysis or "油烟低" in analysis or "烟雾低" in analysis:
                has_negative = True
            if not has_negative and ("油烟" in analysis or "烟雾" in analysis):
                if "很" in analysis or "浓" in analysis or "大" in analysis:
                    oil_smoke_level = "很高"
                elif "高" in analysis or "明显" in analysis:
                    oil_smoke_level = "高"
                elif "中" in analysis or "一定" in analysis:
                    oil_smoke_level = "中"
            description = analysis[:100]
    except (json.JSONDecodeError, AttributeError) as e:
        backend_log(f"[油烟检测] 解析AI结果失败: {e}，回退到关键词检测")
        has_negative = any(word in analysis for word in ["无", "没有", "不明显", "少"])
        if "浓度低" in analysis or "油烟低" in analysis or "烟雾低" in analysis:
            has_negative = True
        if not has_negative and ("油烟" in analysis or "烟雾" in analysis):
            if "很" in analysis or "浓" in analysis or "大" in analysis:
                oil_smoke_level = "很高"
            elif "高" in analysis or "明显" in analysis:
                oil_smoke_level = "高"
            elif "中" in analysis or "一定" in analysis:
                oil_smoke_level = "中"
        description = analysis[:100]
    
    threshold_levels = ["无/低", "中", "高", "很高"]
    current_level_index = threshold_levels.index(oil_smoke_level) if oil_smoke_level in threshold_levels else 0
    threshold_index = threshold_levels.index(OIL_SMOKE_THRESHOLD) if OIL_SMOKE_THRESHOLD in threshold_levels else 2
    
    if current_level_index < threshold_index:
        return
    
    current_time = time.time()
    if current_time - state.last_oil_smoke_alert_time < OIL_SMOKE_ALERT_COOLDOWN:
        return
    
    state.last_oil_smoke_alert_time = current_time
    state.oil_smoke_pending_response = True
    
    backend_log(f"[油烟检测] 检测到油烟浓度{oil_smoke_level}，准备询问用户是否开启排气扇")
    
    alert_text = f"检测到当前油烟浓度较高，是否需要打开排气扇？"
    try:
        await ws_send(ws, "assistant_delta", {"delta": alert_text, "request_id": "oil_smoke_alert"})
        await ws_send(ws, "assistant_final", {"text": alert_text, "request_id": "oil_smoke_alert"})
        await generate_tts(runtime, ws, state, alert_text, log_prefix="油烟检测")
    except Exception as e:
        backend_log(f"[油烟检测] 发送TTS失败: {e}")


async def process_video_frame_queue(state: SessionState, ws: WebSocket = None) -> None:
    """异步处理视频帧队列"""
    try:
        while True:
            try:
                frame_data = await asyncio.wait_for(state.video_frame_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            runtime = frame_data["runtime"]
            ws = frame_data["ws"]
            frame_id = frame_data["frame_id"]
            # 视觉安全监测是持续型安全功能：不受对话打断状态影响——
            # 否则用户打断过一次 AI 后，interrupted 标志残留会让此后所有帧被
            # 静默丢弃，烟雾/明火检测与警报全部停摆，直到下一条聊天消息才复位
            if not state.vision_enabled:
                continue

            # 检测开关全关：整帧跳过，不调 VLM（兑现设置页"关闭检测可减少 API 调用与带宽消耗"）
            if not state.danger_alert_enabled and not state.oil_smoke_alert_enabled:
                backend_log(f"[视觉分析] 帧 {frame_id} 跳过：危险报警与油烟检测均已关闭")
                continue

            # 开关=行为开关（2026-09-06）：按开关选择分析范围——仅油烟开启时用油烟专用
            # 提示词（JSON 含 oil_smoke_level），关掉的检测彻底不参与本次分析
            analysis_prompt = (
                UNIFIED_DANGER_PROMPT
                if state.danger_alert_enabled
                else OIL_SMOKE_ANALYSIS_PROMPT
            )
            scope_label = (
                "危险+油烟" if (state.danger_alert_enabled and state.oil_smoke_alert_enabled)
                else ("仅危险" if state.danger_alert_enabled else "仅油烟")
            )

            try:
                image_bytes = base64.b64decode(frame_data["jpeg_b64"])
                # 本机画面方向（CAMERA_ROTATION）：推流是原始帧，显示由前端 CSS 旋转，
                # AI 看到的帧在这里转正——0.2fps 低频，编解码成本可忽略
                rotated = rotate_jpeg_bytes(image_bytes, rotation_angle())
                if rotated is not None:
                    image_bytes = rotated
                frame_hash = state.get_frame_hash(image_bytes)
                cached_analysis = state.get_cached_analysis(frame_hash)

                if cached_analysis is not None:
                    backend_log(f"[视觉分析] 帧 {frame_id} 使用缓存结果")
                    await ws_send(ws, "vision_analysis", {
                        "frame_id": frame_id,
                        "analysis": cached_analysis,
                        "timestamp": frame_data["timestamp"],
                    })
                    if state.danger_alert_enabled:
                        await check_and_alert_danger(ws, state, runtime, cached_analysis)
                    else:
                        backend_log(f"[视觉分析] 帧 {frame_id} 跳过危险检测（已禁用）")
                    if state.oil_smoke_alert_enabled:
                        await check_and_alert_oil_smoke(ws, state, runtime, cached_analysis)
                    continue

                if not state.should_analyze_frame(frame_hash):
                    backend_log(f"[视觉分析] 帧 {frame_id} 跳过重复帧")
                    continue

                if runtime.vlm is None:
                    backend_log(f"[视觉分析] 帧 {frame_id} 跳过：VLM 未初始化")
                    continue

                if hasattr(runtime.vlm, "analyze_image_stream"):
                    analysis_chunks = []
                    async for chunk in runtime.vlm.analyze_image_stream(image_bytes, analysis_prompt, enable_thinking=False):
                        analysis_chunks.append(chunk)
                        await ws_send(ws, "vision_analysis_delta", {
                            "frame_id": frame_id,
                            "delta": chunk,
                            "timestamp": frame_data["timestamp"],
                        })
                    analysis = "".join(analysis_chunks)
                    await ws_send(ws, "vision_analysis", {
                        "frame_id": frame_id,
                        "analysis": analysis,
                        "timestamp": frame_data["timestamp"],
                    })
                    state.add_observation(analysis[:300])
                    state.cache_analysis_result(frame_hash, analysis)
                    backend_log(f"[视觉分析] 帧 {frame_id} 流式分析完成（检测范围：{scope_label}）")
                    if state.danger_alert_enabled:
                        await check_and_alert_danger(ws, state, runtime, analysis)
                    else:
                        backend_log(f"[视觉分析] 帧 {frame_id} 跳过危险检测（已禁用）")
                    if state.oil_smoke_alert_enabled:
                        await check_and_alert_oil_smoke(ws, state, runtime, analysis)
                    else:
                        backend_log(f"[视觉分析] 帧 {frame_id} 跳过油烟检测（已禁用）")
                elif hasattr(runtime.vlm, "analyze_image"):
                    analysis = await runtime.vlm.analyze_image(image_bytes, analysis_prompt, enable_thinking=False)
                    await ws_send(ws, "vision_analysis", {
                        "frame_id": frame_id,
                        "analysis": analysis,
                        "timestamp": frame_data["timestamp"],
                    })
                    state.add_observation(analysis[:300])
                    state.cache_analysis_result(frame_hash, analysis)
                    backend_log(f"[视觉分析] 帧 {frame_id} 分析完成（检测范围：{scope_label}）")
                    if state.danger_alert_enabled:
                        await check_and_alert_danger(ws, state, runtime, analysis)
                    else:
                        backend_log(f"[视觉分析] 帧 {frame_id} 跳过危险检测（已禁用）")
                    if state.oil_smoke_alert_enabled:
                        await check_and_alert_oil_smoke(ws, state, runtime, analysis)
                    else:
                        backend_log(f"[视觉分析] 帧 {frame_id} 跳过油烟检测（已禁用）")
            except Exception as exc:
                backend_log(f"[视觉分析] 帧 {frame_id} 分析失败: {exc}\n{traceback.format_exc()}")
    except Exception as exc:
        backend_log(f"[视觉分析] 处理错误: {exc}\n{traceback.format_exc()}")
    finally:
        state.video_frame_processing = False
        if not state.video_frame_queue.empty():
            # 只有WebSocket仍连接时才继续处理
            try:
                if ws.client_state.name == "CONNECTED" and not state.video_frame_queue.empty():
                    await asyncio.sleep(1)
                    state.video_frame_processing = True
                    asyncio.create_task(process_video_frame_queue(state, ws))
            except Exception:
                pass  # WebSocket已关闭，不再递归
