"""
Qwen3 ASR Flash Realtime Provider
阿里云实时语音识别模型，使用 WebSocket 协议
"""
import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator, Optional
import websockets

from ..logger import backend_log
from .base import STTProvider

logger = logging.getLogger(__name__)


class Qwen3ASRRealtime(STTProvider):
    """阿里云 Qwen3 实时语音识别"""
    
    def __init__(self, api_key: str, model: str = "qwen-audio-3.0-asr-flash"):
        self.api_key = api_key
        self.model = model
        self.base_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        self.url = f"{self.base_url}?model={model}"
        
    async def transcribe_pcm16(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        """
        识别 PCM16 音频（单次识别）
        
        Args:
            pcm16: PCM16 格式的音频数据
            sample_rate: 采样率（默认 16000）
            
        Returns:
            识别的文本
        """
        if sample_rate != 16000:
            backend_log(f"[Qwen3 ASR] 警告: 采样率 {sample_rate} 不是 16000，可能影响识别效果")
        
        return await self._transcribe_stream(pcm16)
    
    async def _transcribe_stream(self, pcm_data: bytes) -> str:
        """使用 WebSocket 实时识别音频"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1"
        }
        
        result_text = ""
        
        try:
            async with websockets.connect(self.url, additional_headers=headers) as ws:
                # 发送会话配置
                session_config = {
                    "event_id": "session_init",
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": "pcm",
                        "sample_rate": 16000,
                        "input_audio_transcription": {
                            "language": "zh",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.2,
                            "silence_duration_ms": 800
                        }
                    }
                }
                await ws.send(json.dumps(session_config))
                backend_log(f"[Qwen3 ASR] 会话配置已发送")
                
                # 分块发送音频数据（每次 3200 字节 = 100ms @ 16kHz）
                chunk_size = 3200
                total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size
                
                for i in range(total_chunks):
                    start = i * chunk_size
                    end = min(start + chunk_size, len(pcm_data))
                    chunk = pcm_data[start:end]
                    
                    if len(chunk) == 0:
                        break
                    
                    # Base64 编码
                    encoded_data = base64.b64encode(chunk).decode('utf-8')
                    
                    # 发送音频块
                    audio_event = {
                        "event_id": f"audio_{i}",
                        "type": "input_audio_buffer.append",
                        "audio": encoded_data
                    }
                    await ws.send(json.dumps(audio_event))
                    
                    # 模拟实时发送延迟（可选）
                    await asyncio.sleep(0.02)
                
                # 发送提交事件（触发识别）
                commit_event = {
                    "event_id": "commit_audio",
                    "type": "input_audio_buffer.commit"
                }
                await ws.send(json.dumps(commit_event))
                backend_log(f"[Qwen3 ASR] 音频已提交，等待识别结果")
                
                # 接收识别结果
                timeout = 30  # 30 秒超时
                start_time = asyncio.get_event_loop().time()
                
                while True:
                    if asyncio.get_event_loop().time() - start_time > timeout:
                        backend_log(f"[Qwen3 ASR] 识别超时")
                        break
                    
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(message)
                        
                        backend_log(f"[Qwen3 ASR] 收到事件: {data.get('type')}")
                        
                        # 处理不同类型的事件
                        if data.get("type") == "conversation.item.input_audio_transcription.completed":
                            transcript = data.get("transcript", "")
                            if transcript:
                                result_text = transcript
                                backend_log(f"[Qwen3 ASR] 识别完成: {transcript}")
                                break
                        elif data.get("type") == "conversation.item.input_audio_transcription.failed":
                            error = data.get("error", "识别失败")
                            backend_log(f"[Qwen3 ASR] 识别失败: {error}")
                            break
                        elif data.get("type") == "error":
                            error = data.get("error", {}).get("message", "未知错误")
                            backend_log(f"[Qwen3 ASR] 错误: {error}")
                            break
                            
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        backend_log(f"[Qwen3 ASR] 接收消息错误: {e}")
                        break
                
                return result_text
                
        except Exception as e:
            backend_log(f"[Qwen3 ASR] WebSocket 连接失败: {e}")
            raise e


async def test_qwen3_asr():
    """测试 Qwen3 ASR"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("请设置 DASHSCOPE_API_KEY 环境变量")
        return
    
    # 创建测试音频（PCM16 格式）
    import wave
    import numpy as np
    
    # 生成 1 秒的测试音频（正弦波）
    sample_rate = 16000
    duration = 1.0
    frequency = 440  # A4 音符
    
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    audio = np.sin(2 * np.pi * frequency * t) * 0.5
    
    # 转换为 PCM16
    audio_pcm16 = (audio * 32767).astype(np.int16)
    pcm_data = audio_pcm16.tobytes()
    
    # 测试识别
    stt = Qwen3ASRRealtime(api_key)
    result = await stt.transcribe_pcm16(pcm_data, sample_rate)
    print(f"识别结果: {result}")


if __name__ == "__main__":
    asyncio.run(test_qwen3_asr())
