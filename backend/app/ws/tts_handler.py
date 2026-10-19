"""
TTS处理模块
负责文本清理、流式TTS中继、音频生成
"""
from __future__ import annotations

import asyncio
import base64
import re
import traceback
from typing import Any

from fastapi import WebSocket

from ..config import settings
from ..logger import backend_log
from ..runtime_providers import normalize_tts_voice
from ..session import SessionState
from . import (
    ws_send,
    send_latency,
    get_tts_model,
    get_tts_api_key,
    get_tts_voice,
    apply_tts_update,
)
from .glasses_audio_handler import get_glasses_audio_ws


def clean_text_for_tts(text: str) -> str:
    """清理文本用于 TTS。规则已收敛到 app/tts_clean.py（全项目唯一权威版本），此处仅转发。"""
    from ..tts_clean import clean_text_for_tts as _clean

    return _clean(text)


def _should_flush_tts_buffer(buffer: str, next_delta: str | None = None) -> bool:
    """判断是否应该刷新 TTS 缓冲区。

    优先按标点（句号 / 逗号）断句，避免按固定长度在词中硬切导致朗读碎片化。
    """
    if not buffer.strip():
        return False
    # 句末标点（句号/问号/感叹号）立即刷新，停顿自然
    if buffer.endswith(("。", "！", "？", "!", "?", "\n")):
        return True
    if next_delta and next_delta[:1] in {"。", "！", "？", "!", "?", "\n"}:
        return True
    # 逗号处也可刷新，让长句在逗号处自然断句
    if buffer.endswith(("，", ",")):
        return True
    # 仅当累积过长且仍无标点可断时才硬切，阈值提高以减少碎片化
    if len(buffer) >= 80:
        return True
    return False


class _StreamingTTSRelay:
    """流式TTS中继器，实现LLM流式输出与TTS并行"""
    def __init__(
        self,
        ws: WebSocket,
        state: SessionState,
        provider_name: str,
        provider: Any,
        voice: str,
        request_id: str | None,
        log_prefix: str,
    ) -> None:
        self.ws = ws
        self.state = state
        self.provider_name = provider_name
        self.provider = provider
        self.voice = voice
        self.request_id = request_id
        self.log_prefix = log_prefix
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=10)
        self.worker: asyncio.Task | None = None
        self.chunk_index = 0
        self.first_chunk_sent = False
        self.started = False
        self.closed = False

    async def open(self) -> None:
        if not self.provider:
            return
        try:
            if hasattr(self.provider, 'stream_pcm_chunks'):
                pass
        except Exception as exc:
            backend_log(f"[TTS] 初始化音频流失败: {exc}")

    async def enqueue(self, text: str) -> None:
        cleaned_text = clean_text_for_tts(text)
        if not cleaned_text or self.state.interrupted:
            return
        if self.closed:
            # close 之后的 enqueue 直接丢弃：worker 即将随 None 哨兵退出，
            # 此时入队只会白占队列、永远不会被播出去
            return
        # worker 死过（异常曾跳出到外层）时 task 对象仍 truthy 但已 done，
        # 必须先清掉才能在下方重启；否则队列无人消费、塞满 10 条后
        # await put 永久阻塞调用方（llm_handler 的 LLM 流式循环），整条回复卡死。
        # 放在 put 之前：先重启 worker 让它消费，put 才不至于在满队上无限等。
        if self.worker and self.worker.done():
            self.worker = None
        await self.queue.put(cleaned_text)
        if not self.worker:
            self.worker = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.queue.put(None)
        if self.worker:
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            # 引用一并清空：残留已结束的 task 对象会让后续 enqueue 的 done 判断落空
            self.worker = None

    async def _run(self) -> None:
        current_text = ""
        failed = False
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    break
                if self.state.interrupted:
                    break

                current_text = item

                if not self.started:
                    self.started = True
                    await send_latency(
                        self.ws,
                        self.request_id,
                        "tts_start",
                        {"provider": self.provider_name, "model": getattr(self.provider, "model", None)},
                    )

                # 逐段容错：单段 provider 异常只报一次错并跳过该段，worker 继续消费下一段。
                # 不能让异常跳出到外层——那会杀死整个 worker，而 self.worker 仍是已结束的
                # task 对象（truthy），enqueue 永远不会重启它，队列塞满后 put 永久阻塞 LLM 流式循环。
                try:
                    self.chunk_index, self.first_chunk_sent = await _stream_tts_pcm_chunks(
                        self.ws,
                        self.state,
                        self.provider,
                        item,
                        self.voice,
                        request_id=self.request_id,
                        chunk_index_start=self.chunk_index,
                        first_chunk_sent=self.first_chunk_sent,
                        trace_text=item,
                    )
                except Exception as exc:
                    failed = True
                    backend_log(f"[{self.log_prefix}] 流式 TTS relay 单段失败: {exc}\n{traceback.format_exc()}")
                    if self.state.interrupted:
                        break
                    await _send_tts_error(
                        self.ws,
                        self.request_id,
                        self.provider_name,
                        str(exc),
                        text=current_text,
                        chunk_count=self.chunk_index,
                    )
                    continue
        except Exception as exc:
            # 兜底网：循环体外非 provider 异常（如 send_latency 失败）。worker 虽死，
            # 但 enqueue 里的 done 检查会在下一段入队时重启新 worker，不再永久卡死。
            failed = True
            backend_log(f"[{self.log_prefix}] 流式 TTS relay 失败: {exc}\n{traceback.format_exc()}")
            if not self.state.interrupted:
                await _send_tts_error(
                    self.ws,
                    self.request_id,
                    self.provider_name,
                    str(exc),
                    text=current_text,
                    chunk_count=self.chunk_index,
                )
        finally:
            # 只要开过流且未被用户打断，无论成败都必须发 tts_pcm_end 收尾——
            # 前端靠它结束 PCM 流，缺了只能等 30 秒挂起超时兜底丢弃（且该超时
            # 只在别的流收尾时才被评估）。failed 标记进 payload 供排查，前端忽略额外字段。
            if self.started and not self.state.interrupted:
                end_payload = {"total_chunks": self.chunk_index, "sample_rate": 24000, "request_id": self.request_id}
                if failed:
                    end_payload["failed"] = True
                await ws_send(self.ws, "tts_pcm_end", end_payload)
                if getattr(self.state, "audio_source", "local") == "glasses":
                    await _forward_tts_to_glasses("tts_pcm_end", end_payload)


