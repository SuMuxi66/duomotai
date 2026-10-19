#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★ 演示文件 - 语音问油温 · 端到端测试（真实链路）

链路：麦克风语音 → RK 端侧 ASR 识别 → 判定"油温/锅温"询问 →
      真实读取 MLX90640 红外温度 → 小米 TTS 语音播报数值。

模块对应：backend/voice_oil_temp_test.py（完整实现，本文件为演示独立版）
配套演示代码同目录：oil_watch.py（核心逻辑）、temperature.py 见完整实现。

与 cmd_voice_chat.py 共用同一套语音服务（RK3588 端侧）：
  - ASR：ws://127.0.0.1:8768/asr （FunASR 流式，16k PCM16）
  - TTS：小米 MIMO（XIAOMI_TTS_API_KEY 从 backend/.env.local 自动读取）

温度数据源（--temp-source）：
  - http  （默认）：GET 后端 /temperature 拿实时数据。
          推荐：后端已在运行并占用 COM6 串口时走这个，避免两个进程抢串口。
  - serial：脚本内直连 MLX90640（后端未运行且串口空闲时用）。

运行方式：
    python voice_oil_temp_test.py
    python voice_oil_temp_test.py --asr-url ws://192.168.1.248:8768/asr
    python voice_oil_temp_test.py --temp-source serial
    python voice_oil_temp_test.py --one-shot "油温到了吗"   # 不走麦克风，直接文本测链路

交互：
    - 程序保持待命监听，按 Enter 开始听一句；
    - 说「油温到了吗 / 现在油温多少 / 锅温够不够」→ 真读温度并 TTS 播报；
    - 说「盯油温 / 提醒我下食材」→ 后台轮询监测，到 160°C 主动 TTS 提醒；
    - 说「退出」回到待命，Ctrl+C 结束。

依赖：websockets, numpy, httpx, sounddevice, python-dotenv（与 cmd_voice_chat 相同）
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent             # 本文件所在目录
BACKEND_DIR = SCRIPT_DIR.parents[3] / "backend"          # 定位真实 backend（.env.local 所在）

# 加载真实后端 .env.local 里的 TTS key
def load_keys():
    try:
        from dotenv import load_dotenv
        for f in (BACKEND_DIR / ".env.local", BACKEND_DIR / ".env"):
            if f.exists():
                load_dotenv(f)
                break
    except Exception:
        pass
    return os.getenv("XIAOMI_TTS_API_KEY") or os.getenv("XIAOMI_API_KEY")


# RK 端侧 ASR（与前端 / cmd_voice_chat 同一协议）
DEFAULT_ASR_URL = "ws://127.0.0.1:8768/asr"
ASR_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 24000  # 小米 TTS 输出 PCM 采样率

# 小米 TTS
XIAOMI_TTS_URL = "https://api.xiaomimimo.com/v1/chat/completions"
DEFAULT_TTS_VOICE = "mimo_default"
DEFAULT_TTS_MODEL = "mimo-v2.5-tts"

# 后端温度接口（真实探测数据源）
DEFAULT_BACKEND_URL = "http://127.0.0.1:8010"

HANGUP_PHRASES = ["退出", "结束对话", "停止", "挂断", "再见", "先这样"]


# ---------- 终端 UI ----------
def ui_print(text: str = "", color: str = ""):
    sys.stdout.write(f"{color}{text}{chr(27)}[0m\n")
    sys.stdout.flush()


class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    DIM = "\033[2m"


# ---------- 温度探测（真实） ----------

