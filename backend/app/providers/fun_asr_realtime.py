"""Fun-ASR Realtime —— 阿里云百炼实时语音识别（DashScope SDK）。

模型 ID：`fun-asr-realtime-2026-02-28`（北京地域），与
`qwen-audio-3.0-asr-flash-streaming` 同属 DashScope 的 Recognition 通道：
SDK 内部走 WebSocket 双向流式（发音频帧 → 实时回文本）。

两种形态并存：
- `transcribe_pcm16`：一次性拿到整段音频再提交（`Recognition.call()` 同步调用，
  经线程池包成 async）——唤醒词云端兜底的整段上传走这条。
- `create_stream`：流式会话（`start()` + `send_audio_frame()` + `stop()`），
  前端收集期逐帧上行，说话期间实时回 partial（2026-09-14 输入框云端优先显示）。

注意：这是 DashScope **自有**协议，与 `qwen3_asr_realtime.py`
（OpenAI Realtime 兼容 WS，用于 qwen3-asr-flash-realtime-*）不是同一条通道，
模型名不能互用。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import wave
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Optional

from ..logger import backend_log


class FunASRStreamSession:
    """一轮识别的流式会话：喂 PCM 帧 → 实时回调 partial → stop 后收 final。

    SDK 回调在其内部线程触发，统一经 run_coroutine_threadsafe 桥回事件循环；
    喂帧/收口用 asyncio.Lock 串行化，保证帧序。
    """

    def __init__(self, api_key: str, model: str, sample_rate: int = 16000,
                 partial_min_interval: float = 0.3):
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self.partial_min_interval = partial_min_interval

        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._recognition: Any = None
        self._started = False
        self._stopped = False
        self._on_partial: Callable[[str], Awaitable[None]] = lambda text: None

        self._sentence_final = ""   # 已收口句子的累积文本
        self._last_partial = ""     # 最近一次下发的 partial（含累积）
        self._last_partial_at = 0.0
        self._error_text = ""
        self._done = asyncio.Event()
        # 空闲看门狗读取：前端异常退出收不到 audio_stream_end 时据此回收会话
        self.last_feed_monotonic = time.monotonic()
        # [调试] 喂帧/回调事件计数（排查流式会话 EmptyAudio 问题）
        self._feed_frames = 0
        self._feed_bytes = 0
        self._partial_events = 0

    # ---------- SDK 回调（SDK 内部线程调用） ----------
    def _build_callback(self) -> Any:
        from dashscope.audio.asr import RecognitionCallback, RecognitionResult

        session = self

        class _Callback(RecognitionCallback):
            def on_open(self) -> None:
                backend_log("[FunASR 流式][调试] on_open 连接已建立")

            def on_complete(self) -> None:
                # ⚠️ SDK 调用 on_complete() 不带参数（签名由基类决定）；
                # 带多余的 result 参数会让 worker 线程 TypeError 崩溃，
                # final 永远收不到（2026-09-18 排查：流式上线以来一直如此，
                # 旧烟测喂静音走 on_error 路径签名恰好对，未暴露）
                backend_log(
                    f"[FunASR 流式][调试] on_complete（喂帧 {session._feed_frames} 帧/"
                    f"{session._feed_bytes}B, partial事件 {session._partial_events}）"
                )
                session._signal_done()

            def on_error(self, result: Any) -> None:
                try:
                    session._error_text = getattr(result, "message", "") or "recognition error"
                except Exception:
                    session._error_text = "recognition error"
                backend_log(f"[FunASR 流式][调试] on_error: {session._error_text}")
                session._signal_done()

            def on_event(self, result: Any) -> None:
                # 该 SDK 版本基类 on_event 是空操作，不会分发到
                # on_result_changed/on_sentence_end——必须在这里自己判句尾/中间态
                try:
                    s = result.get_sentence()
                except Exception:
                    return
                text = session._sentence_text_from(s)
                if not text:
                    return
                is_end = False
                try:
                    is_end = bool(RecognitionResult.is_sentence_end(s))
                except Exception:
                    pass
                if is_end:
                    backend_log(f"[FunASR 流式][调试] 句尾: {text!r}")
                    session._sentence_final += text
                    session._emit_partial(session._sentence_final, force=True)
                else:
                    session._partial_events += 1
                    if session._partial_events <= 3:
                        backend_log(
                            f"[FunASR 流式][调试] partial #{session._partial_events}: {text!r}"
                        )
                    session._emit_partial((session._sentence_final + text).strip())

            def on_closed(self) -> None:
                pass

        return _Callback()

    @staticmethod
    def _sentence_text_from(s: Any) -> str:
        if isinstance(s, dict):
            return (s.get("text") or "").strip()
        if isinstance(s, list):
            return "".join(
                (x.get("text") or "") if isinstance(x, dict) else str(x)
                for x in s
            ).strip()
        return s.strip() if isinstance(s, str) else ""

        return _Callback()

    @staticmethod
    def _sentence_text(result: Any) -> str:
        try:
            s = result.get_sentence()
        except Exception:
            return ""
        if isinstance(s, dict):
            return (s.get("text") or "").strip()
        if isinstance(s, list):
            return "".join(
                (x.get("text") or "") if isinstance(x, dict) else str(x)
                for x in s
            ).strip()
        return s.strip() if isinstance(s, str) else ""

    def _emit_partial(self, text: str, force: bool = False) -> None:
        if not text or text == self._last_partial:
            return
        now = time.monotonic()
        if not force and now - self._last_partial_at < self.partial_min_interval:
            return
        self._last_partial_at = now
        self._last_partial = text
        try:
            asyncio.run_coroutine_threadsafe(self._aemit(text), self._loop)
        except RuntimeError:
            pass  # 事件循环已关闭（连接 teardown），静默丢弃

    async def _aemit(self, text: str) -> None:
        try:
            await self._on_partial(text)
        except Exception:
            pass  # 下发失败（ws 断开等）不影响会话本身

    def _signal_done(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._done.set)
        except RuntimeError:
            pass

    # ---------- 会话生命周期 ----------
    async def start(self, on_partial: Callable[[str], Awaitable[None]]) -> None:
        """建立到百炼的流式 WS 连接（阻塞 SDK 调用，放线程池跑）"""
        self._on_partial = on_partial

        def _start() -> None:
            import dashscope
            from dashscope.audio.asr import Recognition

            dashscope.api_key = self.api_key
            recognition = Recognition(
                model=self.model,
                format="pcm",
                sample_rate=self.sample_rate,
                callback=self._build_callback(),
            )
            recognition.start()
            self._recognition = recognition
            self._started = True

        await asyncio.to_thread(_start)

    async def feed(self, pcm: bytes) -> None:
        self.last_feed_monotonic = time.monotonic()
        if self._stopped or not pcm:
            return
        async with self._lock:  # 串行化，保证帧序
            recognition = self._recognition
            if recognition is None or not self._started:
                return
            try:
                self._feed_frames += 1
                self._feed_bytes += len(pcm)
                if self._feed_frames % 16 == 1:
                    backend_log(
                        f"[FunASR 流式][调试] 喂帧 #{self._feed_frames}（累计 {self._feed_bytes}B，本帧 {len(pcm)}B）"
                    )
                await asyncio.to_thread(recognition.send_audio_frame, pcm)
            except Exception as exc:
                self._error_text = self._error_text or str(exc)
                self._signal_done()
                raise

    async def stop_and_collect(self, timeout: float = 6.0) -> str:
        """收口：发停止信号，等尾部结果，返回最终文本（失败返回空串）"""
        self._stopped = True
        async with self._lock:
            recognition = self._recognition
            if recognition is not None and self._started:
                try:
                    await asyncio.to_thread(recognition.stop)
                except Exception as exc:
                    self._error_text = self._error_text or str(exc)
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            backend_log(f"[FunASR 流式] 收口等待超时（{timeout}s），以已收文本兜底")
        if self._error_text:
            backend_log(f"[FunASR 流式] 会话异常: {self._error_text}")
        text = self._sentence_final.strip()
        if not text and not self._error_text:
            # 尾句没等到句尾事件：拿最后的中间结果兜底（比丢内容强）
            text = self._last_partial.strip()
        return text


class FunASRRealtime:
    """阿里云 Fun-ASR 实时语音识别（STTProvider 同签名：transcribe_pcm16）"""

    def __init__(self, api_key: str, model: str = "fun-asr-realtime-2026-02-28"):
        self.api_key = api_key
        self.model = model

    def supports_streaming(self) -> bool:
        return True

    def create_stream(self, sample_rate: int = 16000) -> FunASRStreamSession:
        """创建一轮流式识别会话（audio_handler 逐帧喂入，partial 实时回调）"""
        return FunASRStreamSession(self.api_key, self.model, sample_rate)

    # ---------- 同步部分（在线程池里跑，SDK 是阻塞式 API） ----------
    def _transcribe_sync(self, wav_path: str, sample_rate: int) -> str:
        import dashscope
        from dashscope.audio.asr import Recognition

        # SDK 读全局 key；与项目 dashscope_tts 的设置方式保持一致
        dashscope.api_key = self.api_key

        recognition = Recognition(
            model=self.model,
            callback=None,
            format="wav",
            sample_rate=sample_rate,
        )
        result = recognition.call(wav_path)

        if result.status_code != HTTPStatus.OK:
            backend_log(
                f"[FunASR] 识别失败 status={result.status_code} "
                f"code={getattr(result, 'code', '?')} message={getattr(result, 'message', '?')}"
            )
            return ""

        sentences = result.get_sentence() or []
        text = "".join(
            (s.get("text") or "") if isinstance(s, dict) else str(s)
            for s in sentences
        ).strip()
        backend_log(f"[FunASR] 识别完成: {text!r}（{len(sentences)} 句）")
        return text

    # ---------- 异步入口（STTProvider 接口） ----------
    async def transcribe_pcm16(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        """把 PCM16 组包成 WAV 后提交识别，返回转写文本（失败返回空串）"""
        if not pcm16:
            return ""

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm16)

            backend_log(
                f"[FunASR] 提交识别: model={self.model} "
                f"{len(pcm16)} 字节 PCM @ {sample_rate}Hz"
            )
            return await asyncio.to_thread(self._transcribe_sync, wav_path, sample_rate)
        except Exception as exc:
            backend_log(f"[FunASR] 识别异常: {exc}")
            return ""
        finally:
            try:
                os.unlink(wav_path)
            except Exception:
                pass
