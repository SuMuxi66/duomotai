"""
【实操5：油温问答与盯守】—— 真实全链路演示（唤醒架构照 cmd_voice_chat.py，与前端同款）

与前端完全一致的三段真实链路（答辩稿颜色标注 → 后端真实源码）：
  ① 唤醒：麦克风常开，待命时 16k PCM16 裸帧推 RK3588 端侧 KWS（8767/kws）
     → 命中「小云小云」服务端推 {"event":"wakeword"} → 进入对话（同前端 createSttWakeWordListener）
  ② 识别：对话中同一麦克风流切推端侧 FunASR（8768/asr）→ 出句即回
     {"type":"asr","isPartial":false,"text":...}
  ③ 对话：识别文本经 WS 发后端 8010 主链路（不经过任何脚本内模拟）——
     【稿·红】llm_handler 入口拦截 looks_like_oil_temp_query → 不进大模型
     【稿·黄】get_temperature → 红外测温真实锅面最高温（oil_watch.py）
     【稿·蓝】意图回答：还差多少度 / 已达到 / 下锅后不用关小火
     【稿·紫】TTS 与主项目同步调用 xiaomi mimo → tts_pcm(24k) 下发 → 本脚本播放
     【稿·绿】回"好"后端确认门启动盯守 → 每 1 秒采样 → 到点主动推送语音

用法：
  python 实操5-稿中代码-油温完整闭环.py                    # 全真实：KWS 唤醒 + 语音问答
  python 实操5-稿中代码-油温完整闭环.py --kws-url ws://192.168.1.248:8767/kws --asr-url ws://192.168.1.248:8768/asr
  python 实操5-稿中代码-油温完整闭环.py --text             # 文本兜底：input 代替语音，其余链路相同
  说「退出」回到待命；Ctrl+C 退出程序
"""

import sys
import io
import os
import time
import json
import base64
import asyncio
import threading
from idlelib import query
from pydoc import text
from unittest import result

import numpy as np
from numba.cuda import target

from backend.app.oil_watch import get_valid_oil_temp

# 解决 Windows 终端编码问题（中文输出）
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TEXT_MODE = "--text" in sys.argv

BACKEND_WS = "ws://127.0.0.1:8010/ws"
SESSION_ID = "demo_oil_practice5"
WAKE_WORD = "小云小云"
DEFAULT_KWS_URL = "ws://127.0.0.1:8767/kws"     # RK3588 端侧 KWS（与前端同款）
DEFAULT_ASR_URL = "ws://127.0.0.1:8768/asr"     # RK3588 端侧 FunASR
ASR_SAMPLE_RATE = 16000                          # KWS/ASR 都要求 16k

# 退出/回待命指令词（cmd_voice_chat 同款）
HANGUP_PHRASES = ["退出", "结束对话", "结束通话", "停止对话", "挂断", "再见", "先这样"]


# ================================================================
# 8010 后端客户端（主链路）：hello → user_text → 收回答 + tts_pcm 播放
# ================================================================


if ckyw(text):
    await query(runtime, ws, state, text)


    async def query(text: str) -> bool:
        result = await mcp_clinet.call_tool("get_temprature", text)

if temp is None:
    reply = "温度传感器暂时没有读到有效数据，请确认红外探头是否对准锅面。"
elif:
    reply = (
        f"当前锅面最高温度约{temp:.0f}℃，距离{target}℃的目标温度还差约{target - temp:.0f}℃，如果需要的话我可以帮你盯着油温，需要的话说一声。")

await ywbb(runtime, ws, state, reply)
return True


async def _monitor_loop(runtime, ws, state):
    while True:
        await asyncio.sleep(1)

        if state.oil_watch is None or state.interrupted:
            return

        temp = get_valid_oil_temp()
        await_announce(ws, state, f"油温到了{约temp:.0f}℃，可以下食材了")