class TempSource:
    """真实温度数据源：http（后端, 推荐）或 serial（直连 MLX90640）"""

    def __init__(self, mode: str, backend_url: str):
        self.mode = mode
        self.backend_url = backend_url

    def read_maximum(self) -> float | None:
        """返回锅面最高温；无有效数据返回 None"""
        if self.mode == "http":
            return self._read_http()
        return self._read_serial()

    def _read_http(self) -> float | None:
        import httpx

        try:
            r = httpx.get(f"{self.backend_url}/temperature", timeout=3.0)
            if r.status_code != 200:
                return None
            data = r.json()
            maximum = float(data.get("maximum") or 0.0)
            return maximum if maximum > 1.0 else None
        except Exception:
            return None

    def _read_serial(self) -> float | None:
        # 直连：把真实 backend 加入 sys.path，复用 app.temperature 的 MLX90640 读取
        try:
            if str(BACKEND_DIR) not in sys.path:
                sys.path.insert(0, str(BACKEND_DIR))
            from app.temperature import get_temperature

            data = get_temperature()
            maximum = float(data.get("maximum") or 0.0)
            return maximum if maximum > 1.0 else None
        except Exception:
            return None


# ---------- TTS（小米 MIMO，与 cmd_voice_chat 相同实现） ----------

def split_tts(text: str, max_len: int = 80) -> list:
    text = re.sub(r"[\r\n]+", " ", text).strip()
    if not text:
        return []
    out = []
    for sent in re.split(r"(?<=[。！？!?])", text):
        sent = sent.strip()
        if not sent:
            continue
        for cl in re.split(r"(?<=[，,])", sent):
            cl = cl.strip()
            if not cl:
                continue
            if len(cl) <= max_len:
                out.append(cl)
            else:
                for i in range(0, len(cl), max_len):
                    piece = cl[i:i + max_len].strip()
                    if piece:
                        out.append(piece)
    return out


