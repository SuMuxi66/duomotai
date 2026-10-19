import httpx
from ..logger import backend_log
from .base import TTSProvider

SILICONFLOW_TTS_URL = "https://api.siliconflow.cn/v1/audio/speech"

_sf_limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
_sf_http_client: httpx.AsyncClient | None = None

def get_siliconflow_client() -> httpx.AsyncClient:
    global _sf_http_client
    if _sf_http_client is None:
        _sf_http_client = httpx.AsyncClient(timeout=60.0, limits=_sf_limits, trust_env=False)
    return _sf_http_client

class SiliconFlowTTS(TTSProvider):
    def __init__(self, api_key: str, model: str = "FunAudioLLM/CosyVoice2-0.5B"):
        self.api_key = api_key
        self.model = model

    async def synthesize(self, text: str, voice: str = None) -> bytes:
        if not text:
            return b""

        backend_log(f"[SiliconFlow TTS] 发送请求: {text[:50]}..., 音色: {voice}")

        try:
            client = get_siliconflow_client()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": self.model,
                "input": text,
                "response_format": "wav"
            }

            if voice and voice != "default":
                if ":" not in voice and voice != "default":
                    voice = f"{self.model}:{voice}"
                payload["voice"] = voice

            resp = await client.post(
                SILICONFLOW_TTS_URL,
                headers=headers,
                json=payload
            )

            if resp.status_code != 200:
                error_text = resp.text
                backend_log(f"[SiliconFlow TTS] 错误响应: {resp.status_code} - {error_text}")
                raise Exception(f"SiliconFlow TTS API 错误: {resp.status_code} - {error_text}")

            audio_data = resp.content
            backend_log(f"[SiliconFlow TTS] 收到音频数据: {len(audio_data)} bytes")
            return audio_data

        except Exception as e:
            backend_log(f"[SiliconFlow TTS] 请求失败: {e}")
            raise e
