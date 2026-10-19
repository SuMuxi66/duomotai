from .base import TTSProvider


class DummyTTS(TTSProvider):
    async def synthesize(self, text: str, voice: str = None) -> bytes:
        del text, voice
        return b""
