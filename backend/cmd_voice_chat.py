#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_voice_chat.py — 极简语音助手（cmd 运行）
=============================================

只做两件事：**唤醒词检测** + **语音对话（含 TTS 语音播报）**。
唤醒词与语音识别都对接你 RK3588 上的端侧服务（跟前端同一套）：

  - 唤醒词(KWS)：RK3588 端侧 KWS WebSocket（默认 ws://127.0.0.1:8767/kws，
                 即前端 createSttWakeWordListener 用的那个服务，离线、低延迟）
  - 语音识别(STT)：RK3588 端侧 ASR WebSocket（默认 ws://127.0.0.1:8768/asr，
                 即前端用的那个 FunASR 流式服务，本地、低延迟）
  - 大模型(LLM)：DashScope（通义千问），key 从后端 .env 自动读取
  - 语音合成(TTS)：小米 MIMO TTS，key 从后端 .env 自动读取，sounddevice 播放

运行方式（在 PC 或 RK3588 上，只要能访问到 RK 的 8767/8768 服务即可）：
    python cmd_voice_chat.py                         # 默认连本机 127.0.0.1:8767/8768
    python cmd_voice_chat.py --kws-url ws://192.168.1.248:8767/kws --asr-url ws://192.168.1.248:8768/asr
    python cmd_voice_chat.py --no-tts                # 只要文字，不要语音
    python cmd_voice_chat.py --list-devices          # 列出音频设备编号
    python cmd_voice_chat.py --input-device 2 --output-device 1

依赖：websockets, numpy, httpx, sounddevice, python-dotenv
     pip install websockets numpy httpx sounddevice python-dotenv
     sounddevice 需要系统 PortAudio: (Debian/Ubuntu: apt install portaudio19-dev)

退出：Ctrl+C
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import threading
import time

import numpy as np

# ---------- 路径 & 配置 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = SCRIPT_DIR  # 本脚本放在 backend/ 下

# 默认唤醒词（仅用于日志展示；真正的命中由 RK 端侧 KWS 服务判定）
DEFAULT_WAKE_WORDS = ["小云小云", "小云", "晓云", "小芸小芸"]

# RK3588 端侧 KWS / ASR WebSocket 地址（和前端 createSttWakeWordListener 一致）
DEFAULT_KWS_URL = "ws://127.0.0.1:8767/kws"
DEFAULT_ASR_URL = "ws://127.0.0.1:8768/asr"

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
XIAOMI_TTS_URL = "https://api.xiaomimimo.com/v1/chat/completions"
TTS_SAMPLE_RATE = 24000  # 小米 TTS 输出 PCM 采样率
ASR_SAMPLE_RATE = 16000  # 端侧 ASR / KWS 输入采样率

SYS_PROMPT = (
    "你是一个叫“小云”的中文语音助手，语气自然、简洁、口语化，"
    "像真人聊天一样回答，不要使用 Markdown 符号，不要列出长篇要点，"
    "一句话能说清就别啰嗦。"
)

# 退出/挂断指令词：唤醒后说这些词，助手回到待命（重新等待唤醒词）
HANGUP_PHRASES = ["退出", "结束对话", "结束通话", "停止对话", "挂断", "再见", "先这样"]


# ---------- 终端 UI（ANSI 彩色 + 电平条不乱跳）----------
if sys.platform == "win32":
    os.system("")  # 启用 Windows ANSI VT 模式（cmd 支持 \033 转义）


class _C:
    """ANSI 颜色码"""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"
    GRAY   = "\033[90m"


_monitor_on = False  # 电平条是否正在显示（打印消息前需先清除，避免乱跳）


def ui_clear_monitor():
    """清除电平条行"""
    global _monitor_on
    if _monitor_on:
        sys.stdout.write("\033[2K\r")
        sys.stdout.flush()
        _monitor_on = False


def ui_print(text: str = "", color: str = "", end: str = "\n"):
    """打印消息（自动清除电平条行，避免文字乱跳）"""
    ui_clear_monitor()
    sys.stdout.write(f"{color}{text}{_C.RESET}{end}")
    sys.stdout.flush()


