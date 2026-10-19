"""Xiaomi MiMo TTS provider —— 官方流式调用方式。

对齐官方 OpenAI 兼容用法（mimo-v2.5-tts）：
    client = AsyncOpenAI(api_key=..., base_url="https://api.xiaomimimo.com/v1")
    completion = await client.chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[user=语气指令, assistant=待朗读文本],
        audio={"format": "pcm16", "voice": ...},
        stream=True,
    )
    # delta.audio["data"] 为 base64(pcm16le mono 24kHz)，逐块产出

与旧实现的差异：
- 鉴权改回官方的 Authorization: Bearer（原来自造的 api-key 头）
- messages 改为官方两段式：user=语气指令 + assistant=待朗读文本
- 一次调用流式输出整段文本，不再客户端预切分（llm_handler 已按句喂入）
- 保留下单前重试（仅当尚未产出任何 PCM 时才安全重试，避免重复播放）
"""
import asyncio
import base64

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, InternalServerError

from ..logger import backend_log
from .base import TTSProvider
from .tts_wav import pcm_to_wav

MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_TTS_MODEL = "mimo-v2.5-tts"
PCM_SAMPLE_RATE = 24000

# user 消息的默认语气指令（官方示例中 user 承担风格控制；缺省保持中性自然朗读）
DEFAULT_STYLE_INSTRUCTION = "请用自然、清晰、亲切的语气朗读下面的内容，语速适中。"

# 网络类错误重试：最多 2 次，间隔 0.5s / 1s
_MAX_RETRIES = 2


class XiaomiTTS(TTSProvider):
    def __init__(self, api_key: str, model: str = MIMO_TTS_MODEL):
        self.api_key = api_key
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=MIMO_BASE_URL, timeout=120.0)

    def _add_wav_header(self, pcm_data: bytes, sample_rate: int = PCM_SAMPLE_RATE, channels: int = 1, bits: int = 16) -> bytes:
        return pcm_to_wav(pcm_data, sample_rate, channels, bits, label="Xiaomi TTS")

    async def stream_pcm_chunks(self, text: str, voice: str = None):
        """官方流式调用：一次请求流式产出整段文本的 PCM 块。

        网络类错误在"尚未产出任何 PCM"时安全重试；已产出后重试会重复播放，
        必须直接 raise 交给上层逐段容错。
        """
        if not text:
            return

        backend_log(f"[Xiaomi TTS] Start streaming synthesis, voice={voice}, len={len(text)}")

        retries = 0
        pcm_chunk_count = 0
        while True:
            try:
                completion = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": DEFAULT_STYLE_INSTRUCTION},
                        {"role": "assistant", "content": text},
                    ],
                    audio={"format": "pcm16", "voice": voice or "mimo_default"},
                    stream=True,
                )
                async for chunk in completion:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    audio = getattr(delta, "audio", None)
                    if audio is None:
                        continue

                    # 官方示例中 delta.audio 为 dict（{"data": base64-pcm16}），
                    # 兼容 SDK 可能包装成对象的情况
                    if isinstance(audio, dict):
                        audio_data = audio.get("data", "")
                    else:
                        audio_data = getattr(audio, "data", "") or ""

                    if not audio_data:
                        continue

                    pcm_chunk_count += 1
                    yield base64.b64decode(audio_data)
                break  # 本段正常完成，退出重试循环
            except Exception as e:
                # 可重试判定：主流网络异常 + SSE 中途断流。
                # 2026-09-16 实测小米服务端会偶发 "peer closed connection without sending
                # complete message body (incomplete chunked read)"（httpx.RemoteProtocolError），
                # 这类断流重试一次通常就能成功；此前未纳入重试 → 每次断流都直接失败、TTS 全哑。
                msg = str(e)
                retryable = (
                    isinstance(e, (APIConnectionError, APITimeoutError, InternalServerError))
                    or "peer closed connection" in msg
                    or "incomplete chunked read" in msg
                    or "RemoteProtocolError" in type(e).__name__
                )
                # 已经产出过 PCM 就不能重试（会重复播放半句），只能如实上报
                if not retryable or pcm_chunk_count > 0 or retries >= _MAX_RETRIES:
                    backend_log(f"[Xiaomi TTS] Stream error（不可重试/已重试完）: {e}")
                    raise
                delay = 0.5 * (2 ** retries)  # 0.5s → 1s
                retries += 1
                backend_log(f"[Xiaomi TTS] 流式断流，重试 {retries}/{_MAX_RETRIES}: {e}")
                await asyncio.sleep(delay)

        backend_log(f"[Xiaomi TTS] 完成，累计收到 {pcm_chunk_count} 个 PCM 块")

    async def synthesize(self, text: str, voice: str = None) -> bytes:
        if not text:
            return b""

        try:
            pcm_chunks = []
            async for pcm_chunk in self.stream_pcm_chunks(text, voice):
                pcm_chunks.append(pcm_chunk)

            if not pcm_chunks:
                backend_log("[Xiaomi TTS] No PCM data received")
                return b""

            pcm_data = b"".join(pcm_chunks)
            wav_data = self._add_wav_header(pcm_data)
            backend_log(f"[Xiaomi TTS] PCM 块数={len(pcm_chunks)}, PCM bytes={len(pcm_data)}, WAV bytes={len(wav_data)}")
            return wav_data
        except Exception as e:
            backend_log(f"[Xiaomi TTS] Request failed: {e}")
            return b""

    async def synthesize_chunks(self, text: str, voice: str = None) -> list:
        audio = await self.synthesize(text, voice)
        return [audio] if audio else []