class BackendClient:
    def __init__(self):
        self.ws = None
        self.assistant_text: str | None = None
        self.assistant_event = asyncio.Event()
        self.pcm_chunks: list[bytes] = []
        self.tts_end_event = asyncio.Event()
        self.play_task: asyncio.Task | None = None
        self.muted = False               # TTS 播报期间静音麦克风，防回声自我识别

    async def connect(self):
        import websockets
        self.ws = await websockets.connect(f"{BACKEND_WS}?session_id={SESSION_ID}", open_timeout=10)
        await self.ws.send(json.dumps({"type": "hello", "payload": {
            "tts_provider": "xiaomi",    # TTS 与主项目同步调用 xiaomi mimo
            "stt_provider": "siliconflow",
        }}))
        asyncio.create_task(self.recv_loop())


#==============================================================================#
   if ckyw(text):
       await query(runtime,ws,state,text)
       async def query(text: str) -> bool:
           result = await mcp_clinet.call_tool("get_temprature", text)

   if temp is None:
       reply="温度传感器没有读到有效数据，请确认红外测温探头是否对准锅面"

    elif
        reply={f"当前锅面最高温度约{temp:.0f}，距离{target}℃的目标还差约{targe-temp:.0f}℃，"
               f"如果需要的话我还可以帮你盯着温度，需要的话说一声"}
        await ywbb(runtime, ws, state, reply)
        return True
    async def _monitor_loop(runtime, ws, state):
        while True:
            await asyncio.sleep(1)

            if state.oil_watch is None or state.interrupted:
                return
            temp = get_valid_oil_temp()
            await _announce(ws,state,f"油温到了约{temp:.0f}℃，可以下食材了！")

    
































