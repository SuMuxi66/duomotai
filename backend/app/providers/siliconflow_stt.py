import asyncio
import tempfile
import wave
import httpx
from ..logger import backend_log

SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"

class SiliconFlowSTT:
    def __init__(self, api_key: str, model: str = "FunAudioLLM/SenseVoiceSmall"):
        self.api_key = api_key
        self.model = model
    
    async def transcribe_pcm16(self, pcm_data: bytes, sample_rate: int = 16000) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_data)
            tmp_path = tmp.name
        
        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                with open(tmp_path, "rb") as f:
                    files = {"file": ("audio.wav", f, "audio/wav")}
                    headers = {
                        "Authorization": f"Bearer {self.api_key}"
                    }
                    data = {
                        "model": self.model
                    }
                    
                    backend_log(f"[SiliconFlow STT] 发送请求到 {SILICONFLOW_API_URL}")
                    backend_log(f"[SiliconFlow STT] 模型: {self.model}")
                    
                    resp = await client.post(
                        SILICONFLOW_API_URL,
                        files=files,
                        data=data,
                        headers=headers
                    )
                
                if resp.status_code != 200:
                    error_text = resp.text
                    backend_log(f"[SiliconFlow STT] 错误响应: {resp.status_code} - {error_text}")
                    raise Exception(f"SiliconFlow API 错误: {resp.status_code} - {error_text}")
                
                result = resp.json()
                text = result.get("text", "")
                backend_log(f"[SiliconFlow STT] 识别结果: {text}")
                return text
                
        except Exception as e:
            backend_log(f"[SiliconFlow STT] 请求失败: {e}")
            raise e
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except:
                pass