async def tts_synthesize(text: str, api_key: str, model: str, voice: str) -> bytes:
    import httpx

    segments = split_tts(text)
    if not segments:
        return b""
    chunks = []
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        for seg in segments:
            payload = {
                "model": model,
                "messages": [{"role": "assistant", "content": seg}],
                "audio": {"format": "pcm16", "voice": voice or DEFAULT_TTS_VOICE},
                "stream": True,
            }
            async with client.stream("POST", XIAOMI_TTS_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"Xiaomi TTS HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    choices = obj.get("choices")
                    if not choices or choices[0] is None:
                        continue
                    delta = choices[0].get("delta")
                    audio = (delta or {}).get("audio")
                    if not audio:
                        continue
                    audio_data = audio.get("data", "") if isinstance(audio, dict) else ""
                    if audio_data:
                        chunks.append(base64.b64decode(audio_data))
    return b"".join(chunks)


def play_pcm(pcm: bytes, samplerate: int):
    import sounddevice as sd

    if not pcm:
        return
    out = sd.RawOutputStream(samplerate=samplerate, channels=1, dtype="int16", blocksize=0)
    out.start()
    try:
        out.write(pcm)
    finally:
        time.sleep(len(pcm) / 2 / samplerate + 0.1)
        out.stop()
        out.close()


# ---------- 油温快答（与后端 oil_watch.answer_oil_temp_query 同语义，演示版） ----------

async def answer_oil_temp(ts: TempSource, api_key: str, tts_model: str, tts_voice: str) -> None:
    temp = ts.read_maximum()
    if temp is None:
        reply = "温度传感器暂时没有读到有效数据，请确认红外测温探头已对准锅面。"
    else:
        reply = (
            f"当前锅面最高温度约 {temp:.0f}°C。参考：三四成热 90 到 120 度适合滑炒，"
            f"五六成热 150 到 180 度适合下葱花姜片和主料，七八成热 200 度以上适合煎炸。"
        )
    ui_print(f"🤖 回执: {reply}", _C.GREEN)
    if api_key:
        pcm = await tts_synthesize(reply, api_key, tts_model, tts_voice)
        await asyncio.get_running_loop().run_in_executor(None, play_pcm, pcm, TTS_SAMPLE_RATE)
    else:
        ui_print("（未配置小米 TTS key，仅文字回执）", _C.DIM)


async def watch_oil_temp(ts: TempSource, api_key: str, tts_model: str, tts_voice: str, target: int = 160):
    """后台盯油温：每 2 秒采样，连续 3 次达标 → TTS 提醒下食材"""
    ui_print(f"⏳ 开始盯油温，目标 {target}°C（连续 3 次达标判定）…", _C.DIM)
    consecutive = 0
    for _ in range(150):  # 上限 5 分钟
        await asyncio.sleep(2)
        temp = ts.read_maximum()
        if temp is None:
            consecutive = 0
            continue
        if temp >= target:
            consecutive += 1
            if consecutive >= 3:
                msg = f"油温到了，约 {temp:.0f}°C，可以下食材了！"
                ui_print(f"⏰ 主动提醒: {msg}", _C.YELLOW)
                if api_key:
                    pcm = await tts_synthesize(msg, api_key, tts_model, tts_voice)
                    await asyncio.get_running_loop().run_in_executor(None, play_pcm, pcm, TTS_SAMPLE_RATE)
                return
        else:
            consecutive = 0
    ui_print("（等待超时，未达到目标温度）", _C.DIM)


async def handle_utterance(text: str, cfg) -> None:
    """一句话进来：判定意图 → 油温快答 / 盯油温 / 退出"""
    if any(p in text for p in HANGUP_PHRASES):
        ui_print("👋 回到待命", _C.DIM)
        return
    if ("油温" in text or "锅温" in text) and any(kw in text for kw in ("盯", "提醒", "叫我", "监测")):
        await watch_oil_temp(cfg["ts"], cfg["xiaomi_key"], cfg["tts_model"], cfg["tts_voice"])
        return
    if "油温" in text or "锅温" in text:
        await answer_oil_temp(cfg["ts"], cfg["xiaomi_key"], cfg["tts_model"], cfg["tts_voice"])
        return
    ui_print(f"（非油温问题，请说「油温到了吗 / 盯油温」，或「退出」）", _C.DIM)


# ---------- 语音链路（复用 cmd_voice_chat 的 ASR 协议 + 内联重采样） ----------

class SharedState:
    def __init__(self):
        self.mode = "idle"      # idle=待命(不推ASR), listen=对话推ASR
        self.listening = False
        self.lock = threading.Lock()

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def get(self, k):
        with self.lock:
            return getattr(self, k)


_st = SharedState()


class StreamResampler:
    """跨帧重采样（设备原生采样率 ≠ 16k 时自动修正）—— 内联自 cmd_voice_chat.py"""

    def __init__(self, src_sr, dst_sr):
        self.src_sr = src_sr
        self.dst_sr = dst_sr
        self._carry = np.zeros(0, dtype=np.float32)

    def process(self, chunk_int16):
        x = chunk_int16.astype(np.float32) / 32768.0
        x = np.concatenate([self._carry, x])
        if self.src_sr == self.dst_sr:
            self._carry = np.zeros(0)
            y = x
        else:
            n_dst = int(round(len(x) * self.dst_sr / self.src_sr))
            if n_dst == 0:
                self._carry = x
                return np.zeros(0, dtype=np.int16)
            idx = np.linspace(0, len(x) - 1, n_dst)
            y = np.interp(idx, np.arange(len(x)), x)
            used = int(round(n_dst * self.src_sr / self.dst_sr))
            self._carry = x[used:] if 0 < used < len(x) else np.zeros(0)
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)


async def asr_loop(asr_url: str, asr_queue: asyncio.Queue):
    import websockets

    while _st.get("listening"):
        try:
            async with websockets.connect(asr_url, max_size=None, ping_interval=20) as ws:
                ui_print(f"  ✓ ASR 已连接", _C.GREEN)
                send_task = asyncio.create_task(_asr_sender(ws, asr_queue))
                recv_task = asyncio.create_task(_asr_receiver(ws))
                done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                if not _st.get("listening"):
                    break
                await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(2)


async def _asr_sender(ws, queue):
    while True:
        data = await queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


async def _asr_receiver(ws):
    global _pending_texts
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") == "asr" and not msg.get("isPartial"):
            text = (msg.get("text") or "").strip()
            if text:
                _pending_texts.append(text)


_pending_texts: list = []