#==============================================================================#
    async def send_user_text(self, text: str):
        """正文进主链路：后端【红】拦截 →【黄】真实红外 →【蓝】意图回答 →【紫】xiaomi TTS"""
        self.assistant_text, self.assistant_event = None, asyncio.Event()
        self.pcm_chunks, self.tts_end_event = [], asyncio.Event()
        await self.ws.send(json.dumps({"type": "user_text", "payload": {"text": text}}))

    async def wait_reply(self, timeout: float = 40.0) -> str | None:
        """等回答正文 + TTS 下发，并等播放完成"""
        try:
            await asyncio.wait_for(self.assistant_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print("  （等待回答超时）")
            return None
        try:
            await asyncio.wait_for(self.tts_end_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        if self.play_task:
            try:
                await asyncio.wait_for(self.play_task, timeout=120)
            except asyncio.TimeoutError:
                pass
        return self.assistant_text

    async def recv_loop(self):
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            typ, payload = msg.get("type"), msg.get("payload") or {}

            if typ == "assistant_delta":
                print(payload.get("delta") or "", end="", flush=True)
            elif typ == "assistant_final":
                self.assistant_text = str(payload.get("text") or "")
                self.assistant_event.set()
            elif typ == "tts_pcm":
                self.pcm_chunks.append(base64.b64decode(payload.get("pcm_b64") or ""))
            elif typ == "tts_pcm_end":
                self.tts_end_event.set()
                self.play_task = asyncio.create_task(self.play_pending_tts())

    async def play_pending_tts(self):
        """xiaomi mimo 合成的 24k PCM16 → sounddevice 播放（播报期间静音麦克风）"""
        chunks, self.pcm_chunks = self.pcm_chunks, []
        if not chunks:
            return
        pcm = b"".join(chunks)
        audio = np.frombuffer(pcm, dtype=np.int16)
        self.muted = True
        print("\n  🔊 [xiaomi TTS 播报中...]")

        def _play():
            import sounddevice as sd
            sd.play(audio, samplerate=24000, channels=1)
            sd.wait()
        try:
            await asyncio.to_thread(_play)
        finally:
            self.muted = False


# ================================================================
# 语音前端：KWS(8767) 唤醒 + ASR(8768) 识别（cmd_voice_chat.py 同款架构）
# ================================================================

class VoiceFrontend:
    """麦克风常开一个流：待命(idle)推 KWS，唤醒(listen)推 ASR；TTS 播报时静音丢弃"""

    def __init__(self, kws_url: str, asr_url: str, backend: BackendClient, input_device=None):
        self.kws_url = kws_url
        self.asr_url = asr_url
        self.backend = backend
        self.input_device = input_device
        self.mode = "idle"               # idle=等待唤醒词；listen=对话中
        self.lock = threading.Lock()
        self.kws_queue: asyncio.Queue = asyncio.Queue()
        self.asr_queue: asyncio.Queue = asyncio.Queue()
        self.asr_task: asyncio.Task | None = None
        self.busy = False                # 一轮对话处理中（不再重复触发）

    def set_mode(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    # ---- KWS：待命时持续推裸帧，命中推 {"event":"wakeword"} ----

    async def kws_loop(self):
        import websockets
        while True:
            try:
                async with websockets.connect(self.kws_url, max_size=None, ping_interval=20) as ws:
                    print("  ✓ KWS 已连接，待命中…（说「小云小云」唤醒）")
                    send = asyncio.create_task(self._pump(self.kws_queue, ws))
                    recv = asyncio.create_task(self._kws_recv(ws))
                    done, pending = await asyncio.wait([send, recv], return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    print("  KWS 断开，1s 后重连…")
                    await asyncio.sleep(1)
            except Exception as exc:
                print(f"  ⚠ KWS 连接失败: {exc}")
                print("    （端侧 8767 未启动？可 --kws-url 指定地址，或 Ctrl+C 后用 --text 模式兜底）")
                await asyncio.sleep(3)

    async def _kws_recv(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("event") == "wakeword":
                keyword = msg.get("keyword", "")
                print(f"\n🔔 命中唤醒词: {keyword or WAKE_WORD}")
                asyncio.ensure_future(self.on_wake())

    # ---- ASR：唤醒后推流，FunASR 出句回 {"type":"asr","isPartial":false,"text":..} ----

    async def asr_loop(self):
        import websockets
        while self.mode == "listen":
            try:
                async with websockets.connect(self.asr_url, max_size=None, ping_interval=20) as ws:
                    print("  ✓ ASR 已连接，请说出你的问题…")
                    send = asyncio.create_task(self._pump(self.asr_queue, ws))
                    recv = asyncio.create_task(self._asr_recv(ws))
                    done, pending = await asyncio.wait([send, recv], return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    if self.mode != "listen":
                        break
                    print("  ASR 断开，重连中…")
                    await asyncio.sleep(1)
            except Exception as exc:
                print(f"  ⚠ ASR 连接失败: {exc}")
                await asyncio.sleep(2)

    async def _asr_recv(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "asr" and not msg.get("isPartial"):
                text = (msg.get("text") or "").strip()
                if text:
                    asyncio.ensure_future(self.on_utterance(text))

    async def on_wake(self):
        if self.mode == "listen" or self.busy:
            return
        self.set_mode(mode="listen")
        print("  🔔 唤醒！我在听…（说「退出」回到待命）")
        if self.asr_task and not self.asr_task.done():
            self.asr_task.cancel()
        self.asr_task = asyncio.create_task(self.asr_loop())

    async def on_utterance(self, text: str):
        """ASR 出句：退出词回待命；否则正文发后端 8010 主链路"""
        if self.busy:
            return
        self.busy = True
        try:
            print(f"\n  🗣 你: {text}")
            if any(p in text for p in HANGUP_PHRASES):
                print("  👋 回到待命（说唤醒词重新开始）")
                self.set_mode(mode="idle")
                self.asr_task and self.asr_task.cancel()
                return
            await self.backend.send_user_text(text)      # 正文进主链路
            reply = await self.backend.wait_reply()
            if reply:
                print(f"\n  💬 回答：{reply[:60]}{'...' if len(reply) > 60 else ''}")
            print("  👂 继续听…（可直接继续说；说「退出」回待命）")
        finally:
            self.busy = False

    @staticmethod
    async def _pump(queue: asyncio.Queue, ws):
        while True:
            data = await queue.get()
            try:
                await ws.send(data)
            except Exception:
                return

    # ---- 麦克风常开：idle 推 KWS，listen 推 ASR，muted 丢弃 ----

    def start_microphone(self):
        import sounddevice as sd
        loop = asyncio.get_event_loop()
        resampler = StreamResampler(ASR_SAMPLE_RATE, ASR_SAMPLE_RATE)  # 实际采样率开流后修正

        def callback(indata, frames, time_info, status):
            if self.backend.muted:
                return
            pcm = indata[:, 0] if indata.ndim > 1 else indata
            if resampler.src_sr != ASR_SAMPLE_RATE:
                pcm = resampler.process(pcm)
                if pcm.size == 0:
                    return
            data = pcm.astype(np.int16).tobytes()
            if self.mode == "listen":
                loop.call_soon_threadsafe(self.asr_queue.put_nowait, data)
            elif self.mode == "idle":
                loop.call_soon_threadsafe(self.kws_queue.put_nowait, data)

        stream = sd.InputStream(device=self.input_device, channels=1,
                                samplerate=ASR_SAMPLE_RATE, dtype="int16",
                                blocksize=1024, callback=callback)
        stream.start()
        actual_sr = stream.samplerate
        if actual_sr != ASR_SAMPLE_RATE:
            # 设备不支持 16k：PortAuto 以原生率开流，回调里重采样到 16k（KWS/ASR 要求）
            resampler.src_sr = actual_sr
            print(f"  ⚠ 设备采样率 {actual_sr}Hz → 自动重采样到 16k")
        print(f"  ✓ 麦克风已开启（{actual_sr}Hz）")
        return stream


# ---------- 文本兜底模式（--text）：input 代替语音，对话链路完全相同 ----------

async def text_mode(backend: BackendClient):
    print(f"\n文本模式：输入需以「{WAKE_WORD}」开头（q 退出）。对话链路与语音模式完全相同。\n")
    while True:
        try:
            raw = input(f"（{WAKE_WORD} + 问题）> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if raw.lower() in ("q", "quit", "exit", "退出"):
            break
        t = raw.strip().strip("，,。 ：:")
        if not t.startswith(WAKE_WORD):
            print(f"  （未检测到唤醒词「{WAKE_WORD}」）")
            continue
        body = t[len(WAKE_WORD):].strip("，,。 ：:")
        if not body:
            print("  （唤醒后内容为空）")
            continue
        await backend.send_user_text(body)
        reply = await backend.wait_reply()
        if reply:
            print(f"\n  💬 回答：{reply[:60]}{'...' if len(reply) > 60 else ''}")


# ---------- 跨帧重采样（设备原生采样率 ≠ 16k 时自动修正，cmd_voice_chat 同款） ----------

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

def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="油温问答与盯守：KWS 唤醒 + 端侧 ASR + 后端主链路 + xiaomi TTS")
    p.add_argument("--kws-url", default=DEFAULT_KWS_URL)
    p.add_argument("--asr-url", default=DEFAULT_ASR_URL)
    p.add_argument("--input-device", type=str, default=None, help="麦克风设备索引或名称")
    p.add_argument("--text", action="store_true", help="文本模式（input 代替语音，链路相同）")
    return p.parse_args()


def _resolve_device(name_or_idx: str):
    try:
        import sounddevice as sd
        return int(name_or_idx)
    except ValueError:
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if name_or_idx.lower() in (d["name"] or "").lower():
                    return i
        except Exception:
            pass
    return None


async def async_main(args):
    backend = BackendClient()
    try:
        await backend.connect()
        print(f"  ✔ 已连后端 8010（session={SESSION_ID}，TTS=xiaomi mimo）")
    except Exception as exc:
        print(f"⚠ 无法连接后端：{exc}\n  请先启动后端（8010）再运行。")
        return

    if TEXT_MODE:
        await text_mode(backend)
        await backend.ws.close()
        return

    frontend = VoiceFrontend(
        args.kws_url, args.asr_url, backend,
        input_device=_resolve_device(args.input_device) if args.input_device else None,
    )
    print(f"  唤醒词 KWS: {args.kws_url}")
    print(f"  端侧 ASR : {args.asr_url}")
    asyncio.create_task(frontend.kws_loop())

    stream = None
    try:
        stream = frontend.start_microphone()
        print(f"\n  🎙 说「{WAKE_WORD}」唤醒 → 直接提问（例：我想炸软炸里脊，现在能下锅吗）")
        print("  说「退出」回到待命 · Ctrl+C 退出\n")
        while True:
            await asyncio.sleep(0.2)
    except Exception as exc:
        print(f"[错误] 无法打开麦克风: {exc}\n  可 Ctrl+C 后加 --text 用文本模式兜底。")
    finally:
        if stream:
            stream.stop()
            stream.close()


def main():
    args = parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
