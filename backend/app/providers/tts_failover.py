# -*- coding: utf-8 -*-
"""双线 TTS（故障自动切换）——2026-09-16 用户要求。

主 provider 先上，**首块音频超时（默认 5 秒）或首块之前就失败** → 自动改走备用 provider。
与电磁阀 failover（2026-09-15）同构的核心约束：

- **只在「还没产出任何 PCM」时才切换**：已经播出去半句了再换供应商，会出现
  「半句男声半句女声」/重复播放，比诚实失败更糟；
- 主线路失败/超时的**原因照实写日志**，便于定位到底是供应商挂了还是网络问题。

对外接口与普通 TTSProvider 完全一致（`stream_pcm_chunks` / `synthesize`），
所以 `tts_handler` 与前端都无感。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from ..logger import backend_log
from .base import TTSProvider


class FailoverTTS(TTSProvider):
    """主 + 备两个 TTS provider 的组合，带首块超时自动切换。"""

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        primary_name: str = "primary",
        fallback_name: str = "fallback",
        first_chunk_timeout: float = 5.0,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.first_chunk_timeout = max(1.0, float(first_chunk_timeout))

    # ---------------- 流式（主路径） ----------------

    async def stream_pcm_chunks(self, text: str, voice: str = None):
        t0 = time.monotonic()
        produced = 0
        agen: Optional[Any] = None
        switched = False

        try:
            agen = self.primary.stream_pcm_chunks(text, voice)
            while True:
                # 只对「首块」设超时（这是"供应商是不是挂了"的判据）；
                # 首块之后的块给宽松超时，避免长句被误判
                timeout = self.first_chunk_timeout if produced == 0 else 30.0
                try:
                    chunk = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    return
                if not chunk:
                    continue
                if produced == 0:
                    backend_log(
                        f"[TTS 双线] {self.primary_name} 首块就绪 +{time.monotonic() - t0:.2f}s"
                    )
                produced += 1
                yield chunk
            return
        except asyncio.TimeoutError:
            switched = True
            backend_log(
                f"[TTS 双线] {self.primary_name} 首块 {self.first_chunk_timeout:.1f}s 内未就绪"
                f" → 自动切换 {self.fallback_name}"
            )
        except Exception as e:
            if produced > 0:
                # 已经出过音频：不切换（换音色/重复播放更糟），如实抛出
                backend_log(
                    f"[TTS 双线] {self.primary_name} 中途失败（已出 {produced} 块，不切换）: "
                    f"{type(e).__name__}: {e}"
                )
                raise
            switched = True
            backend_log(
                f"[TTS 双线] {self.primary_name} 失败 → 自动切换 {self.fallback_name}: "
                f"{type(e).__name__}: {e}"
            )
        finally:
            if agen is not None:
                try:
                    await agen.aclose()      # 释放主线路连接
                except Exception:
                    pass

        if not switched:
            return

        fb_t0 = time.monotonic()
        n = 0
        async for chunk in self.fallback.stream_pcm_chunks(text, voice):
            if not chunk:
                continue
            n += 1
            if n == 1:
                backend_log(
                    f"[TTS 双线] {self.fallback_name} 首块就绪 +{time.monotonic() - fb_t0:.2f}s"
                    f"（从开始共 {time.monotonic() - t0:.2f}s）"
                )
            yield chunk
        if n == 0:
            backend_log(f"[TTS 双线] {self.fallback_name} 也未产出音频，本次播报失败")

    # ---------------- 非流式（兜底路径） ----------------

    async def synthesize(self, text: str, voice: str = None) -> bytes:
        """非流式合成：一次性拿到整段，切换不会造成"半句换音色"，可放心主→备。"""
        try:
            data = await self.primary.synthesize(text, voice)
            if data:
                return data
            backend_log(f"[TTS 双线] {self.primary_name} 返回空音频 → 改试 {self.fallback_name}")
        except Exception as e:
            backend_log(
                f"[TTS 双线] {self.primary_name} 非流式合成失败 → 改试 {self.fallback_name}: "
                f"{type(e).__name__}: {e}"
            )
        try:
            return await self.fallback.synthesize(text, voice)
        except Exception as e:
            backend_log(f"[TTS 双线] {self.fallback_name} 也失败: {type(e).__name__}: {e}")
            return b""

    async def synthesize_chunks(self, text: str, voice: str = None) -> list:
        audio = await self.synthesize(text, voice)
        return [audio] if audio else []
