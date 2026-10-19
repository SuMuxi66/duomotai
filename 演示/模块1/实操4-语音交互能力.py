"""
【模块三：语音交互支持】完整实操演示（真实接入 RK3588 端侧 KWS/ASR）

对标《智能烹饪项目全环节技能实操讲解文档》环节三：
- 演示点1：两级降噪语音唤醒 —— 本地 VAD 音量过滤（噪音抑制）+ RK3588 端侧 KWS 关键词匹配
- 演示点2：流式实时识别 —— 唤醒后音频流分片推给 RK 端侧 ASR，边识别边出部分结果
- 演示点3：InterruptHandler 全链路语音打断 —— 秒级中断 AI 生成与 TTS 播报
- 真实优先：默认探测 RK3588 端侧 KWS(8767)/ASR(8768)，可用则走真实识别链路；
  连不上直接报错退出（仅显式 --demo 才走本地模拟讲解，供离线彩排用）。

运行方式（比赛/现场环境：3588 在线，默认连 192.168.1.248）：
    python 实操4-语音交互能力.py
    python 实操4-语音交互能力.py --kws-url ws://<3588IP>:8767/kws --asr-url ws://<3588IP>:8768/asr
    python 实操4-语音交互能力.py --demo          # 仅本地讲解用：强制纯模拟（现场演示请勿用）
    python 实操4-语音交互能力.py --no-tts        # 关闭语音播报，只打印文字
    python 实操4-语音交互能力.py --list-devices  # 列出音频设备

注意：默认（不加 --demo）会探测 3588 端侧服务，连不上会**报错退出**，
不会静默降级成模拟——避免现场演示失真。

依赖：websockets numpy httpx sounddevice python-dotenv（key 自动读 backend/.env.local）
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

# 解决 Windows 终端编码问题（用 reconfigure，避免 TextIOWrapper 重复包装 stdout.buffer 引发冲突）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------- 路径 & 配置 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 尝试定位 backend 目录读 key（脚本放在 演示/模块1 下，向上找 backend/.env.local）
_BACKEND_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "..", "..", "backend"),
    os.path.join(SCRIPT_DIR, "..", "backend"),
    os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "backend")),
]

# 默认指向 RK3588 端侧 KWS/ASR（比赛环境固定 IP；本机联调可 --kws-url 覆盖）
DEFAULT_KWS_URL = "ws://192.168.1.248:8767/kws"
DEFAULT_ASR_URL = "ws://192.168.1.248:8768/asr"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
XIAOMI_TTS_URL = "https://api.xiaomimimo.com/v1/chat/completions"
TTS_SAMPLE_RATE = 24000
ASR_SAMPLE_RATE = 16000

SYS_PROMPT = (
    "你是一个叫“小云”的中文智能烹饪助手，语气自然、简洁、口语化，"
    "像真人聊天一样回答，不要使用 Markdown 符号，不要列出长篇要点，"
    "一句话能说清就别啰嗦。"
)
HANGUP_PHRASES = ["退出", "结束对话", "停止对话", "挂断", "再见", "先这样"]


# ---------- 终端 UI ----------
if sys.platform == "win32":
    os.system("")


class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


_monitor_on = False


def ui_clear_monitor():
    global _monitor_on
    if _monitor_on:
        sys.stdout.write("\033[2K\r")
        sys.stdout.flush()
        _monitor_on = False


def ui_print(text: str = "", color: str = "", end: str = "\n"):
    ui_clear_monitor()
    sys.stdout.write(f"{color}{text}{_C.RESET}{end}")
    sys.stdout.flush()


def ui_stream(text: str, color: str = ""):
    sys.stdout.write(f"{color}{text}{_C.RESET}")
    sys.stdout.flush()


def ui_monitor(rms: float, mode: str):
    global _monitor_on
    if mode != "idle":
        return
    bar_len = int(min(30, rms / 150))
    bar = "█" * bar_len + "░" * (30 - bar_len)
    color = _C.GRAY if rms < 50 else (_C.GREEN if rms < 3000 else (_C.YELLOW if rms < 8000 else _C.RED))
    sys.stdout.write(f"\r{color}🎙️  {bar} rms={int(rms):5d}{_C.RESET}  待命…说「小云小云」唤醒")
    sys.stdout.flush()
    _monitor_on = True


def _resolve_device(name: str):
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        for i, d in enumerate(sd.query_devices()):
            if name.lower() in (d["name"] or "").lower():
                return i
    except Exception:
        pass
    return None


# ---------- 全局共享状态 ----------
class SharedState:
    def __init__(self):
        self.mode = "idle"          # idle=待唤醒 ; listen=对话中
        self.muted = False          # TTS/LLM 思考时静音，防回声
        self.processing = False
        self.listening = False
        # ---- 回声检测（TTS 外放被麦克风回采导致自打断的防护）----
        self.tts_recent = ""        # 正在播报的 TTS 文本（近期拼接，供 ASR 回声比对）
        self.tts_started_at = 0.0   # 本轮播报开始时间（起始阶段禁用能量打断，避开 TTS 起始尖峰）
        self.echo_rms = 0.0         # 播报期间回采能量滑动平均（回声基线）
        self.loud_frames = 0        # 显著高于回声基线的连续帧数（凑近大喊才打断）
        self.lock = threading.Lock()

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def get(self, k):
        with self.lock:
            return getattr(self, k)


state = SharedState()
asr_task = None
asr_queue = None
kws_queue = None
# 全局打断器：真实模式下 ASR 收到插话时，用来取消当前 AI 生成/播报任务
interrupt_handler = None


# ---------- 回声检测：TTS 外放被麦克风回采 → ASR 识别出播报内容 → 误触发打断 ----------
def _norm_text(s: str) -> str:
    """归一化文本：去掉空白与标点，仅保留有效字符，供回声比对"""
    import re as _re
    return _re.sub(r"[\s，。！？!?,.:：;；、'\"“”「」（）()\-—…·]", "", s or "")


def is_echo_of_tts(text: str) -> bool:
    """判断 ASR 识别文本是否为当前 TTS 播报的回声。

    判据（满足任一即判回声）：
    1. 归一化后与正在播报的 TTS 文本互为子串（回声常为截断片段）；
    2. 与播报文本尾部的序列相似度 > 0.55（SequenceMatcher，容忍 ASR 识别误差）；
    3. 播报期间的超短识别结果（<3 有效字符）——基本为回声碎片。
    """
    recent = state.get("tts_recent") or ""
    if not recent:
        return False
    a, b = _norm_text(recent), _norm_text(text)
    if not b:
        return True
    if len(b) < 3:
        return True
    if b in a or a in b:
        return True
    import difflib
    ratio = difflib.SequenceMatcher(None, a[-80:], b).ratio()
    return ratio > 0.55


# ==================== 演示点1：两级降噪语音唤醒 ====================

class WakeWordDetector:
    """
    【演示点1】两级降噪语音唤醒
    第一级：本地 VAD 音量检测（RMS），过滤炒菜声/环境风等无效噪音；
    第二级：RK3588 端侧 KWS（sherpa-onnx）关键词精准匹配，命中才进入对话。
    模拟回退：离线时用本地关键词匹配兜底。
    """

    def __init__(self):
        self.sample_rate = ASR_SAMPLE_RATE
        self.vad_threshold = 30          # VAD 音量阈值（RMS 0-32767 刻度）
        self.wake_words = ["小云小云", "小云", "小芸小芸"]
        self.audio_buffer = bytearray()
        self.buffer_duration_ms = 2000
        self.on_wake_word_detected = None
        self.real_mode = False           # 是否走真实 KWS

    def calculate_volume(self, pcm_data: bytes) -> float:
        """第一级：VAD 音量计算 —— 基于 RMS 均方根实时计算音频音量"""
        if len(pcm_data) < 2:
            return 0.0
        arr = np.frombuffer(pcm_data, dtype=np.int16)
        return float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))

    def add_audio(self, pcm_data: bytes) -> bool:
        """第一级：音量阈值判定，过滤环境噪音，仅收录有效人声"""
        if self.calculate_volume(pcm_data) < self.vad_threshold:
            return False
        self.audio_buffer.extend(pcm_data)
        max_bytes = int(self.sample_rate * 2 * self.buffer_duration_ms / 1000)
        if len(self.audio_buffer) > max_bytes:
            self.audio_buffer = self.audio_buffer[-max_bytes:]
        return True

    def check_wake_word(self, text: str) -> bool:
        """第二级：本地关键词匹配（模拟回退用；真实模式由 RK KWS 服务判定）"""
        text = text.replace(" ", "").replace("。", "").replace("，", "")
        for wake_word in self.wake_words:
            if wake_word in text:
                if self.on_wake_word_detected:
                    self.on_wake_word_detected(wake_word)
                return True
        return False

    def reset(self):
        self.audio_buffer = bytearray()


# ==================== 演示点2：流式实时识别 ====================

class RealtimeASR:
    """
    【演示点2】流式实时识别
    真实模式：唤醒后音频流分片推给 RK3588 端侧 ASR WebSocket(8768)，
    服务端（sherpa-onnx + silero VAD）实时回推 partial/final 结果。
    模拟回退：本地按 100ms 分片模拟识别进度。
    """

    def __init__(self):
        self.sample_rate = ASR_SAMPLE_RATE
        self.chunk_size = 3200  # 100ms @ 16kHz
        self.on_partial_result = None
        self.on_final_result = None

    async def transcribe_stream(self, pcm_data: bytes) -> str:
        """模拟模式：对音频流实时分片解析，逐片输出部分结果，最终输出完整文本"""
        total_chunks = (len(pcm_data) + self.chunk_size - 1) // self.chunk_size
        for i in range(total_chunks):
            await asyncio.sleep(0.02)
            if self.on_partial_result:
                self.on_partial_result(self._mock_partial_recognition(i, total_chunks))
        final_text = "小云小云，我想做番茄炒蛋"
        if self.on_final_result:
            self.on_final_result(final_text)
        return final_text

    def _mock_partial_recognition(self, chunk_index: int, total_chunks: int) -> str:
        """模拟分片实时识别进度（回退演示用）"""
        progress = chunk_index / total_chunks
        if progress < 0.2:
            return "小云"
        elif progress < 0.4:
            return "小云小云"
        elif progress < 0.6:
            return "小云小云，我想做"
        elif progress < 0.85:
            return "小云小云，我想做番茄"
        else:
            return "小云小云，我想做番茄炒蛋"


# ==================== 演示点3：InterruptHandler 全链路语音打断 ====================

from dataclasses import dataclass


@dataclass
class SessionState:
    """会话状态"""
    interrupted: bool = False
    current_task = None


class InterruptHandler:
    """
    【演示点3】全链路语音打断
    用户随时插话，系统立刻标记打断状态，同步取消当前正在后台运行的
    AI 生成 / TTS 播报任务，实现秒级中断效果，支持灵活连续对话。
    """

    def __init__(self, state_obj: SessionState):
        self.state = state_obj

    async def handle_interrupt(self):
        self.state.interrupted = True
        if self.state.current_task and not self.state.current_task.done():
            self.state.current_task.cancel()
        ui_print("  [打断] 已终止当前播报/生成任务，释放会话通道", _C.YELLOW)

    def check_interrupted(self) -> bool:
        if self.state.interrupted:
            ui_print("  [打断] 检测到打断标志，立即停止当前操作", _C.YELLOW)
            return True
        return False

    def reset(self):
        self.state.interrupted = False


# ---------- LLM（DashScope / ModelScope 流式）----------
async def llm_stream(text: str, history: list, api_key: str, model: str, provider: str = "dashscope", thinking: bool = False):
    """流式生成器：yield (kind, text)。kind ∈ {"reasoning"(思考过程), "content"(正文)}"""
    # OpenAI 兼容 SDK 分支：modelscope / siliconflow / openai / deepseek（不同平台仅 base_url 不同）
    if provider in ("modelscope", "openai", "siliconflow", "deepseek"):
        try:
            from openai import AsyncOpenAI
        except Exception as e:
            raise RuntimeError(f"未安装 openai SDK：{e}（pip install openai）")

        base_urls = {
            "modelscope": "https://api-inference.modelscope.cn/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com",
        }
        client = AsyncOpenAI(
            base_url=base_urls[provider],
            api_key=api_key,
            timeout=120.0,
        )
        messages = [{"role": "system", "content": SYS_PROMPT}]
        messages += history
        messages.append({"role": "user", "content": text})
        # 思考开关：开启则支持 reasoning_content；关闭则不思考，正文更快
        # （enable_thinking 仅 ModelScope 支持；SiliconFlow 对不支持该参数的模型会忽略/报错，故不传）
        req_kwargs = {"model": model, "messages": messages, "stream": True}
        if not thinking and provider == "modelscope":
            req_kwargs["extra_body"] = {"enable_thinking": False}
        try:
            stream = await client.chat.completions.create(**req_kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None) or ""
                content = getattr(delta, "content", None) or ""
                if reasoning:
                    yield ("reasoning", reasoning)
                if content:
                    yield ("content", content)
        except Exception as e:
            raise RuntimeError(f"{provider} 请求失败：{e}")
        return

    # DashScope：httpx 直接流式
    messages = [{"role": "system", "content": SYS_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": text})
    # 思考开关：true 让模型思考（先 reasoning 后 content），false 直接出正文更快
    payload = {"model": model, "messages": messages, "stream": True, "enable_thinking": thinking}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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
                delta_obj = (obj.get("choices") or [{}])[0].get("delta", {}) or {}
                # 优先取正文 content；思考模式下先出 reasoning_content，兜底也吐出来避免"无输出"
                delta = delta_obj.get("content") or delta_obj.get("reasoning_content")
                if delta:
                    yield ("reasoning" if not delta_obj.get("content") else "content", delta)


def split_tts(text: str, max_len: int = 80) -> list:
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
            out.extend(cl[i:i + max_len] for i in range(0, len(cl), max_len)) if len(cl) > max_len else out.append(cl)
    return out


async def tts_synthesize(text: str, api_key: str, model: str, voice: str):
    """流式语音合成（真流式）：yield 每个 pcm16 音频分片，边收边播，紧跟文字"""
    import httpx

    segments = split_tts(text)
    if not segments:
        return
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
                    choices = obj.get("choices") or []
                    if not choices or choices[0] is None:
                        continue
                    audio = choices[0].get("delta", {}).get("audio")
                    if not audio:
                        continue
                    audio_data = audio.get("data", "") if isinstance(audio, dict) else ""
                    if audio_data:
                        yield base64.b64decode(audio_data)


import queue as _queue_mod


def drain_queue(q: asyncio.Queue):
    """清空 asyncio.Queue 中所有未消费的元素（打断时丢弃待播分句）"""
    while True:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break
        except Exception:
            break


class PcmStreamPlayer:
    """
    流式 PCM 播放器：独立后台播放线程 + 线程安全队列。
    接收端 play() 非阻塞入队，播放线程持续顺序写音频设备 → 避免反复切换线程造成卡顿。
    """

    def __init__(self):
        self._samplerate = TTS_SAMPLE_RATE
        self._q: "queue.Queue" = _queue_mod.Queue()
        self._stream = None
        self._thread = None
        self._running = False
        self._stop = False  # 打断硬停标志：置真后立即停止写入并关闭设备
        # 播放线程每写完一片，若队列仍有数据则置 busy；队列耗尽则置 idle（表示已播完当前全部数据）
        self._busy = threading.Event()

    def _write_loop(self):
        import sounddevice as sd

        try:
            if self._stream is None:
                self._stream = sd.RawOutputStream(
                    samplerate=self._samplerate, channels=1, dtype="int16", blocksize=0
                )
                self._stream.start()
            while self._running and not self._stop:
                pcm = self._q.get()
                if pcm is None or self._stop:
                    break
                # 阻塞写直到设备有空间，天然控制节奏，连续顺滑
                self._stream.write(pcm)
                # 写后立即检查打断，尽快退出
                if self._stop:
                    break
                # 队列清空则标记 idle（当前全部数据已播完）；否则保持 busy
                if self._q.empty():
                    self._busy.clear()
        except Exception:
            pass
        finally:
            # 立即停止设备，中断任何未写尽的音频
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def play(self, pcm: bytes):
        """非阻塞入队（由后台播放线程顺序消费）"""
        if not pcm or self._stop:  # 打断后拒绝再入队，避免"串了"
            return
        if not self._running:
            self.start()
        self._busy.set()
        self._q.put(pcm)

    def wait_drained(self):
        """等待当前已入队数据全部播完（队列清空），但不终止播放线程"""
        if self._thread is None:
            return
        # 队列非空或播放线程正忙于写，则等待；最多等 5s 兜底（_stop 时立即返回）
        deadline = time.time() + 5.0
        while (not self._q.empty() or self._busy.is_set()) and not self._stop:
            if time.time() > deadline:
                break
            time.sleep(0.02)

    def start(self):
        if self._running:
            return
        self._running = True
        self._q = _queue_mod.Queue()
        self._busy = threading.Event()
        self._thread = threading.Thread(target=self._write_loop, daemon=True)
        self._thread.start()

    def flush(self, wait: float = 0.05):
        """播完尾音后稍候，给播放线程流出时间写最后一段"""
        if self._thread is not None:
            time.sleep(wait)

    def close(self):
        """立即硬停：置终止标志、清空队列、abort 设备，并等待播放线程彻底退出"""
        self._stop = True
        self._busy.set()  # 唤醒可能在等待的 wait_drained
        # 清空队列，丢弃未播分片
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except Exception:
                break
        # 放入 None 唤醒可能阻塞在 queue.get() 的播放线程
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        # 立即 abort 流，中断正在进行的 write
        if self._stream is not None and self._running:
            try:
                self._stream.abort()
            except Exception:
                pass
        # 等待播放线程彻底结束，确保 write 被中断并退出
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._running = False
        self._thread = None
        self._stream = None


def play_pcm(pcm: bytes, samplerate: int):
    """非流式播放（保留给静音/等待判断用）：完整播放一段 PCM"""
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


# ---------- RK3588 端侧 ASR / KWS WebSocket ----------
async def _ws_receiver_loop(ws, kind: str, on_msg):
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if kind == "asr":
            if msg.get("type") == "asr":
                text = (msg.get("text") or "").strip()
                is_partial = bool(msg.get("isPartial"))
                # 流式实时识别：partial 结果实时上屏（边识别边出字）
                if is_partial:
                    if text:
                        ui_stream(text, _C.CYAN)
                # 最终结果：非阻塞触发对话（打断逻辑在 on_msg 包装里处理）
                elif text:
                    ui_print()
                    asyncio.ensure_future(on_msg(text))
        elif kind == "kws":
            if msg.get("event") == "wakeword":
                keyword = msg.get("keyword", "")
                ui_print(f"🔔 命中唤醒词: {keyword}" if keyword else "🔔 命中唤醒词", _C.BOLD + _C.YELLOW)
                asyncio.ensure_future(on_msg())


async def asr_loop(asr_url: str, on_final):
    import websockets

    while state.get("listening"):
        try:
            async with websockets.connect(asr_url, max_size=None, ping_interval=20) as ws:
                ui_print(f"  ✓ ASR 已连接（真实模式）", _C.GREEN)
                send_task = asyncio.create_task(_asr_sender(ws))
                recv_task = asyncio.create_task(_ws_receiver_loop(ws, "asr", on_final))
                done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
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


async def _asr_sender(ws):
    while True:
        data = await asr_queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


async def kws_loop(kws_url: str, on_wake):
    import websockets

    while True:
        try:
            async with websockets.connect(kws_url, max_size=None, ping_interval=20) as ws:
                ui_print(f"  ✓ KWS 已连接，等待唤醒词…（真实模式）", _C.GREEN)
                send_task = asyncio.create_task(_kws_sender(ws))
                recv_task = asyncio.create_task(_ws_receiver_loop(ws, "kws", on_wake))
                done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                ui_print("  KWS 断开，1s 后重连…", _C.YELLOW)
                await asyncio.sleep(1)
        except Exception as e:
            ui_print(f"  KWS 连接失败: {e}", _C.RED)
            await asyncio.sleep(2)


async def _kws_sender(ws):
    while True:
        data = await kws_queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


# ---------- 一轮对话 ----------
async def handle_utterance(text: str, cfg, history: list):
    if state.get("processing"):
        return
    state.set(processing=True, muted=True, tts_recent="", tts_started_at=time.time(), echo_rms=0.0, loud_frames=0)
    # 注册当前任务到打断器，供实时插话取消（全链路打断）
    if interrupt_handler:
        interrupt_handler.state.current_task = asyncio.current_task()
    ui_print(f"🗣️  你: {text}", _C.CYAN)

    if any(p in text for p in HANGUP_PHRASES):
        ui_print("👋 回到待命（说唤醒词重新开始）", _C.DIM)
        state.set(listening=False, mode="idle", processing=False, muted=False)
        return
    try:
        reply = []
        ui_print("🤖 AI: ", _C.GREEN, end="")

        tts_queue: asyncio.Queue = asyncio.Queue()
        tts_done = asyncio.Event()

        # TTS 播报 worker：分句入队 → 流式合成（边收边播），与文字上屏并行
        player = PcmStreamPlayer()

        async def _tts_worker():
            while True:
                seg = await tts_queue.get()
                if seg is None:
                    break
                seg = seg.strip()
                if not seg or not cfg["tts_enabled"]:
                    continue
                try:
                    # 流式合成：每个 pcm 分片立即非阻塞入队 → 后台播放线程顺序写，顺滑连续
                    async for pcm_chunk in tts_synthesize(seg, cfg["xiaomi_key"], cfg["tts_model"], cfg["tts_voice"]):
                        # 打断检查：一旦被插话，立即停止合成与入队，不再推送新音频
                        if player._stop or (interrupt_handler and interrupt_handler.check_interrupted()):
                            raise asyncio.CancelledError("tts interrupted")
                        if pcm_chunk:
                            player.play(pcm_chunk)
                    # 每句播完稍候，避免尾音截断
                    await asyncio.get_running_loop().run_in_executor(None, player.flush, 0.05)
                except asyncio.CancelledError:
                    # 被打断：丢弃剩余待播分句、立即停播、退出 worker，不再播报上一句
                    drain_queue(tts_queue)
                    await asyncio.get_running_loop().run_in_executor(None, player.close)
                    break
                except Exception:
                    pass
            # 最后一句：等后台播放线程把全部已入队分片播完，再标记完成，避免"没播完就继续听"
            await asyncio.get_running_loop().run_in_executor(None, player.wait_drained)
            tts_done.set()

        worker = asyncio.create_task(_tts_worker())

        buffer = ""
        # 小分句边界：句末 + 逗号/顿号，均提前送播，让语音紧跟文字（并行）
        SENT_END = set("。！？!?；;\n，、,， ")

        # 通道级自动兜底：主通道欠费/鉴权类失败（402/429/401/账户状态异常）时，
        # 自动切换 cfg["llm_chain"] 里的下一个备用通道重试；
        # 仅在尚未输出任何正文时兜底（reply 非空 = 已开始播报，切通道会重复答案）
        async def _llm_with_fallback():
            chain = cfg.get("llm_chain") or [(cfg["llm_provider"], cfg["llm_model"], cfg["llm_key"])]
            last_exc = None
            for provider, model, key in chain:
                produced = bool(reply)
                try:
                    async for kind, delta in llm_stream(text, history, key, model, provider, cfg.get("thinking", False)):
                        produced = True
                        yield kind, delta
                    return
                except RuntimeError as e:
                    last_exc = e
                    msg = str(e)
                    retriable = any(c in msg for c in ("402", "429", "401", "not in good standing"))
                    if produced or not retriable:
                        raise
                    ui_print(f"  ⚠️ LLM 通道 {provider} 不可用，自动切换备用通道…", _C.YELLOW)
            if last_exc:
                raise last_exc

        try:
            async for kind, delta in _llm_with_fallback():
                # 全链路打断：AI 生成/播报途中被插话，立即退出生成循环
                if interrupt_handler and interrupt_handler.check_interrupted():
                    raise asyncio.CancelledError("interrupted by user")
                # 思考过程：仅灰色打印，不推 TTS（避免播报一堆"我在想..."）
                if kind == "reasoning":
                    ui_stream(delta, _C.GRAY)
                    continue
                # 正文：上屏 + 推送 TTS 播报
                reply.append(delta)
                ui_stream(delta)
                buffer += delta
                # 遇到句末/逗号边界，或攒够一定长度，立即送入播报队列（并行）
                if buffer and (buffer[-1] in SENT_END or len(buffer) >= 26):
                    await tts_queue.put(buffer)
                    # 记录播报文本供回声比对（ASR 识别出相似内容 = 回声，不触发打断）
                    state.set(tts_recent=((state.get("tts_recent") or "") + buffer)[-240:])
                    buffer = ""
            if buffer.strip():
                await tts_queue.put(buffer)
                state.set(tts_recent=((state.get("tts_recent") or "") + buffer)[-240:])
        except asyncio.CancelledError:
            # 打断清理：丢弃剩余待播分句、停止 TTS worker 与播报，立即释放播放流
            drain_queue(tts_queue)
            if not worker.done():
                worker.cancel()
            await asyncio.get_running_loop().run_in_executor(None, player.close)
            ui_print("  ⚡ 对话被打断", _C.YELLOW)
            raise
        await tts_queue.put(None)
        await tts_done.wait()
        worker.cancel()
        await asyncio.get_running_loop().run_in_executor(None, player.close)
        ui_print()

        full_reply = "".join(reply).strip()
        if full_reply:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": full_reply})
            history[:] = history[-20:]
    except Exception as e:
        ui_print(f"[错误] {e}", _C.RED)
    finally:
        state.set(processing=False, muted=False, tts_recent="", loud_frames=0)
        if interrupt_handler:
            interrupt_handler.state.current_task = None
        ui_print("👂 继续听…（说话即可）", _C.DIM)


async def on_wake(cfg, history):
    global asr_task
    if state.get("listening"):
        return
    state.set(listening=True, mode="listen")
    ui_print("🔔 唤醒！我在听…", _C.BOLD + _C.YELLOW)

    if not cfg.get("real_mode"):
        # 模拟回退：无 3588 时，自动演示识别结果进入对话（讲解用）
        ui_print("  [模拟] 正在流式识别…", _C.DIM)
        await asyncio.sleep(0.8)
        ui_print("  [部分识别] 小云小云，我想做", _C.CYAN)
        await asyncio.sleep(0.6)
        ui_print("  [最终识别] 小云小云，我想做番茄炒蛋", _C.CYAN)
        await handle_utterance("小云小云，我想做番茄炒蛋", cfg, history)
        return

    if asr_task is not None and not asr_task.done():
        asr_task.cancel()

    # 全链路打断：AI 生成/播报途中用户插话时，先取消当前任务、等待收尾，再进入新一轮对话
    last_interrupt_at = 0.0
    async def on_final(text: str):
        nonlocal last_interrupt_at
        now = time.time()
        # 打断冷却：上一次打断后短时间内忽略连续 final，避免播报回声连环误打断
        if now - last_interrupt_at < 1.5:
            return
        if state.get("processing"):
            # 回声检测：识别文本与正在播报的 TTS 文本高度重合 → 判为播报回声，静默丢弃
            if is_echo_of_tts(text):
                return
            if interrupt_handler:
                await interrupt_handler.handle_interrupt()
                last_interrupt_at = now
                # 等当前任务真正取消并清空引用，避免并发/残留
                for _ in range(50):
                    if not interrupt_handler.state.current_task:
                        break
                    await asyncio.sleep(0.05)
                interrupt_handler.reset()
        await handle_utterance(text, cfg, history)

    asr_task = asyncio.create_task(asr_loop(cfg["asr_url"], on_final))


# ---------- 跨帧重采样 ----------
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


# ---------- 真实链路探测 ----------
async def probe_real(cfg) -> bool:
    """探测 RK3588 端侧 KWS/ASR 是否可用（真实优先）"""
    import websockets

    for name, url in (("KWS", cfg["kws_url"]), ("ASR", cfg["asr_url"])):
        try:
            ws = await asyncio.wait_for(websockets.connect(url, max_size=None, ping_interval=20), timeout=3)
            await ws.close()
        except Exception as e:
            ui_print(f"  [探测] {name} {url} 不可达: {e}", _C.YELLOW)
            return False
    return True


# ---------- 主流程 ----------
async def main_async(cfg):
    global asr_queue, kws_queue, interrupt_handler

    history: list = []
    real_mode = cfg["real_mode"]

    # 初始化全局打断器（真实模式全链路打断用）
    interrupt_handler = InterruptHandler(SessionState())

    # 演示点1 两级唤醒（真实 KWS or 本地模拟）
    detector = WakeWordDetector()
    detector.real_mode = real_mode
    # 模拟回退：本地关键词匹配命中 → 触发完整唤醒流程
    detector.on_wake_word_detected = lambda wake_word: asyncio.create_task(on_wake(cfg, history))

    asr_queue = asyncio.Queue()
    kws_queue = asyncio.Queue()

    if real_mode:
        ui_print(f"[初始化] 真实模式：RK 端侧 KWS/ASR（唤醒词: 小云小云/小芸小芸）", _C.GREEN)
        asyncio.create_task(kws_loop(cfg["kws_url"], lambda: on_wake(cfg, history)))
    else:
        ui_print(f"[初始化] 模拟模式：本地两级唤醒 + 模拟流式识别（3588 不可达自动回退）", _C.YELLOW)
        # 模拟模式仍走同一套交互，仅识别源不同（见 audio_callback 分支）

    import sounddevice as sd

    loop = asyncio.get_event_loop()

    def audio_callback(indata, frames, time_info, status):
        if status:
            pass
        pcm_int16 = indata[:, 0] if indata.ndim > 1 else indata
        # 实时计算 RMS（音量）
        rms = float(np.sqrt(np.mean(pcm_int16.astype(np.float32) ** 2)))
        audio_callback._last_rms = rms

        # VAD 实时打断（带回声门控）：播报期间麦克风必然采到 TTS 外放回声，
        # 不能用固定阈值（rms>300 会被自己的播报声触发自打断）。
        # 策略：① 播报起始 0.6s 内不判定（避开 TTS 起始尖峰）；
        #       ② 持续更新回采能量基线 echo_rms（EMA）；
        #       ③ 仅当能量显著高于基线（真人凑近喊话）且连续 3 帧（≈0.2s）才打断；
        #       ④ 轻声插话走 ASR final 路径，由 is_echo_of_tts 文本比对过滤回声。
        if state.get("processing") and state.get("muted"):
            now = time.time()
            if now - (state.get("tts_started_at") or 0.0) > 0.6:
                echo = state.get("echo_rms") or 0.0
                state.set(echo_rms=echo * 0.9 + rms * 0.1)
                if rms > 5000 and rms > echo * 2.0 + 1500:
                    loud = state.get("loud_frames") + 1
                    state.set(loud_frames=loud)
                    if loud >= 3 and interrupt_handler and not interrupt_handler.state.interrupted:
                        state.set(loud_frames=0)
                        loop.call_soon_threadsafe(lambda: asyncio.create_task(interrupt_handler.handle_interrupt()))
                        # 清空 ASR 队列，丢弃播报回声数据，避免被识别成用户指令
                        while not asr_queue.empty():
                            try:
                                asr_queue.get_nowait()
                            except Exception:
                                break
                else:
                    state.set(loud_frames=0)

        if cfg.get("monitor"):
            pass  # _last_rms 已实时更新
        if resampler.src_sr != ASR_SAMPLE_RATE:
            pcm_int16 = resampler.process(pcm_int16)
            if pcm_int16.size == 0:
                return
        pcm_bytes = pcm_int16.astype(np.int16).tobytes()
        if state.get("mode") == "listen" and state.get("listening"):
            loop.call_soon_threadsafe(asr_queue.put_nowait, pcm_bytes)
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

        if cfg.get("monitor"):
            async def _monitor_loop():
                while True:
                    ui_monitor(audio_callback._last_rms, state.get("mode"))
                    await asyncio.sleep(0.2)
            asyncio.create_task(_monitor_loop())
    except Exception as e:
        ui_print(f"[错误] 无法打开麦克风: {e}", _C.RED)
        return

    # ---- 模拟回退：无 3588 时用本地 KWS 判定 + 模拟 ASR ----
    async def _sim_fallback():
        """3588 不可达时：周期演示「两级唤醒 → 流式识别 → AI 对话」全链路（讲解用）"""
        while True:
            await asyncio.sleep(0.5)
            if real_mode:
                continue
            if state.get("mode") != "idle":
                continue
            # 第一级：模拟 VAD 音量判定（有效人声）
            mock_audio = np.random.randint(-60, 60, 3200, dtype=np.int16)
            pcm_data = mock_audio.tobytes()
            has_speech = detector.add_audio(pcm_data)
            rms = detector.calculate_volume(pcm_data)
            ui_print(f"    [VAD] RMS={rms:5.1f} 判定={'有效人声' if has_speech else '噪音/过滤'}", _C.DIM)
            if has_speech:
                # 第二级：本地关键词匹配 → 触发唤醒
                ui_print("    [KWS] 命中唤醒词「小云小云」", _C.GREEN)
                detector.check_wake_word("小云小云")
                # 等待对话结束后再演示下一轮
                for _ in range(120):
                    await asyncio.sleep(0.5)
                    if real_mode:
                        break
                    if state.get("mode") == "idle" and not state.get("processing"):
                        break

    if not real_mode:
        asyncio.create_task(_sim_fallback())

    ui_print("━" * 44, _C.DIM)
    ui_print("  🎙️  小云智能烹饪助手 · 语音交互演示", _C.BOLD)
    ui_print(f"  模式: {'真实(RK3588 KWS/ASR)' if real_mode else '模拟回退'}", _C.DIM)
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
    p = argparse.ArgumentParser(description="语音交互演示：RK3588 端侧 KWS/ASR 真实接入 + 模拟回退")
    p.add_argument("--asr-url", default=DEFAULT_ASR_URL)
    p.add_argument("--kws-url", default=DEFAULT_KWS_URL)
    p.add_argument("--llm-model", default=None,
                   help="LLM 模型名（默认按 provider 自动选择：siliconflow=Qwen/Qwen2.5-7B-Instruct、modelscope=Qwen/Qwen3-8B、dashscope=qwen3.5-plus）")
    p.add_argument("--llm-provider", default="deepseek",
                   choices=["modelscope", "siliconflow", "dashscope", "deepseek"],
                   help="LLM 服务商：deepseek(默认)；modelscope 当前欠费429、dashscope 当前400 不可用；siliconflow 备用")
    p.add_argument("--llm-key", default=None, help="LLM API Key（默认读环境变量；ModelScope 填 ModelScope Token）")
    p.add_argument("--think", type=int, choices=[0, 1], default=None,
                   help="是否开启模型思考：0=不开启(更快) 1=开启(先思考再回答)；不传则在启动时询问")
    p.add_argument("--tts-voice", default="mimo_default")
    p.add_argument("--tts-model", default="mimo-v2.5-tts")
    p.add_argument("--demo", action="store_true", help="强制纯模拟，不做 3588 探测")
    p.add_argument("--no-tts", action="store_true", help="关闭语音播报")
    p.add_argument("--input-device", type=str, default=None)
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--monitor", action="store_true", help="实时显示麦克风输入电平")
    return p.parse_args()


def load_keys():
    try:
        from dotenv import load_dotenv
        # 优先级 1：脚本所在目录下的 .env 与 .env.local（模块1 本地 token，最直接）
        # 两个都加载且 .env.local 后加载（覆盖 .env）——.env.local 不入 git，可放个人 key
        for f in (os.path.join(SCRIPT_DIR, ".env"), os.path.join(SCRIPT_DIR, ".env.local")):
            if os.path.exists(f):
                load_dotenv(f, override=True)
        # 优先级 2：向上找 backend 的 .env / .env.local 兜底
        # override=False：只补当前缺失的 key（如 SILICONFLOW_API_KEY），不覆盖模块1 本地已有值
        needed = ("MODELSCOPE_API_KEY", "DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY",
                  "XIAOMI_TTS_API_KEY", "DEEPSEEK_API_KEY")
        for d in _BACKEND_CANDIDATES:
            for f in (os.path.join(d, ".env.local"), os.path.join(d, ".env")):
                if os.path.exists(f):
                    load_dotenv(f, override=False)
                    break
            if all(os.getenv(k) for k in needed):
                break
    except Exception:
        pass
    return (os.getenv("DASHSCOPE_API_KEY"),
            os.getenv("XIAOMI_TTS_API_KEY") or os.getenv("XIAOMI_API_KEY"),
            os.getenv("MODELSCOPE_API_KEY"),
            os.getenv("SILICONFLOW_API_KEY"),
            os.getenv("DEEPSEEK_API_KEY"))


def main():
    args = parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    dashscope_key, xiaomi_key, modelscope_key, siliconflow_key, deepseek_key = load_keys()

    # 按 provider 选择对应的 API Key（--llm-key 显式传入优先，其次读环境变量）
    llm_provider = args.llm_provider
    if llm_provider == "modelscope":
        llm_key = args.llm_key or modelscope_key
        if not llm_key:
            ui_print("[错误] ModelScope 需要 API Key：用 --llm-key 传入，或设置 MODELSCOPE_API_KEY", _C.RED)
            sys.exit(1)
    elif llm_provider == "siliconflow":
        llm_key = args.llm_key or siliconflow_key
        if not llm_key:
            ui_print("[错误] SiliconFlow 需要 API Key：用 --llm-key 传入，或设置 SILICONFLOW_API_KEY", _C.RED)
            sys.exit(1)
    elif llm_provider == "deepseek":
        llm_key = args.llm_key or deepseek_key
        if not llm_key:
            ui_print("[错误] DeepSeek 需要 API Key：用 --llm-key 传入，或设置 DEEPSEEK_API_KEY（模块1/.env.local）", _C.RED)
            sys.exit(1)
    else:
        llm_key = args.llm_key or dashscope_key
        if not llm_key:
            ui_print("[错误] DashScope 需要 DASHSCOPE_API_KEY（检查 backend/.env.local 或 --llm-key）", _C.RED)
            sys.exit(1)

    # 模型名默认值按 provider 自动选择（--llm-model 显式传入优先）
    default_models = {
        "modelscope": "Qwen/Qwen3-8B",
        "dashscope": "qwen3.5-plus",
        "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
        "deepseek": "deepseek-v4-flash-vision-exp",
    }
    llm_model = args.llm_model or default_models[llm_provider]
    ui_print(f"  ? LLM 通道: {llm_provider} / {llm_model}", _C.DIM)

    # 通道兜底链：主通道在前，其余有 key 的通道依次兜底（欠费/鉴权失败自动切换）
    provider_defaults = {
        "deepseek": (deepseek_key, "deepseek-v4-flash-vision-exp"),
        "siliconflow": (siliconflow_key, "Qwen/Qwen2.5-7B-Instruct"),
        "modelscope": (modelscope_key, "Qwen/Qwen3-8B"),
        "dashscope": (dashscope_key, "qwen3.5-plus"),
    }
    llm_chain = [(llm_provider, llm_model, llm_key)]
    for p_name, (p_key, p_model) in provider_defaults.items():
        if p_name != llm_provider and p_key:
            llm_chain.append((p_name, p_model, p_key))
    if not args.no_tts and not xiaomi_key:
        ui_print("[警告] 未找到 XIAOMI_TTS_API_KEY，关闭语音播报", _C.YELLOW)
        args.no_tts = True

    # 思考开关：--think 显式传入优先，否则启动时交互询问（0=不开启更快，1=开启先思考）
    thinking = bool(args.think) if args.think is not None else None
    while thinking is None:
        try:
            ans = input("是否开启模型思考？(0=不开启·更快，1=开启·先思考再回答) [默认0] > ").strip()
        except (EOFError, KeyboardInterrupt):
            ui_print("", _C.DIM)
            ans = ""
        if ans == "":
            thinking = False  # 默认不思考，语音对话更快
        elif ans in ("0", "1"):
            thinking = ans == "1"
        else:
            ui_print("  请输入 0 或 1", _C.YELLOW)
    ui_print(f"  ✓ 模型思考: {'开启(先思考)' if thinking else '关闭(更快)'}", _C.DIM)

    input_device = None
    if args.input_device:
        try:
            input_device = int(args.input_device)
        except ValueError:
            input_device = _resolve_device(args.input_device)

    cfg = {
        "asr_url": args.asr_url,
        "kws_url": args.kws_url,
        "llm_model": llm_model,
        "llm_provider": llm_provider,
        "llm_key": llm_key,
        "llm_chain": llm_chain,
        "thinking": thinking,
        "tts_voice": args.tts_voice,
        "tts_model": args.tts_model,
        "tts_enabled": not args.no_tts,
        "xiaomi_key": xiaomi_key,
        "input_device": input_device,
        "monitor": args.monitor,
        "real_mode": not args.demo,
    }

    # 真实优先：探测 3588 端侧服务。默认（非 --demo）连不上即报错退出，
    # 避免现场静默降级成模拟导致演示失真；只有显式 --demo 才走模拟讲解。
    if cfg["real_mode"]:
        ui_print("[初始化] 探测 RK3588 端侧 KWS/ASR …", _C.DIM)
        if not asyncio.run(probe_real(cfg)):
            ui_print("", _C.RESET)
            ui_print("[错误] RK3588 端侧 KWS/ASR 不可达！", _C.RED)
            ui_print(f"  KWS: {cfg['kws_url']}", _C.RED)
            ui_print(f"  ASR: {cfg['asr_url']}", _C.RED)
            ui_print("  请检查：", _C.YELLOW)
            ui_print("    1. 3588 是否开机且服务已拉起（bash scripts/start_kws.sh / start_asr.sh）", _C.YELLOW)
            ui_print("    2. PC 与 3588 是否同局域网，能否 ping 通 192.168.1.248", _C.YELLOW)
            ui_print("    3. 若 IP 不同，用 --kws-url / --asr-url 指定实际地址", _C.YELLOW)
            ui_print("  ⚠️ 只有显式加 --demo 才会走本地模拟讲解（现场演示请勿使用）", _C.RED)
            sys.exit(2)

    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        ui_clear_monitor()
        ui_print("已停止。", _C.DIM)


if __name__ == "__main__":
    main()
