from .tts_piper import DummyTTS


class LocalCosyVoiceTTS(DummyTTS):
    def __init__(self, server_url: str, voice: str):
        self.server_url = server_url
        self.voice = voice