def ui_stream(text: str, color: str = ""):
    """流式输出（不换行，用于 AI 回复逐字打印）"""
    sys.stdout.write(f"{color}{text}{_C.RESET}")
    sys.stdout.flush()


def ui_monitor(rms: float, mode: str):
    """显示/更新电平条（仅 idle 模式；其他模式不显示避免和对话输出冲突）"""
    global _monitor_on
    if mode != "idle":
        return
    bar_len = int(min(30, rms / 150))
    bar = "█" * bar_len + "░" * (30 - bar_len)
    if rms < 50:
        color = _C.GRAY
    elif rms < 3000:
        color = _C.GREEN
    elif rms < 8000:
        color = _C.YELLOW
    else:
        color = _C.RED
    sys.stdout.write(f"\r{color}🎙️  {bar} rms={int(rms):5d}{_C.RESET}  待命中…")
    sys.stdout.flush()
    _monitor_on = True


# ---------- 音频设备选择 ----------
def _resolve_device(name: str):
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        devs = sd.query_devices()
        for i, d in enumerate(devs):
            if name.lower() in (d["name"] or "").lower():
                return i
    except Exception:
        pass
    return None


# ---------- 全局状态（音频回调线程 与 asyncio 主线程共享）----------
class SharedState:
    def __init__(self):
        self.mode = "idle"          # idle=等待唤醒词 ; listen=对话中
        self.muted = False          # 播放 TTS / LLM 思考时静音，防止回声
        self.processing = False     # 正在处理一轮对话，避免重叠
        self.listening = False      # 是否已开启 ASR 会话
        self.lock = threading.Lock()

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def get(self, k):
        with self.lock:
            return getattr(self, k)


state = SharedState()
asr_task = None  # 当前 ASR 监听任务（用于重新唤醒时取消旧任务，避免重复连接）


# ---------- 唤醒词（RK3588 端侧 KWS WebSocket @ 8767/kws）----------
# 不再本地跑 sherpa-onnx，直接连设备端 KWS 服务：持续推 16k PCM16 裸帧，
# 命中后服务端推 JSON {"event":"wakeword","keyword":"..."}，与前端协议一致。