async def _forward_tts_to_glasses(event_type: str, payload: dict) -> None:
    """如果当前选择了眼镜扬声器，把 TTS 事件转发给眼镜音频 WebSocket"""
    glasses_ws = get_glasses_audio_ws()
    if glasses_ws is None:
        return
    try:
        await glasses_ws.send_json({"type": event_type, "payload": payload})
    except Exception as exc:
        backend_log(f"[TTS转发眼镜] 失败: {exc}")


async def _stream_tts_pcm_chunks(
    ws: WebSocket,
    state: SessionState,
    provider: Any,
    text: str,
    voice: str,
    request_id: str | None = None,
    chunk_index_start: int = 0,
    first_chunk_sent: bool = False,
    trace_text: str | None = None,
) -> tuple[int, bool]:
    """流式输出TTS PCM块。

    2026-09-16 攒块优化：TTS 是**按句合成**的（要等 LLM 吐完一句才能合成下一句），
    音频因此「一阵一阵」到达——实测有些批次整体只有 **0.41x 实时**（慢于朗读），
    前端播放队列不断断粮（`underrun` 连续刷屏）。
    这里把**首块立即推**（不牺牲首播延迟）、**后续块攒到 ≥BATCH_BYTES 再推**，
    让前端一次拿到更成批的数据；配合前端 `FIRST_CHUNK_LEAD_SECONDS` 起始缓冲，
    即可把断粮抹平。
    """
    chunk_index = chunk_index_start
    use_glasses_audio = getattr(state, "audio_source", "local") == "glasses"
    text_trace = (trace_text or text).replace("\r", " ").replace("\n", " ") if (trace_text or text) else ""
    # 攒块阈值：0.8 秒 @24kHz 16bit 单声道 = 38400 字节
    BATCH_BYTES = int(24000 * 2 * 0.8)
    pending = bytearray()

    async def _send_one(data: bytes) -> None:
        nonlocal chunk_index, first_chunk_sent
        backend_log(
            f"[TTS][WS发送] request_id={request_id} pcm_chunk={chunk_index + 1} bytes={len(data)} text={text_trace}"
        )
        payload = {
            "pcm_b64": base64.b64encode(data).decode("utf-8"),
            "sample_rate": 24000,
            "chunk_index": chunk_index,
            "request_id": request_id,
            "tts_text": trace_text or text,
        }
        await ws_send(ws, "tts_pcm", payload)
        if use_glasses_audio:
            await _forward_tts_to_glasses("tts_pcm", payload)
        if not first_chunk_sent:
            first_chunk_sent = True
            await send_latency(ws, request_id, "tts_payload_ready")
        chunk_index += 1

    async for pcm_chunk in provider.stream_pcm_chunks(text, voice):
        if state.interrupted:
            break
        if not pcm_chunk:
            continue
        if not first_chunk_sent:
            # 首块立即推：让用户尽快听到开口，不牺牲首播延迟
            await _send_one(pcm_chunk)
            continue
        pending += pcm_chunk
        if len(pending) >= BATCH_BYTES:
            await _send_one(bytes(pending))
            pending.clear()

    if pending and not state.interrupted:
        await _send_one(bytes(pending))   # 收尾：把不足一攒的余量推掉

    return chunk_index, first_chunk_sent


