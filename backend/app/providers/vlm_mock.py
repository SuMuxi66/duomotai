from typing import AsyncIterator
from .base import LLMProvider, VLMProvider


class MockVLM(LLMProvider, VLMProvider):
    """模型未配置时的兜底实例：任何 provider 缺 api_key/model 时回退到此。

    补齐 stream_chat / stream_chat_with_image，避免回退后文本对话崩溃
    （'MockVLM' object has no attribute 'stream_chat'）。
    """

    async def stream_chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list | None = None,
        **kwargs,
    ) -> AsyncIterator[tuple[str, str]]:
        del prompt, system_prompt, history, kwargs
        yield "（模型未配置，请先在设置中填写所选供应商的 API Key）", ""

    async def stream_chat_with_image(
        self,
        prompt: str,
        jpeg_bytes: bytes | None = None,
        *,
        file_images: list | None = None,
        system_prompt: str | None = None,
        history: list | None = None,
        **kwargs,
    ) -> AsyncIterator[tuple[str, str]]:
        del prompt, jpeg_bytes, file_images, system_prompt, history, kwargs
        yield "（模型未配置，请先在设置中填写所选供应商的 API Key）", ""

    async def analyze_image(self, image_b64: str, prompt: str, enable_thinking: bool = False) -> str:
        del image_b64, prompt, enable_thinking
        return "当前暂无视觉观察记录。"

    async def analyze_image_stream(
        self, image_b64: str, prompt: str, enable_thinking: bool = False
    ) -> AsyncIterator[str]:
        del image_b64, prompt, enable_thinking
        yield "当前暂无视觉观察记录。"