def run_mic_stdin(loop: asyncio.AbstractEventLoop, asr_queue: asyncio.Queue) -> None:
    """阻塞线程：按 Enter 完成一次聆听 → 丢给 asyncio 处理"""
    import sounddevice as sd

    resampler = None
    actual_sr = None

    def callback(indata, frames, time_info, status):
        nonlocal resampler, actual_sr
        if _st.get("mode") != "listen" or not _st.get("listening"):
            return
        pcm = indata[:, 0] if indata.ndim > 1 else indata
        pcm = pcm.astype(np.int16)
        if actual_sr is None:
            return
        if resampler.src_sr != ASR_SAMPLE_RATE:
            pcm = resampler.process(pcm)
            if pcm.size == 0:
                return
        loop.call_soon_threadsafe(asr_queue.put_nowait, pcm.tobytes())

    with sd.InputStream(samplerate=ASR_SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=1024, callback=callback) as stream:
        actual_sr = stream.samplerate
        resampler = StreamResampler(actual_sr, ASR_SAMPLE_RATE)
        ui_print(f"🎙️  麦克风就绪（{actual_sr}Hz）— 按 Enter 开始听一句", _C.DIM)
        while True:
            line = sys.stdin.readline()
            if line is None:
                break
            cmd = line.strip()
            if cmd.lower() in ("q", "quit"):
                break
            _pending_texts.clear()
            _st.set(mode="listen", listening=True)
            loop.call_soon_threadsafe(asyncio.create_task, _handle_once())
    _st.set(listening=False, mode="idle")


async def _handle_once():
    """听一句（等约 4 秒后收 final 文本）并处理"""
    await asyncio.sleep(4.0)
    _st.set(listening=False, mode="idle")
    if _pending_texts:
        text = _pending_texts[-1]
        ui_print(f"🗣️ 识别: {text}", _C.CYAN)
        await handle_utterance(text, cfg_global)
    else:
        ui_print("（未识别到语音）", _C.DIM)


def parse_args():
    p = argparse.ArgumentParser(description="语音问油温 · 端到端测试（真实链路：ASR→MLX90640→TTS）")
    p.add_argument("--asr-url", default=DEFAULT_ASR_URL, help="RK 端侧 ASR WebSocket 地址")
    p.add_argument("--temp-source", choices=["http", "serial"], default="http",
                   help="温度数据源：http=走后端 8010（推荐,避免抢串口）；serial=脚本直连 MLX90640")
    p.add_argument("--backend-url", default=DEFAULT_BACKEND_URL, help="后端温度接口地址（temp-source=http 时）")
    p.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE, help="小米 TTS 音色")
    p.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help="小米 TTS 模型")
    p.add_argument("--no-tts", action="store_true", help="关闭语音播报，只打印文字")
    p.add_argument("--one-shot", default=None, metavar="TEXT",
                   help="不走麦克风，直接用文本测试链路（如 \"油温到了吗\"）")
    return p.parse_args()


cfg_global = None


def main():
    global cfg_global
    args = parse_args()
    xiaomi_key = None if args.no_tts else load_keys()
    if args.no_tts:
        ui_print("（--no-tts：仅文字，不播报）", _C.DIM)
    elif not xiaomi_key:
        ui_print("[警告] 未找到 XIAOMI_TTS_API_KEY，仅文字回执", _C.YELLOW)

    ts = TempSource(args.temp_source, args.backend_url)
    cfg_global = {
        "ts": ts,
        "xiaomi_key": xiaomi_key,
        "tts_model": args.tts_model,
        "tts_voice": args.tts_voice,
    }

    # 快速自检：真实温度是否可读
    t = ts.read_maximum()
    if t is None:
        ui_print(f"[温度] 当前读不到有效数据（source={args.temp_source}），请确认探头/后端 {args.backend_url}")
    else:
        ui_print(f"[温度] 真实探测 OK，当前锅面最高约 {t:.0f}°C", _C.GREEN)

    if args.one_shot:
        asyncio.run(handle_utterance(args.one_shot, cfg_global))
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    asr_queue: asyncio.Queue = asyncio.Queue()
    loop.create_task(asr_loop(args.asr_url, asr_queue))
    run_mic_stdin(loop, asr_queue)


if __name__ == "__main__":
    main()