from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

import re


def model_error_message(provider: str, status: int | None = None, detail: str = "") -> str:
    """模型调用失败时用户侧的真实错误信息（provider + 状态码 + 详情摘要）。

    完整细节仍进后端日志；此处压缩空白并截断 200 字符，避免长错误页刷屏聊天窗。
    认证（401/403）与余额（402）类错误转为可行动的干净中文提示——原始 JSON
    曾被整段送进 TTS 播报（"Your api key: … type … param …"），对语音用户不可读。
    """
    detail = " ".join((detail or "").split())
    if status in (401, 403):
        m = re.search(r"\*{2,}([A-Za-z0-9]{2,8})", detail)
        suffix = f"（尾号 {m.group(1)}）" if m else ""
        return f"（{provider} 认证失败：API Key {suffix}无效或已失效，请在设置中更新 API Key）"
    if status == 402:
        return f"（{provider} 余额不足，请充值后重试）"
    if len(detail) > 200:
        detail = detail[:200] + "…"
    head = f"（模型调用失败 [{provider}" + (f" HTTP {status}" if status else "") + "]"
    return f"{head}: {detail}）" if detail else f"{head}）"


def downscale_jpeg_bytes(
    jpeg_bytes: bytes, max_side: int = 2048, quality: int = 85, resample: int = None
) -> bytes:
    """等比缩小超过 max_side 的摄像头帧，重新编码为 JPEG。

    ModelScope 等推理平台对输入图有 2048x2048 硬限制，USB 摄像头原生
    2560x1440 直接上送会 400（input size exceed limit）。解码失败或尺寸
    合规则原样返回，不做兜底转换。

    resample：重采样滤波，默认 LANCZOS（画质最好但最慢：2K→720p 约 200ms+）。
    对速度敏感、不追求画质的调用方（如前端抓帧出图）可传 `Image.BILINEAR`，
    同一尺寸约快 2~3 倍（2026-09-16 实测）。
    """
    if not jpeg_bytes:
        return jpeg_bytes
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            width, height = img.size
            if max(width, height) <= max_side:
                return jpeg_bytes
            scale = max_side / max(width, height)
            resized = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                resample if resample is not None else Image.LANCZOS,
            )
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()
    except Exception:
        return jpeg_bytes


class LLMProvider(ABC):
    @abstractmethod
    async def stream_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[list] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        n: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        response_format: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        ...

class VLMProvider(ABC):
    @abstractmethod
    async def analyze_image(self, jpeg_bytes: bytes, prompt: str, enable_thinking: bool = False) -> str:
        ...

    @abstractmethod
    async def analyze_image_stream(self, jpeg_bytes: bytes, prompt: str, enable_thinking: bool = False) -> AsyncIterator[str]:
        """流式分析图片，逐步返回结果"""


# 同时支持文本和视觉的 LLM 接口扩展
class MultimodalLLMProvider(LLMProvider, VLMProvider):
    """支持多模态（文本+图片）的 LLM Provider 基类"""

    @abstractmethod
    async def stream_chat_with_image(
        self,
        prompt: str,
        jpeg_bytes: bytes | None = None,
        *,
        file_images: list[tuple[bytes, str]] | None = None,
        system_prompt: Optional[str] = None,
        history: Optional[list] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """带图片的多轮对话流式生成（支持单张或多张图片）

        Args:
            prompt: 当前用户消息
            jpeg_bytes: 可选，单张视觉帧图片数据（摄像头快照）
            file_images: 可选，用户上传的多张图片数据，每项为 (bytes, mime_type) 元组
            system_prompt: 系统提示词
            history: 对话历史，格式为 [{"role": "user"/"assistant", "content": "..."}, ...]
        """
        ...

class STTProvider(ABC):
    @abstractmethod
    async def transcribe_pcm16(self, pcm16: bytes, sample_rate: int) -> str:
        ...

class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: Optional[str] = None) -> Optional[bytes]:
        ...