# ---------- LLM（DashScope 流式）----------
async def llm_stream(text: str, history: list, api_key: str, model: str, enable_thinking: bool):
    import httpx

    messages = [{"role": "system", "content": SYS_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": text})

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "enable_thinking": enable_thinking,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    full = []
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        async with client.stream("POST", DASHSCOPE_URL, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                err = await resp.aread()
                raise RuntimeError(f"DashScope HTTP {resp.status_code}: {err.decode('utf-8', 'ignore')[:200]}")
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
                if not obj.get("choices"):
                    continue
                delta = obj["choices"][0].get("delta", {}).get("content")
                if delta:
                    full.append(delta)
                    yield delta


def split_tts(text: str, max_len: int = 80) -> list:
    """按句末标点、再按逗号切分，贴合语音自然停顿。"""
    import re
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


# ---------- TTS（小米 MIMO，流式 PCM）----------
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
                "audio": {"format": "pcm16", "voice": voice or "mimo_default"},
                "stream": True,
            }
            async with client.stream("POST", XIAOMI_TTS_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    raise RuntimeError(f"Xiaomi TTS HTTP {resp.status_code}: {err.decode('utf-8', 'ignore')[:200]}")
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
                    if not delta:
                        continue
                    audio = delta.get("audio")
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


# ---------- 设备 ASR WebSocket（FunASR，端口 8768）----------
async def asr_loop(asr_url: str, asr_queue: asyncio.Queue, on_final):
    import websockets

    while state.get("listening"):
        try:
            async with websockets.connect(asr_url, max_size=None, ping_interval=20) as ws:
                ui_print(f"  ✓ ASR 已连接", _C.GREEN)
                # 发送任务：从队列取 PCM 帧发给 ASR
                send_task = asyncio.create_task(_asr_sender(ws, asr_queue))
                # 接收任务：解析 asr 结果
                recv_task = asyncio.create_task(_asr_receiver(ws, on_final))
                done, pending = await asyncio.wait(
                    [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                if not state.get("listening"):
                    break
                ui_print("  ASR 断开，重连中…", _C.YELLOW)
                await asyncio.sleep(1)
        except Exception as e:
            if not state.get("listening"):
                break
            ui_print(f"  ASR 连接失败: {e}", _C.RED)
            await asyncio.sleep(2)


async def _asr_sender(ws, asr_queue: asyncio.Queue):
    while True:
        data = await asr_queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


async def _asr_receiver(ws, on_final):
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") == "asr" and not msg.get("isPartial"):
            text = (msg.get("text") or "").strip()
            if text:
                asyncio.create_task(on_final(text))


# ---------- 唤醒词 KWS WebSocket（RK3588 @ 8767/kws）----------
async def kws_loop(kws_url: str, kws_queue: asyncio.Queue, on_wake):
    import websockets

    while True:
        try:
            async with websockets.connect(kws_url, max_size=None, ping_interval=20) as ws:
                ui_print(f"  ✓ KWS 已连接，等待唤醒词…", _C.GREEN)
                send_task = asyncio.create_task(_kws_sender(ws, kws_queue))
                recv_task = asyncio.create_task(_kws_receiver(ws, on_wake))
                done, pending = await asyncio.wait(
                    [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                ui_print("  KWS 断开，1s 后重连…", _C.YELLOW)
                await asyncio.sleep(1)
        except Exception as e:
            ui_print(f"  KWS 连接失败: {e}", _C.RED)
            await asyncio.sleep(2)


async def _kws_sender(ws, kws_queue: asyncio.Queue):
    while True:
        data = await kws_queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


async def _kws_receiver(ws, on_wake):
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("event") == "wakeword":
            keyword = msg.get("keyword", "")
            ui_print(f"🔔 命中唤醒词: {keyword}" if keyword else "🔔 命中唤醒词", _C.BOLD + _C.YELLOW)
            asyncio.ensure_future(on_wake())


# ---------- 一轮对话 ----------
async def handle_utterance(text: str, cfg, history: list, asr_queue: asyncio.Queue):
    if state.get("processing"):
        return
    state.set(processing=True, muted=True)  # 静音：避免 AI 自己的声音被再次识别
    ui_print(f"🗣️  你: {text}", _C.CYAN)

    # 退出指令：回到待命，重新等待唤醒词
    if any(p in text for p in HANGUP_PHRASES):
        ui_print("👋 回到待命（说唤醒词重新开始）", _C.DIM)
        state.set(listening=False, mode="idle", processing=False, muted=False)
        return
    try:
        reply = []
        ui_print("🤖 AI: ", _C.GREEN, end="")

        # ---- 流式分句 TTS：LLM 边输出文字，遇到句子边界立即送 TTS，文字和语音同步 ----
        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_done = asyncio.Event()

        async def _tts_worker():
            """按顺序合成+播放每个句子片段（不阻塞 LLM 流式接收）"""
            while True:
                seg = await tts_queue.get()
                if seg is None:
                    break
                seg = seg.strip()
                if not seg or not cfg["tts_enabled"]:
                    continue
                try:
                    pcm = await tts_synthesize(
                        seg, cfg["xiaomi_key"], cfg["tts_model"], cfg["tts_voice"]
                    )
                    await asyncio.get_running_loop().run_in_executor(
                        None, play_pcm, pcm, TTS_SAMPLE_RATE
                    )
                except Exception:
                    pass
            tts_done.set()

        worker = asyncio.create_task(_tts_worker())

        buffer = ""
        SENT_END = set("。！？!?；;\n")
        async for delta in llm_stream(
            text, history, cfg["dashscope_key"], cfg["llm_model"], cfg["enable_thinking"]
        ):
            reply.append(delta)
            ui_stream(delta)
            buffer += delta
            # 遇到句子边界或 buffer 过长 → 立即送 TTS（不等 LLM 完成）
            if buffer and (buffer[-1] in SENT_END or len(buffer) >= 80):
                await tts_queue.put(buffer)
                buffer = ""
        # 剩余尾部
        if buffer.strip():
            await tts_queue.put(buffer)
        await tts_queue.put(None)  # 结束信号

        await tts_done.wait()  # 等所有 TTS 片段播完
        worker.cancel()
        ui_print()

        full_reply = "".join(reply).strip()
        if full_reply:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": full_reply})
            history[:] = history[-20:]
    except Exception as e:
        ui_print(f"[错误] {e}", _C.RED)
    finally:
        state.set(processing=False, muted=False)
        ui_print("👂 继续听…（说话即可）", _C.DIM)


async def on_wake(cfg, history, asr_queue):
    global asr_task
    if state.get("listening"):
        return
    state.set(listening=True, mode="listen")
    ui_print("🔔 唤醒！我在听…", _C.BOLD + _C.YELLOW)
    # 取消可能残留的旧 ASR 任务，避免重复连接
    if asr_task is not None and not asr_task.done():
        asr_task.cancel()
    asr_task = asyncio.create_task(
        asr_loop(cfg["asr_url"], asr_queue, lambda t: handle_utterance(t, cfg, history, asr_queue))
    )


# ---------- 跨帧重采样（设备原生采样率 ≠ 16k 时自动修正）----------
class StreamResampler:
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


# ---------- 主流程 ----------
async def main_async(cfg):
    import sounddevice as sd

    loop = asyncio.get_event_loop()
    asr_queue: asyncio.Queue = asyncio.Queue()
    kws_queue: asyncio.Queue = asyncio.Queue()
    history: list = []

    # 启动 RK 端侧 KWS（8767）常驻监听：命中唤醒词即进入对话
    ui_print(f"[初始化] 唤醒词: {', '.join(cfg['wake_words'])}", _C.DIM)
    asyncio.create_task(
        kws_loop(cfg["kws_url"], kws_queue, lambda: on_wake(cfg, history, asr_queue))
    )

    # 音频回调（运行在 portaudio 线程）
    def audio_callback(indata, frames, time_info, status):
        if status:
            pass
        if state.get("muted"):
            return
        pcm_int16 = indata[:, 0] if indata.ndim > 1 else indata
        # 调试：更新电平值（不在 portaudio 回调线程里 print，避免 I/O 阻塞导致 xrun 打断音频流）
        if cfg.get("monitor"):
            audio_callback._last_rms = float(np.sqrt(np.mean(pcm_int16.astype(np.float32) ** 2)))
        # 若设备实际采样率 ≠ 16k，先重采样到 16k（KWS/ASR 都要求 16k）
        if resampler.src_sr != ASR_SAMPLE_RATE:
            pcm_int16 = resampler.process(pcm_int16)
            if pcm_int16.size == 0:
                return
        pcm_bytes = pcm_int16.astype(np.int16).tobytes()
        # 对话中：推给 ASR（8768）
        if state.get("mode") == "listen" and state.get("listening"):
            loop.call_soon_threadsafe(asr_queue.put_nowait, pcm_bytes)
        # 待命(idle)：推给 KWS（8767）做唤醒词检测
        elif state.get("mode") == "idle":
            loop.call_soon_threadsafe(kws_queue.put_nowait, pcm_bytes)

    audio_callback._last_rms = 0.0

    try:
        stream = sd.InputStream(
            device=cfg["input_device"],
            channels=1,
            samplerate=ASR_SAMPLE_RATE,
            dtype="int16",
            blocksize=1024,
            callback=audio_callback,
        )
        stream.start()
        actual_sr = stream.samplerate
        resampler = StreamResampler(actual_sr, ASR_SAMPLE_RATE)
        if actual_sr != ASR_SAMPLE_RATE:
            ui_print(f"  ⚠️  采样率 {actual_sr}Hz → 自动重采样到 16k", _C.YELLOW)
        else:
            ui_print(f"  ✓ 采样率 {actual_sr}Hz  设备={stream.device}", _C.GREEN)

        # monitor 电平条放到异步任务里 print（不阻塞 portaudio 回调线程）
        if cfg.get("monitor"):
            async def _monitor_loop():
                while True:
                    ui_monitor(audio_callback._last_rms, state.get("mode"))
                    await asyncio.sleep(0.2)
            asyncio.create_task(_monitor_loop())
    except Exception as e:
        ui_print(f"[错误] 无法打开麦克风: {e}", _C.RED)
        return

    ui_print("━" * 44, _C.DIM)
    ui_print("  🎙️  小云语音助手 · 唤醒词 + 对话", _C.BOLD)
    ui_print("  说「小云小云」唤醒 → 直接对话", _C.DIM)
    ui_print("  说「退出」回到待命 · Ctrl+C 退出", _C.DIM)
    ui_print("━" * 44, _C.DIM)

    try:
        while True:
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        pass
    finally:
        state.set(listening=False)
        stream.stop()
        stream.close()
        ui_clear_monitor()
        ui_print("已停止。", _C.DIM)


def parse_args():
    p = argparse.ArgumentParser(description="极简语音助手：唤醒词 + 语音对话(TTS)")
    p.add_argument("--asr-url", default=DEFAULT_ASR_URL, help="RK 端侧 ASR WebSocket 地址")
    p.add_argument("--kws-url", default=DEFAULT_KWS_URL, help="RK 端侧 KWS WebSocket 地址（唤醒词）")
    p.add_argument("--wake-words", default=",".join(DEFAULT_WAKE_WORDS), help="唤醒词，仅用于日志展示（真正命中由 RK KWS 服务判定）")

    p.add_argument("--tts-voice", default="mimo_default", help="小米 TTS 音色")
    p.add_argument("--tts-model", default="mimo-v2.5-tts", help="小米 TTS 模型")
    p.add_argument("--think", action="store_true", help="开启大模型深度思考（更慢）")
    p.add_argument("--no-tts", action="store_true", help="关闭语音播报，只打印文字")
    p.add_argument("--input-device", type=str, default=None, help="麦克风设备索引或名称")
    p.add_argument("--output-device", type=str, default=None, help="(预留) 扬声器设备")
    p.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    p.add_argument("--monitor", action="store_true", help="实时显示麦克风输入电平（排查唤醒无响应用）")
    return p.parse_args()


def load_keys():
    # 自动从后端 .env 读取 key
    try:
        from dotenv import load_dotenv
        for f in (os.path.join(BACKEND_DIR, ".env.local"), os.path.join(BACKEND_DIR, ".env")):
            if os.path.exists(f):
                load_dotenv(f)
                break
    except Exception:
        pass
    dashscope_key = os.getenv("DASHSCOPE_API_KEY")
    xiaomi_key = os.getenv("XIAOMI_TTS_API_KEY") or os.getenv("XIAOMI_API_KEY")
    return dashscope_key, xiaomi_key


def main():
    args = parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    dashscope_key, xiaomi_key = load_keys()

    if not dashscope_key:
        ui_print("[错误] 未找到 DASHSCOPE_API_KEY（检查 backend/.env）", _C.RED)
        sys.exit(1)
    if not args.no_tts and not xiaomi_key:
        ui_print("[警告] 未找到 XIAOMI_TTS_API_KEY，关闭语音播报", _C.YELLOW)
        args.no_tts = True

    # 解析输入设备
    input_device = None
    if args.input_device:
        try:
            input_device = int(args.input_device)
        except ValueError:
            input_device = _resolve_device(args.input_device)

    cfg = {
        "asr_url": args.asr_url,
        "kws_url": args.kws_url,
        "wake_words": [w.strip() for w in args.wake_words.split(",") if w.strip()],
        "llm_model": args.llm_model,
        "tts_voice": args.tts_voice,
        "tts_model": args.tts_model,
        "enable_thinking": args.think,
        "tts_enabled": not args.no_tts,
        "dashscope_key": dashscope_key,
        "xiaomi_key": xiaomi_key,
        "input_device": input_device,
        "monitor": args.monitor,
    }

    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        ui_clear_monitor()
        ui_print("已停止。", _C.DIM)


if __name__ == "__main__":
    main()
