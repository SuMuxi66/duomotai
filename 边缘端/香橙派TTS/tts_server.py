"""tts_server.py — 香橙派 RK3588 本地 TTS HTTP 服务（阿里 Sambert-HiFTGAN 16k 中文）。

模型: damo/speech_sambert-hifigan_tts_zh-cn_16k（ModelScope，阿里达摩院开源）
前端: ttsfrd_compat（开源替代，jieba+pypinyin+KAN-TTS 音素表）
端口: 8769   接口: GET /health  GET|POST /tts  GET /voices
"""
import io
import os
import sys
import threading
import time
import wave
from typing import Optional

# 关键：先注入开源前端桩，再让 modelscope import ttsfrd 时拿到它
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ttsfrd_compat  # noqa: E402

sys.modules["ttsfrd"] = ttsfrd_compat

import numpy as np  # noqa: E402
import torch  # noqa: E402
from fastapi import FastAPI, HTTPException, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

MODEL_DIR = "/home/orangepi/.cache/modelscope/hub/models/damo/speech_sambert-hifigan_tts_zh-cn_16k"
torch.set_num_threads(int(os.environ.get("TTS_THREADS", "4")))

app = FastAPI(title="edge-tts-sambert")
_state = {}


def _get_pipeline():
    if "pipe" not in _state:
        from modelscope.pipelines import pipeline
        from modelscope.utils.constant import Tasks

        t0 = time.time()
        _state["pipe"] = pipeline(
            task=Tasks.text_to_speech, model=MODEL_DIR)
        m = _state["pipe"].model
        _state["voices"] = list(m.voices)
        _state["default_voice"] = m.default_voice_name
        print("[tts_server] model loaded in %.1fs, voices=%s" %
              (time.time() - t0, _state["voices"]), flush=True)
    return _state["pipe"]


def _warmup():
    """后台线程预热：进程启动即加载模型，避免首个请求等 1~3 分钟。"""
    try:
        pipe = _get_pipeline()
        # 跑一句极短合成，完成算子预热，稳态 RTF 更好
        t0 = time.time()
        pipe("好。")
        print("[tts_server] warmup done in %.1fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("[tts_server] warmup failed: %r" % (e,), flush=True)


@app.on_event("startup")
def _on_startup():
    threading.Thread(target=_warmup, daemon=True).start()


class TtsReq(BaseModel):
    text: str
    voice: Optional[str] = None
    emotion: Optional[str] = None  # emotion_neutral/happy/angry/... ，需在音色 emo 字典内
    speaker: Optional[str] = None  # 默认 F7，与官方管线行为一致


def _synth(req: TtsReq) -> Response:
    if not req.text or not req.text.strip():
        raise HTTPException(400, "text is empty")
    pipe = _get_pipeline()
    model = pipe.model
    voice = req.voice or _state["default_voice"]
    if voice not in model.voices:
        raise HTTPException(400, "unknown voice %s, have %s" % (voice, _state["voices"]))
    emo = req.emotion or "emotion_neutral"
    spk = req.speaker or "F7"

    # 把 emotion/speaker 传给前端桩（ttsfrd 原接口没有这俩参数）
    ttsfrd_compat.emo = emo
    ttsfrd_compat.spk = spk

    t0 = time.time()
    try:
        out = pipe(req.text.strip(), voice=voice)
    except Exception as e:
        raise HTTPException(500, "synthesis failed: %s" % e)
    dt = time.time() - t0
    wav = out["output_wav"]
    dur = _wav_seconds(wav)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"X-Synth-Seconds": "%.2f" % dt,
                 "X-Audio-Seconds": "%.2f" % dur,
                 "X-RTF": "%.2f" % (dt / max(dur, 1e-3)),
                 "X-Voice": voice})


def _wav_seconds(wav_bytes):
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


@app.get("/health")
def health():
    try:
        _get_pipeline()
        return {"status": "ok", "voices": _state["voices"],
                "default_voice": _state["default_voice"]}
    except Exception as e:
        return {"status": "loading", "error": str(e)[:200]}


@app.get("/voices")
def voices():
    _get_pipeline()
    return {"voices": _state["voices"], "default": _state["default_voice"]}


@app.get("/tts")
def tts_get(text: str, voice: Optional[str] = None, emotion: Optional[str] = None, speaker: Optional[str] = None):
    return _synth(TtsReq(text=text, voice=voice, emotion=emotion, speaker=speaker))


@app.post("/tts")
def tts_post(req: TtsReq):
    return _synth(req)
