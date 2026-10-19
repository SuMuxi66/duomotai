from .base import LLMProvider


class MockLLM(LLMProvider):
    async def stream_chat(self, prompt: str, system_prompt=None, history=None):
        del prompt, system_prompt, history
        yield "当前暂无可用的大模型回复。", ""
