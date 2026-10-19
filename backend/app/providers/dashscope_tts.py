import asyncio
import base64
import queue
import threading
import time
import dashscope
from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime, QwenTtsRealtimeCallback, AudioFormat
from ..logger import backend_log
from .base import TTSProvider
from .tts_wav import pcm_to_wav

DASHSCOPE_MODEL = "qwen3-tts-flash-realtime"  # 2026-10-14: -instruct 变体已下线（Model not exist）

STABLE_VOICES = [
    "Cherry",
    "Xiao Ming",
    "Xiao Yan",
    "Ernest",
    "Alice",
    "Fred",
    "Wenling",
    "Bob",
    "Cathy",
    "David",
]

TTS_INSTRUCTION = "发音清晰,语速适中,不要重复,不要乱读,断句准确,保持自然停顿"


def clean_text_for_tts(text: str) -> str:
    """规则已收敛到 app/tts_clean.py（全项目唯一权威版本）。

    DashScope 合成不接受换行，在权威清洗后追加换行折叠（历史行为保留）。
    """
    from ..tts_clean import clean_text_for_tts as _clean

    import re

    cleaned = _clean(text)
    cleaned = re.sub(r"[\r\n]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def split_long_text(text: str, max_len: int = 20) -> list:
    import re
    sentences = re.split(r'[。！？.!?]', text)
    buffer = []
    result = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        if len(' '.join(buffer) + ' ' + sentence) <= max_len:
            buffer.append(sentence)
        else:
            if buffer:
                result.append(' '.join(buffer))
                buffer = [sentence]
            else:
                result.append(sentence)
    
    if buffer:
        result.append(' '.join(buffer))
    
    return [r for r in result if r.strip() and len(r.strip()) > 1]


class DashScopeTTSCallback(QwenTtsRealtimeCallback):
    def __init__(self):
        self.complete_event = threading.Event()
        self.audio_chunks = []
        self.audio_queue = queue.Queue(maxsize=100)
        self.error_message = None

    def _mark_finished(self) -> None:
        if not self.complete_event.is_set():
            self.complete_event.set()
            self.audio_queue.put(None)
    
    def on_open(self) -> None:
        backend_log("[DashScope TTS] WebSocket 连接已打开")
    
    def on_close(self, close_status_code, close_msg) -> None:
        backend_log(f"[DashScope TTS] WebSocket 连接已关闭: {close_status_code}, {close_msg}")
        self._mark_finished()
    
    def on_event(self, response: dict) -> None:
        try:
            event_type = response.get('type')
            if 'session.created' == event_type:
                backend_log(f"[DashScope TTS] 会话已创建: {response.get('session', {}).get('id')}")
            elif 'response.audio.delta' == event_type:
                audio_b64 = response.get('delta', '')
                if audio_b64:
                    pcm_chunk = base64.b64decode(audio_b64)
                    self.audio_chunks.append(pcm_chunk)
                    self.audio_queue.put(pcm_chunk)
                    backend_log(f"[DashScope TTS] 收到音频数据: {len(audio_b64)} bytes")
            elif 'response.done' == event_type:
                backend_log(f"[DashScope TTS] 响应完成")
            elif 'session.finished' == event_type:
                backend_log("[DashScope TTS] 会话已结束")
                self._mark_finished()
            elif 'error' == event_type:
                self.error_message = response.get('message') or "DashScope TTS 未知错误"
                backend_log(f"[DashScope TTS] 错误: {self.error_message}")
                self._mark_finished()
        except Exception as e:
            backend_log(f"[DashScope TTS] 事件处理错误: {e}")
            self.error_message = str(e)
            self._mark_finished()
    
    def wait_for_finished(self):
        self.complete_event.wait()


class DashScopeTTS(TTSProvider):
    def __init__(self, api_key: str, model: str = DASHSCOPE_MODEL):
        self.api_key = api_key
        self.model = model
        dashscope.api_key = api_key
    
    async def synthesize(self, text: str, voice: str = None) -> bytes:
        audio_chunks = await self.synthesize_chunks(text, voice)
        if audio_chunks:
            return b"" .join(audio_chunks)
        return b""

    def _prepare_request(self, text: str, voice: str = None) -> tuple[str, list[str], str]:
        cleaned_text = clean_text_for_tts(text)
        if not cleaned_text:
            return "", [], "Cherry"

        sentences = split_long_text(cleaned_text, max_len=20)
        if not sentences:
            sentences = [cleaned_text[:20]] if cleaned_text else []

        target_voice = voice if voice in STABLE_VOICES else "Cherry"
        backend_log(f"[DashScope TTS] 清洗后: {cleaned_text}")
        backend_log(f"[DashScope TTS] 句子数: {len(sentences)}, 音色: {target_voice}")
        return cleaned_text, sentences, target_voice

    async def _start_realtime_session(self, callback: DashScopeTTSCallback, sentences: list[str], target_voice: str):
        tts_client = QwenTtsRealtime(
            model=self.model,
            callback=callback,
        )

        backend_log("[DashScope TTS] 连接 WebSocket...")
        await asyncio.get_event_loop().run_in_executor(None, tts_client.connect)

        backend_log("[DashScope TTS] 更新会话参数...")
        tts_client.update_session(
            voice=target_voice,
            response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            mode='server_commit'
        )

        for i, sentence in enumerate(sentences):
            backend_log(f"[DashScope TTS] [{i+1}/{len(sentences)}] {sentence}")
            tts_client.append_text(sentence)
            await asyncio.sleep(0.1)

        backend_log("[DashScope TTS] 发送完成信号...")
        tts_client.finish()
        return tts_client

    async def stream_pcm_chunks(self, text: str, voice: str = None):
        if not text:
            return

        cleaned_text, sentences, target_voice = self._prepare_request(text, voice)
        if not cleaned_text or not sentences:
            return

        callback = DashScopeTTSCallback()
        tts_client = None

        try:
            tts_client = await self._start_realtime_session(callback, sentences, target_voice)

            while True:
                pcm_chunk = await asyncio.to_thread(callback.audio_queue.get)
                if pcm_chunk is None:
                    break
                yield pcm_chunk

            if callback.error_message:
                raise RuntimeError(callback.error_message)
        except Exception as e:
            backend_log(f"[DashScope TTS] 流式请求异常: {e}")
            raise
        finally:
            try:
                if tts_client is not None:
                    tts_client.close()
            except Exception as e:
                backend_log(f"[DashScope TTS] 关闭连接异常: {e}")
    
    async def synthesize_chunks(self, text: str, voice: str = None) -> list:
        if not text:
            return []

        try:
            pcm_chunks = []
            async for pcm_chunk in self.stream_pcm_chunks(text, voice):
                pcm_chunks.append(pcm_chunk)

            backend_log(f"[DashScope TTS] 收到音频数据: {len(pcm_chunks)} 块")
            if not pcm_chunks:
                backend_log("[DashScope TTS] 未收到音频数据")
                return []

            audio_data = b"" .join(pcm_chunks)
            backend_log(f"[DashScope TTS] 音频数据大小: {len(audio_data)} bytes")
            wav_data = self._add_wav_header(audio_data)
            backend_log(f"[DashScope TTS] 转换后 WAV 数据: {len(wav_data)} bytes")
            return [wav_data]
        except Exception as e:
            backend_log(f"[DashScope TTS] 请求异常: {e}")
            return []
    
    def _add_wav_header(self, pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
        # 公共实现见 tts_wav.pcm_to_wav（原与 xiaomi_tts 逐字重复，已收敛）
        return pcm_to_wav(pcm_data, sample_rate, channels, bits, label="DashScope TTS")
