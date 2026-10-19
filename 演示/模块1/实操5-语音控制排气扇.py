"""
【模块一·实操5】语音控制排气扇 Demo（复用项目已有语音链路 + 真实设备控制）
==================================================================

全链路：麦克风录音 → RK3588 端侧 ASR 流式识别（复用实操4语音链路）
        → 本地正则解析开关风扇指令（毫秒级）
        → 调用 backend/app/fan.py 真实控制小米智能插座（miIO 协议）
        → 小米 TTS 流式播报执行结果（复用实操4语音链路）

说「打开排气扇 / 开启风扇」→ 识别 → 真实开启设备 → 播报"排气扇已开启"
说「关闭风扇 / 关掉排气扇」→ 识别 → 真实关闭设备 → 播报"排气扇已关闭"
说「退出」结束演示

运行方式（必须用 F:\\python3.12\\python.exe，miio 依赖装在该环境）：
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py --asr-url ws://<3588IP>:8768/asr
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py --text       # 键盘输入指令联调（无需3588和麦克风）
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py --demo      # 模拟识别离线彩排（设备控制仍真实）
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py --no-tts    # 关闭语音播报
    F:\\python3.12\\python.exe 实操5-语音控制排气扇.py --list-devices

依赖：websockets numpy httpx sounddevice miio python-dotenv
退出：说「退出」或 Ctrl+C
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

import numpy as np

# 解决 Windows 终端编码问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------- 路径 & 配置 ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# backend/app 目录（fan.py 所在），加入 sys.path 以便直接 import fan 复用真实设备控制
BACKEND_APP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "backend", "app"))

# 与实操4一致的 RK3588 端侧 ASR 地址（比赛环境固定 IP）
DEFAULT_ASR_URL = "ws://192.168.1.127:8768/asr"
XIAOMI_TTS_URL = "https://api.xiaomimimo.com/v1/chat/completions"
TTS_SAMPLE_RATE = 24000    # TTS 输出采样率（小米 TTS 固定 24k）
ASR_SAMPLE_RATE = 16000    # ASR 输入采样率（端侧 ASR 要求 16k PCM16）

# 退出演示的语音指令
EXIT_PHRASES = ["退出", "结束", "再见"]


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


def ui_print(text: str = "", color: str = "", end: str = "\n"):
    sys.stdout.write(f"{color}{text}{_C.RESET}{end}")
    sys.stdout.flush()


def ui_stream(text: str, color: str = ""):
    sys.stdout.write(f"{color}{text}{_C.RESET}")
    sys.stdout.flush()


# ---------- 全局状态（播报时静音麦克风，防止 TTS 回声被误识别成指令） ----------
class SharedState:
    def __init__(self):
        self.muted = False            # True = 正在播报/控制中，麦克风静音
        self.lock = threading.Lock()

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def get(self, k):
        with self.lock:
            return getattr(self, k)


state = SharedState()
asr_queue = None


# ==================== ① 本地指令快速解析（毫秒级，不调大模型） ====================

# 与模块3实操1一致的正则：命中「打开/开启/启动 + 风扇/排气扇」→ on，「关闭/关掉/停止 + …」→ off
FAN_OPEN_PATTERN = re.compile(r"(打开|开启|启动)(风扇|排气扇)")
FAN_CLOSE_PATTERN = re.compile(r"(关闭|关掉|停止)(风扇|排气扇)")


def parse_fan_cmd(text: str):
    """本地正则解析语音文本，返回 "on" / "off" / None（None = 非风扇指令）"""
    if FAN_OPEN_PATTERN.search(text):
        return "on"
    if FAN_CLOSE_PATTERN.search(text):
        return "off"
    return None


# ==================== ② 真实设备控制（复用 backend/app/fan.py） ====================

def control_fan(action: str) -> dict:
    """调用项目已有 fan.py 的统一控制接口（miIO 协议控制小米智能插座，含状态回读校验）"""
    if BACKEND_APP_DIR not in sys.path:
        sys.path.insert(0, BACKEND_APP_DIR)
    try:
        import fan                      # backend/app/fan.py：fan_control 返回 {success, is_on, message}
    except Exception as e:
        return {"success": False, "error": f"fan.py 导入失败：{e}（请用 F:\\python3.12\\python.exe 运行）"}
    try:
        return fan.fan_control(action)  # 阻塞式网络调用（含 0.5s 回读等待），由线程池执行
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== ③ 语音播报（复用实操4的小米 TTS 流式合成链路） ====================

def split_tts(text: str, max_len: int = 80) -> list:
    """按句/逗号切分文本，长句再按 max_len 截断，供流式合成逐段播报"""
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
    """流式语音合成：yield 每个 pcm16 音频分片，边收边播，播报紧跟指令执行结果"""
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
                    audio_data = audio.get("data")
                    if audio_data:
                        yield base64.b64decode(audio_data)


import queue as _queue_mod


class PcmStreamPlayer:
    """
    流式 PCM 播放器（复用实操4实现）：独立后台播放线程 + 线程安全队列，
    play() 非阻塞入队，播放线程持续写音频设备 → 播报顺滑连续不卡顿。
    """

    def __init__(self):
        self._samplerate = TTS_SAMPLE_RATE
        self._q: "queue.Queue" = _queue_mod.Queue()
        self._stream = None
        self._thread = None
        self._running = False
        self._busy = threading.Event()

    def _write_loop(self):
        import sounddevice as sd

        try:
            if self._stream is None:
                self._stream = sd.RawOutputStream(
                    samplerate=self._samplerate, channels=1, dtype="int16", blocksize=0
                )
                self._stream.start()
            while self._running:
                pcm = self._q.get()
                if pcm is None:
                    break
                self._stream.write(pcm)          # 阻塞写直到设备写完，天然控制节奏
                if self._q.empty():
                    self._busy.clear()           # 队列耗尽 → 当前数据已播完
        except Exception:
            pass
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def play(self, pcm: bytes):
        """非阻塞入队（后台播放线程顺序消费）"""
        if not pcm:
            return
        if not self._running:
            self.start()
        self._busy.set()
        self._q.put(pcm)

    def wait_drained(self):
        """等待已入队音频全部播完（播完再继续听，避免截断）"""
        if self._thread is None:
            return
        deadline = time.time() + 10.0
        while (not self._q.empty() or self._busy.is_set()):
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

    def close(self):
        """停止播放线程并关闭音频设备"""
        if self._thread is not None and self._thread.is_alive():
            self._q.put_nowait(None)
            self._thread.join(timeout=1.0)
        self._running = False
        self._thread = None
        self._stream = None


async def speak(text: str, cfg: dict):
    """播报一段文本：流式合成 → 逐片入队播放 → 等待播完（期间麦克风静音）"""
    if not cfg.get("tts_enabled") or not cfg.get("xiaomi_key"):
        return
    player = PcmStreamPlayer()
    player.start()
    try:
        async for pcm_chunk in tts_synthesize(text, cfg["xiaomi_key"], cfg["tts_model"], cfg["tts_voice"]):
            if pcm_chunk:
                player.play(pcm_chunk)
        await asyncio.get_running_loop().run_in_executor(None, player.wait_drained)
    finally:
        await asyncio.get_running_loop().run_in_executor(None, player.close)


# ==================== ④ RK3588 端侧 ASR 流式识别（复用实操4链路） ====================

async def _ws_receiver_loop(ws, on_final):
    """接收 ASR 服务回推结果：isPartial 实时上屏，final 触发指令处理"""
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") != "asr":
            continue
        text = (msg.get("text") or "").strip()
        is_partial = bool(msg.get("isPartial"))
        if is_partial:
            if text:
                ui_stream(text, _C.CYAN)          # 部分识别结果实时上屏
        elif text:
            ui_print()                             # 换行结束本句的部分结果
            asyncio.ensure_future(on_final(text))  # 最终结果 → 非阻塞处理


async def _asr_sender(ws):
    """从队列取麦克风 PCM 分片持续推给 ASR 服务"""
    while True:
        data = await asr_queue.get()
        try:
            await ws.send(data)
        except Exception:
            pass


async def asr_loop(asr_url: str, on_final):
    """ASR 长连接主循环：断线自动重连，直到程序退出"""
    import websockets

    while True:
        try:
            async with websockets.connect(asr_url, max_size=None, ping_interval=20) as ws:
                ui_print("  ✓ ASR 已连接，随时说「打开排气扇 / 关闭风扇」", _C.GREEN)
                send_task = asyncio.create_task(_asr_sender(ws))
                recv_task = asyncio.create_task(_ws_receiver_loop(ws, on_final))
                done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                ui_print("  ASR 断开，重连中…", _C.YELLOW)
                await asyncio.sleep(1)
        except Exception as e:
            ui_print(f"  ASR 连接失败: {e}，2s 后重试", _C.RED)
            await asyncio.sleep(2)


# ==================== ⑤ 指令处理：识别文本 → 控制设备 → 播报结果 ====================

async def handle_command(text: str, cfg: dict):
    """一轮指令闭环：本地解析 → 真实控设备 → TTS 播报执行结果"""
    # 播报中/控制中忽略新指令，防止回声与并发触发
    if state.get("muted"):
        return

    # 退出指令 → 结束演示
    if any(p in text for p in EXIT_PHRASES):
        ui_print("👋 收到退出指令，演示结束", _C.YELLOW)
        asyncio.get_event_loop().stop()
        return

    # 本地正则解析风扇指令（毫秒级）
    action = parse_fan_cmd(text)
    if action is None:
        ui_print(f"  · 非风扇指令，忽略：「{text}」", _C.DIM)
        return

    state.set(muted=True)                  # 控制与播报期间静音麦克风，防 TTS 回声误触发
    try:
        ui_print(f"🗣️  指令: {text}", _C.CYAN)
        ui_print(f"⚙️  解析结果: {action} → 调用 backend/app/fan.py 控制真实设备…", _C.DIM)

        # 阻塞式设备控制放到线程池执行，不卡事件循环（miIO 网络调用 + 0.5s 状态回读）
        result = await asyncio.get_running_loop().run_in_executor(None, control_fan, action)

        if result.get("success"):
            reply = "排气扇已开启" if action == "on" else "排气扇已关闭"
            ui_print(f"🤖 执行成功: {result.get('message', reply)}", _C.GREEN)
        else:
            reply = "操作失败，请重试"
            ui_print(f"❌ 执行失败: {result.get('error', '未知错误')}", _C.RED)

        # 小米 TTS 流式播报执行结果（复用实操4语音链路）
        await speak(reply, cfg)
    finally:
        state.set(muted=False)             # 播完解除静音，继续听下一条指令
        ui_print("👂 继续听…（说「打开/关闭 排气扇」，或「退出」结束）", _C.DIM)


# ---------- 跨帧重采样（麦克风采样率 ≠ 16k 时自动转换，复用实操4实现） ----------
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


def _resolve_device(name: str):
    """按名称模糊匹配音频输入设备索引"""
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


# ==================== ⑥ 主流程 ====================

async def main_voice(cfg):
    """语音模式主流程：打开麦克风 → ASR 常听 → 识别到风扇指令即真实执行"""
    global asr_queue
    asr_queue = asyncio.Queue()

    import sounddevice as sd
    loop = asyncio.get_event_loop()

    def audio_callback(indata, frames, time_info, status):
        pcm_int16 = indata[:, 0] if indata.ndim > 1 else indata
        # 播报/控制期间静音：丢弃音频，防止 TTS 回声被识别成新指令
        if state.get("muted"):
            return
        # 采样率不一致时重采样到 16k
        if resampler.src_sr != ASR_SAMPLE_RATE:
            pcm_int16 = resampler.process(pcm_int16)
            if pcm_int16.size == 0:
                return
        pcm_bytes = pcm_int16.astype(np.int16).tobytes()
        loop.call_soon_threadsafe(asr_queue.put_nowait, pcm_bytes)

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
        resampler = StreamResampler(stream.samplerate, ASR_SAMPLE_RATE)
        if stream.samplerate != ASR_SAMPLE_RATE:
            ui_print(f"  ⚠️  采样率 {stream.samplerate}Hz → 自动重采样到 16k", _C.YELLOW)
        ui_print(f"  ✓ 麦克风已就绪（设备={stream.device}）", _C.GREEN)
    except Exception as e:
        ui_print(f"[错误] 无法打开麦克风: {e}", _C.RED)
        return

    # ASR 长连接后台任务：识别结果 → handle_command 闭环
    asyncio.create_task(asr_loop(cfg["asr_url"], lambda text: handle_command(text, cfg)))

    ui_print("━" * 50, _C.DIM)
    ui_print("  🎙️  语音控制排气扇演示（真实设备）", _C.BOLD)
    ui_print("  说「打开排气扇」→ 真实开启 → 语音播报结果", _C.DIM)
    ui_print("  说「关闭风扇」→ 真实关闭 → 语音播报结果", _C.DIM)
    ui_print("  说「退出」结束 · Ctrl+C 退出", _C.DIM)
    ui_print("━" * 50, _C.DIM)

    try:
        while True:
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        pass
    finally:
        stream.stop()
        stream.close()
        ui_print("已停止。", _C.DIM)


async def main_text(cfg):
    """键盘联调模式：手动输入指令文本，走同一套解析→控制→播报链路（无需3588和麦克风）"""
    ui_print("━" * 50, _C.DIM)
    ui_print("  ⌨️  键盘联调模式（无需 3588 / 麦克风）", _C.BOLD)
    ui_print("  输入「打开排气扇」「关闭风扇」执行真实控制", _C.DIM)
    ui_print("  输入「退出」结束", _C.DIM)
    ui_print("━" * 50, _C.DIM)
    loop = asyncio.get_event_loop()
    while True:
        text = await loop.run_in_executor(None, input, "\n指令> ")
        text = text.strip()
        if not text:
            continue
        if any(p in text for p in EXIT_PHRASES):
            ui_print("👋 演示结束", _C.YELLOW)
            break
        await handle_command(text, cfg)


async def main_demo(cfg):
    """模拟识别彩排模式：脚本化指令走真实设备控制与播报（离线彩排用）"""
    ui_print("━" * 50, _C.DIM)
    ui_print("  🎬 模拟识别彩排模式（ASR 为脚本模拟，设备控制仍真实）", _C.BOLD)
    ui_print("━" * 50, _C.DIM)
    for text in ["小云小云，打开排气扇", "帮我把风扇关掉", "退出"]:
        await asyncio.sleep(0.8)
        ui_stream("  [模拟流式识别] ", _C.DIM)
        for i in range(1, len(text) + 1):
            ui_stream(text[i - 1], _C.CYAN)
            await asyncio.sleep(0.06)
        ui_print()
        await handle_command(text, cfg)


def probe_asr(asr_url: str) -> bool:
    """探测 RK3588 端侧 ASR 是否可达（真实优先，避免现场静默降级）"""
    import websockets

    async def _probe():
        try:
            ws = await asyncio.wait_for(websockets.connect(asr_url, max_size=None, ping_interval=20), timeout=3)
            await ws.close()
            return True
        except Exception:
            return False
    return asyncio.run(_probe())


def parse_args():
    p = argparse.ArgumentParser(description="语音控制排气扇：RK3588 ASR + 正则解析 + fan.py 真实控制 + 小米 TTS 播报")
    p.add_argument("--asr-url", default=DEFAULT_ASR_URL)
    p.add_argument("--tts-voice", default="mimo_default")
    p.add_argument("--tts-model", default="mimo-v2.5-tts")
    p.add_argument("--text", action="store_true", help="键盘输入指令联调（无需3588和麦克风）")
    p.add_argument("--demo", action="store_true", help="模拟识别离线彩排（设备控制仍真实）")
    p.add_argument("--no-tts", action="store_true", help="关闭语音播报")
    p.add_argument("--input-device", type=str, default=None, help="麦克风设备名或索引")
    p.add_argument("--list-devices", action="store_true")
    return p.parse_args()


def load_keys():
    """读 TTS key：模块1 本地 .env 优先，其次向上找 backend/.env.local（与实操4一致）"""
    try:
        from dotenv import load_dotenv
        for f in (os.path.join(SCRIPT_DIR, ".env"), os.path.join(SCRIPT_DIR, ".env.local")):
            if os.path.exists(f):
                load_dotenv(f, override=True)
                break
        if not os.getenv("XIAOMI_TTS_API_KEY") and not os.getenv("XIAOMI_API_KEY"):
            backend_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "backend"))
            for f in (os.path.join(backend_dir, ".env.local"), os.path.join(backend_dir, ".env")):
                if os.path.exists(f):
                    load_dotenv(f, override=True)
                    break
    except Exception:
        pass
    return os.getenv("XIAOMI_TTS_API_KEY") or os.getenv("XIAOMI_API_KEY")


def main():
    args = parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    cfg = {
        "asr_url": args.asr_url,
        "tts_voice": args.tts_voice,
        "tts_model": args.tts_model,
        "tts_enabled": not args.no_tts,
        "xiaomi_key": load_keys(),
        "input_device": None,
    }
    if cfg["tts_enabled"] and not cfg["xiaomi_key"]:
        ui_print("[警告] 未找到 XIAOMI_TTS_API_KEY，关闭语音播报（设备控制不受影响）", _C.YELLOW)
        cfg["tts_enabled"] = False
    if args.input_device:
        try:
            cfg["input_device"] = int(args.input_device)
        except ValueError:
            cfg["input_device"] = _resolve_device(args.input_device)

    try:
        if args.text:
            asyncio.run(main_text(cfg))
        elif args.demo:
            asyncio.run(main_demo(cfg))
        else:
            # 真实优先：探测 3588 端侧 ASR，连不上报错退出（与实操4同一策略，避免演示失真）
            ui_print("[初始化] 探测 RK3588 端侧 ASR …", _C.DIM)
            if not probe_asr(cfg["asr_url"]):
                ui_print("", _C.RESET)
                ui_print(f"[错误] RK3588 端侧 ASR 不可达: {cfg['asr_url']}", _C.RED)
                ui_print("  请检查 3588 是否在线（ping 192.168.1.248），或用 --asr-url 指定实际地址", _C.YELLOW)
                ui_print("  无 3588 联调可改用：--text（键盘指令）或 --demo（模拟识别彩排）", _C.YELLOW)
                sys.exit(2)
            asyncio.run(main_voice(cfg))
    except KeyboardInterrupt:
        ui_print("\n已停止。", _C.DIM)


if __name__ == "__main__":
    main()