async def _send_tts_error(
    ws: WebSocket,
    request_id: str | None,
    provider: str,
    message: str,
    text: str = "",
    chunk_count: int = 0,
) -> None:
    """发送TTS错误消息"""
    await ws_send(
        ws,
        "tts_error",
        {
            "request_id": request_id,
            "provider": provider,
            "message": message,
            "text": text,
            "chunk_count": chunk_count,
        },
    )


async def stream_xiaomi_tts(
    ws: WebSocket,
    state: SessionState,
    provider: Any,
    text: str,
    voice: str,
    request_id: str | None = None,
    trace_text: str | None = None,
) -> None:
    """流式输出小米TTS音频。

    2026-09-16：改为统一委托 `_stream_tts_pcm_chunks`（内含「首块立即推 + 后续攒到
    0.8 秒音频再推」的攒块优化）。原先这里每块 0.32 秒即推，音频节奏跟着 LLM
    逐句出字走，实测个别批次整体只有 0.41x 实时 → 前端播放队列反复断粮（underrun）。
    """
    chunk_index, _ = await _stream_tts_pcm_chunks(
        ws, state, provider, text, voice, request_id=request_id, trace_text=trace_text,
    )
    if not state.interrupted:
        end_payload = {"total_chunks": chunk_index, "sample_rate": 24000, "request_id": request_id}
        await ws_send(ws, "tts_pcm_end", end_payload)
        if getattr(state, "audio_source", "local") == "glasses":
            await _forward_tts_to_glasses("tts_pcm_end", end_payload)


async def stream_dashscope_tts(
    ws: WebSocket,
    state: SessionState,
    provider: Any,
    text: str,
    voice: str,
    request_id: str | None = None,
    trace_text: str | None = None,
) -> None:
    """流式输出DashScope TTS音频。

    2026-09-16：同 `stream_xiaomi_tts`，统一委托攒块推流的通用实现。
    """
    chunk_index, _ = await _stream_tts_pcm_chunks(
        ws, state, provider, text, voice, request_id=request_id, trace_text=trace_text,
    )
    if not state.interrupted:
        end_payload = {"total_chunks": chunk_index, "sample_rate": 24000, "request_id": request_id}
        await ws_send(ws, "tts_pcm_end", end_payload)
        if getattr(state, "audio_source", "local") == "glasses":
            await _forward_tts_to_glasses("tts_pcm_end", end_payload)


