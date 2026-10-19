"""
WebSocket 连接管理模块
负责 WebSocket 连接生命周期和主循环
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..config import settings
from ..logger import backend_log
from ..session import SessionState
from ..chat_storage import STORAGE_DIR, load_messages
from .. import runtime_providers as provider_runtime
from . import AppRuntime
from .router import EVENT_HANDLERS, handle_unknown_event
from . import ws_send


def _restore_context_engine(state: SessionState) -> None:
    """从持久化聊天记录恢复 AI 记忆（配对的 user/assistant 轮次）

    只恢复最近的记录，末尾未配对的 user 忽略（属于未完成的一轮）。
    """
    try:
        messages = load_messages(state.session_id)
    except Exception as exc:
        backend_log(f"[记忆] 加载会话 {state.session_id} 记录失败: {exc}")
        return
    if not messages:
        return
    # 只恢复最近 100 条，避免历史过长拖慢首次响应
    messages = messages[-100:]
    engine = state.context_engine
    restored = 0
    pending_user = None
    for msg in messages:
        role = msg.get("role", "")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            pending_user = text
        elif role in ("assistant", "assistant_stream"):
            if pending_user is not None:
                engine.record_user_message(session_id=state.session_id, content=pending_user)
                engine.record_assistant_message(session_id=state.session_id, content=text)
                restored += 1
                pending_user = None
    backend_log(f"[记忆] 会话 {state.session_id} 恢复 {restored} 轮历史")

    # 向量记忆索引预热：为恢复的轮次批量补 embedding（一次 API 调用，后台执行，
    # 不阻塞 WS 建连；失败自动回落启发式压缩）
    if restored:
        try:
            asyncio.get_running_loop().create_task(
                engine.warm_vector_index(state.session_id)
            )
        except RuntimeError:
            pass  # 无事件循环（非 async 上下文测试）时跳过预热


# ===== 确认门标志持久化（B2：会话重连丢控制状态 → LLM 自演硬件动作） =====
# 只持久化盐量脉冲 / 油温盯守两个确认门：它们独立于 cooking_state、由 llm_handler
# 确定性拦截链路消费，且前端 hello 不镜像（useCookingMode 只恢复做饭态四件套
# awaiting_servings / exit / step_guide / ingredients_ready，见 apply_hello_settings）。
# 两个门自带时间戳，过期判断由拦截链路自查（恢复时原样带回时间戳，超时自然失效）。
_GATE_FIELDS = (
    "awaiting_salt_pulse_confirmation",
    "salt_pulse_grams",
    "salt_pulse_requested_at",
    "awaiting_oil_watch_confirm",
    "oil_watch_proposed_target",
    "oil_watch_proposed_at",
)

_SESSION_GATES_FILE = STORAGE_DIR.parent / "session_gates.json"


def _read_gate_store() -> dict:
    try:
        with open(_SESSION_GATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_session_gates(state: SessionState) -> None:
    """新连接恢复聊天历史时一并恢复确认门标志（断开前的快照）。"""
    gates = _read_gate_store().get(state.session_id)
    if not isinstance(gates, dict):
        return
    restored = []
    for name in _GATE_FIELDS:
        if name in gates:
            setattr(state, name, gates[name])
            if gates[name]:
                restored.append(name)
    if restored:
        backend_log(f"[确认门] 会话 {state.session_id} 恢复: {restored}")


def _save_session_gates(state: SessionState) -> None:
    """连接退出时把确认门标志快照落盘（按 session_id 分桶；全部关闭时清除该桶）。

    best-effort：多连接同 session 并发退出时读-改-写可能互相覆盖，
    演示场景单连接，可接受。
    """
    try:
        store = _read_gate_store()
        if getattr(state, "awaiting_salt_pulse_confirmation", False) or getattr(
            state, "awaiting_oil_watch_confirm", False
        ):
            gates = {name: getattr(state, name, None) for name in _GATE_FIELDS}
            gates["saved_at"] = time.time()
            store[state.session_id] = gates
        else:
            store.pop(state.session_id, None)
        with open(_SESSION_GATES_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        backend_log(f"[确认门] 持久化失败: {exc}")


# 全局会话连接注册表：session_id -> (ws, state)。
# 供全局后台监控（如油温超温报警）向在线会话广播弹窗/语音；连接建立注册、断开注销。
SESSION_CONNECTIONS: dict[str, tuple[Any, SessionState]] = {}


def register_session_connection(session_id: str, ws: Any, state: SessionState) -> None:
    # 同一 session_id 重连时新连接覆盖旧条目属预期：旧连接退出时的注销带身份
    # 校验（见 unregister_session_connection），不会误删这里的新注册；旧 ws 的
    # 清理（取消任务、停油温监控、关连接）由它自己的 ws_loop finally 走，这里
    # 只覆盖指针、不代关旧 ws，避免与旧连接的清理流程赛跑
    SESSION_CONNECTIONS[session_id] = (ws, state)


def unregister_session_connection(session_id: str, ws: Any = None) -> None:
    """注销会话连接。带 ws 时做身份校验：只有注册表里存的仍是这个连接（is 比对）才删。

    竞态背景：同一 session_id 重连后新连接 register 已覆盖旧条目，旧连接退出时
    若无条件 pop 会把新连接的注册一并删掉——后续广播（油温超温警报等）按
    session_id 找不到连接而静默丢失。ws=None 保持旧的无条件语义（兼容既有
    调用方/测试）；连接生命周期内（ws_loop finally）的注销必须传 ws。
    """
    entry = SESSION_CONNECTIONS.get(session_id)
    if entry is None:
        return
    if ws is not None and entry[0] is not ws:
        backend_log(f"[WS] 注销跳过：session={session_id} 注册表已被新连接覆盖，旧连接退出不误删")
        return
    SESSION_CONNECTIONS.pop(session_id, None)


def iter_session_connections() -> dict[str, tuple[Any, SessionState]]:
    """返回注册表快照（遍历期间连接增删不影响本次遍历）。"""
    return dict(SESSION_CONNECTIONS)


async def ws_loop(ws: WebSocket, session_id: str = "default") -> None:
    """WebSocket 主循环：接收消息并分发到对应处理器"""
    state = SessionState()
    state.session_id = session_id
    state.max_observations = settings.vision_max_observations
    provider_runtime.set_session_tts(
        state,
        settings.tts_provider,
        provider_runtime.resolve_tts_model(settings.tts_provider),
        provider_runtime.resolve_tts_api_key(settings.tts_provider),
        provider_runtime.resolve_tts_voice(settings.tts_provider),
    )
    runtime = AppRuntime()

    await ws.accept()
    register_session_connection(session_id, ws, state)
    backend_log(f"[WS] WebSocket连接已建立 session={state.session_id}")
    # 恢复该会话的 AI 记忆（从持久化聊天记录）
    _restore_context_engine(state)
    # 恢复断开前的确认门标志（盐量脉冲/油温盯守），避免重连后确认门丢失
    _load_session_gates(state)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=300.0)
            except asyncio.TimeoutError:
                backend_log("[WS] 300秒无消息，关闭连接")
                break
            typ = msg.get("type", "")
            payload = msg.get("payload", {}) or {}
            payload["__event_type__"] = typ
            handler = EVENT_HANDLERS.get(typ, handle_unknown_event)
            try:
                await handler(runtime, ws, state, payload)
            except Exception as exc:
                backend_log(f"[WS] handler [{typ}] 异常: {repr(exc)}\n{traceback.format_exc()}")
                try:
                    await ws_send(ws, "error", {"message": f"Handler error: {exc}"})
                except Exception:
                    pass
    except WebSocketDisconnect as exc:
        backend_log(f"[WS] WebSocket连接断开: code={exc.code}")
    except Exception as exc:
        backend_log(f"[WS] ws_loop异常: {repr(exc)}\n{traceback.format_exc()}")
        try:
            await ws_send(ws, "error", {"message": f"ws_loop error: {exc}"})
        except Exception:
            pass
    finally:
        # 带上本连接对象做身份校验：同 session_id 重连后，旧连接退出不得删掉新连接的注册
        unregister_session_connection(session_id, ws)
        # 确认门标志快照落盘（盐量脉冲/油温盯守，供同 session_id 重连后恢复）
        _save_session_gates(state)
        # 取消当前任务
        if state.current_task and not state.current_task.done():
            try:
                state.current_task.cancel()
            except Exception:
                pass
        
        # 取消油烟超时定时器
        if state.oil_smoke_timeout_timer and not state.oil_smoke_timeout_timer.done():
            try:
                state.oil_smoke_timeout_timer.cancel()
            except Exception:
                pass

        # 取消烟雾风扇询问超时定时器
        if state.smoke_timeout_timer and not state.smoke_timeout_timer.done():
            try:
                state.smoke_timeout_timer.cancel()
            except Exception:
                pass

        # 停止油温监测任务
        try:
            from ..oil_watch import stop_oil_watch
            stop_oil_watch(state)
        except Exception:
            pass
        
        # 清理视频帧处理任务
        state.video_frame_processing = False
        # 清空视频帧队列
        while not state.video_frame_queue.empty():
            try:
                state.video_frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        # 清理音频缓冲区
        state.stream_audio_chunks.clear()
        state.audio_chunks.clear()

        # 关闭云端流式 STT 会话（若有，防悬空连接）
        stt_stream = getattr(state, "stt_stream", None)
        if stt_stream is not None:
            state.stt_stream = None
            try:
                await stt_stream.stop_and_collect(timeout=2.0)
            except Exception:
                pass

        try:
            if ws.client_state == WebSocketState.CONNECTED or ws.client_state == WebSocketState.CONNECTING:
                await ws.close()
        except Exception:
            pass
        
        backend_log("[WS] 连接清理完成")