async def generate_tts(
    runtime: Any,
    ws: WebSocket,
    state: SessionState,
    text: str,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    log_prefix: str = "TTS",
    voice_override: str | None = None,
    request_id: str | None = None,
) -> None:
    """生成TTS音频，支持多种provider"""
    effective_provider = provider or state.tts_provider or settings.tts_provider
    effective_model = get_tts_model(effective_provider, model or state.tts_model)
    effective_api_key = get_tts_api_key(effective_provider, api_key or state.tts_api_key)

    if state.interrupted:
        backend_log(f"[{log_prefix}] 已被打断，跳过 TTS")
        return

    if effective_provider == "browser":
        # 眼镜扬声器模式下，browser TTS 后端不生成音频，眼镜收不到声音
        # 临时降级到 dashscope（如果有 API Key）或 siliconflow
        if getattr(state, "audio_source", "local") == "glasses":
            if settings.dashscope_api_key:
                effective_provider = "dashscope"
                effective_model = settings.dashscope_tts_model or "cosyvoice-v2"
                effective_api_key = settings.dashscope_api_key
                backend_log(f"[{log_prefix}] 眼镜模式下 browser TTS 临时降级到 dashscope")
            elif settings.siliconflow_api_key:
                effective_provider = "siliconflow"
                effective_model = settings.siliconflow_tts_model
                effective_api_key = settings.siliconflow_api_key
                backend_log(f"[{log_prefix}] 眼镜模式下 browser TTS 临时降级到 siliconflow")
            else:
                backend_log(f"[{log_prefix}] 眼镜模式下 browser TTS 无可用后端 provider，跳过")
                return
        else:
            backend_log(f"[{log_prefix}] 当前使用浏览器 TTS，后端不生成音频")
            return

    if effective_provider in {"siliconflow", "dashscope", "xiaomi"} and not effective_api_key:
        backend_log(f"[{log_prefix}] 缺少 API Key，跳过 TTS，provider={effective_provider}")
        return

    cleaned_text = clean_text_for_tts(text)
    if not cleaned_text:
        backend_log(f"[{log_prefix}] 文本为空，跳过 TTS")
        return

    try:
        apply_tts_update(runtime, effective_provider, effective_model, effective_api_key)
        # voice 先过契约函数 normalize_tts_voice：xiaomi 非法音色会自动回退默认并打日志；
        # 契约结果为 None（provider 无白名单且无配置值）才落到 get_tts_voice 兜底
        voice = normalize_tts_voice(effective_provider, voice_override or state.tts_voice) or get_tts_voice(effective_provider)
        backend_log(f"[{log_prefix}] 准备生成音频, provider={effective_provider}, model={effective_model}, voice={voice}")
        await send_latency(ws, request_id, "tts_start", {"provider": effective_provider, "model": effective_model})

        if effective_provider == "dashscope" and hasattr(runtime.tts, "stream_pcm_chunks"):
            await stream_dashscope_tts(ws, state, runtime.tts, cleaned_text, voice, request_id=request_id, trace_text=cleaned_text)
            backend_log(f"[{log_prefix}] DashScope 流式音频已发送")
            return

        if effective_provider == "xiaomi" and hasattr(runtime.tts, "stream_pcm_chunks"):
            await stream_xiaomi_tts(ws, state, runtime.tts, cleaned_text, voice, request_id=request_id, trace_text=cleaned_text)
            backend_log(f"[{log_prefix}] Xiaomi 流式音频已发送")
            return

        audio_data = await runtime.tts.synthesize(cleaned_text, voice)
        if audio_data and not state.interrupted:
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            payload = {"wav_b64": audio_b64, "request_id": request_id}
            await ws_send(ws, "tts_audio", payload)
            if getattr(state, "audio_source", "local") == "glasses":
                await _forward_tts_to_glasses("tts_audio", payload)
            await send_latency(ws, request_id, "tts_payload_ready")
            backend_log(f"[{log_prefix}] 音频已发送, 大小: {len(audio_b64)} bytes")
    except Exception as exc:
        backend_log(f"[{log_prefix}] 失败: {exc}\n{traceback.format_exc()}")
        if effective_provider in {"xiaomi", "dashscope"} and not state.interrupted:
            await _send_tts_error(
                ws,
                request_id,
                effective_provider,
                str(exc),
                text=cleaned_text,
                chunk_count=0,
            )
