"""
智能厨房 AI 助手 - 命令行交互 Demo
====================================
演示完整的前后端交互流程：
  WebSocket 消息 → 状态机流转 → 菜谱匹配 → 步骤引导 → 游标推进
  文本清理 → TTS 切分 → 流式 TTS 中继 → PCM 推送
  STT 语音识别 → 文字注入

运行: python demo_cooking.py  （在 演示/模块1/ 目录下）

不需要启动前端、不需要 API Key，纯本地演示。
"""
import sys
import os
import re
import json
import time
import random

# 输出保护: 管道/重定向时 Windows 默认 GBK 编码打不出 emoji，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 让项目模块能被导入：从脚本所在目录逐层向上，找到含 backend/ 的项目根目录
_dir = os.path.dirname(os.path.abspath(__file__))
while _dir != os.path.dirname(_dir):                 # 没到盘符根就继续往上
    if os.path.isdir(os.path.join(_dir, "backend")):
        break                                        # 找到 backend → 这就是项目根
    _dir = os.path.dirname(_dir)
sys.path.insert(0, os.path.join(_dir, "backend"))

from app.session import SessionState
from app.cooking_core import (
    COOKING_START_KEYWORDS, COOKING_CONFIRM_KEYWORDS, COOKING_EXIT_KEYWORDS,
    COOKING_SKIP_KEYWORDS, COOKING_START_GUIDE_KEYWORDS,
    recipe_to_dict, build_step_guidance_text, parse_servings_from_text,
    _reset_cooking_session_state,
    # 份量智能计算（调料收敛 + 主料线性放大）
    build_servings_confirmation_text, smart_calc_source_lines,
    parse_original_servings_from_recipe,
    _multiply_amount_in_line, _seasoning_converged_multiplier, _SEASONING_KEYWORDS,
)
from app.services.recipe_service import get_recipe_service

# 关闭项目日志的终端刷屏（[LOG]/[LOGGER] 行），文件日志 logs/*.md 照常写入
try:
    import app.logger as _applog
    _applog.safe_print = lambda *a, **k: None
except Exception:
    pass

# ── 文本/语音转换管道 ──
# 优先导入项目真实函数；若环境缺 fastapi（app.ws 包的依赖）则自动降级为本地简化实现
try:
    from app.ws.tts_handler import (
        clean_text_for_tts,           # 清理 markdown/HTML → TTS 友好纯文本
        _should_flush_tts_buffer,     # 判断流式缓冲是否该断句送 TTS
    )
    from app.providers.xiaomi_tts import (
        _split_text_for_tts,          # 按句号/逗号切分 → 适合 TTS 的短句
    )
    from app.ws import _split_text_for_streaming  # 按句末标点切 LLM 流式块
    TTS_FROM_PROJECT = True
except ImportError:
    # 典型场景: ModuleNotFoundError: No module named 'fastapi'
    TTS_FROM_PROJECT = False

    def clean_text_for_tts(text):
        """简化版: 去 markdown 标记 / 代码块 / 链接 / HTML 标签"""
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
        text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    def _split_text_for_tts(text, max_len=30):
        """简化版: 按句末标点/换行切；超长段再按逗号切"""
        segs = []
        for part in re.split(r"(?<=[。！？；\n])", text):
            part = part.strip()
            if not part:
                continue
            if len(part) <= max_len:
                segs.append(part)
                continue
            buf = ""
            for sub in re.split(r"(?<=[，、,：:])", part):
                if buf and len(buf) + len(sub) > max_len:
                    segs.append(buf)
                    buf = sub
                else:
                    buf += sub
            if buf:
                segs.append(buf)
        return segs

    def _should_flush_tts_buffer(buffer, _state=None):
        """简化版: 末尾是标点或缓冲过长就 flush"""
        if not buffer:
            return False
        return buffer[-1] in "。！？；，、,;:：\n~" or len(buffer) >= 60

    def _split_text_for_streaming(text):
        """简化版: 按子句切，保留标点"""
        return [s for s in re.split(r"(?<=[。！？；，、,~\n])", text) if s]

# ============================================================
# 颜色工具（让终端输出好看点）
# ============================================================
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    MAGENTA= "\033[95m"
    CYAN   = "\033[96m"

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def print_ws(event, payload, direction="←→"):
    """WebSocket 消息可视化（已关闭：演示只保留状态库面板 + AI 回复）"""
    pass

def print_state(state, label="当前状态"):
    """打印状态库快照"""
    print(f"\n{C.BOLD}{C.YELLOW}╔═══════════════════════ {label} ════════════════════╗{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} cooking_enabled  = {C.GREEN if state.cooking_enabled else C.RED}{state.cooking_enabled}{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} cooking_state    = {C.CYAN}{state.cooking_state:20s}{C.RESET}")
    if state.current_recipe:
        steps = state.current_recipe.get("steps", [])
        print(f"{C.YELLOW}║{C.RESET} current_recipe   = {C.GREEN}{state.current_recipe['name']}{C.RESET} ({len(steps)} 步)")
        print(f"{C.YELLOW}║{C.RESET} current_step     = {C.CYAN}{state.current_step}{C.RESET}  (共 {len(steps)} 步)")
        print(f"{C.YELLOW}║{C.RESET} completed_steps  = {C.GREEN}{state.completed_steps}{C.RESET}")
    else:
        print(f"{C.YELLOW}║{C.RESET} current_recipe   = {C.RED}None{C.RESET}")
        print(f"{C.YELLOW}║{C.RESET} current_step     = {C.RED}0{C.RESET}")
        print(f"{C.YELLOW}║{C.RESET} completed_steps  = {C.RED}[]{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} awaiting_servings= {C.YELLOW}{state.awaiting_servings}{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} current_servings = {C.CYAN}{state.current_servings}{C.RESET}")
    print(f"{C.YELLOW}╚══════════════════════════════════════════════════╝{C.RESET}\n")

def print_ai(text):
    print(f"\n{C.BOLD}{C.BLUE}🤖 AI 助手说:{C.RESET} {text}\n")
    # 真实语音模式: 调真实 TTS 播放；否则跑管道可视化
    if audio_mode.real and REAL_AUDIO_AVAILABLE:
        real_speak(text)
    else:
        print_tts_pipeline(text)

def print_user(text):
    print(f"{C.BOLD}{C.GREEN}👤 你说:{C.RESET} {text}")


# ============================================================
# 🗣️ 文本/语音转换管道可视化
# ============================================================

class AudioMode:
    """音频模式：browser(浏览器TTS) / xiaomi / dashscope / siliconflow / glasses(眼镜)"""
    def __init__(self):
        self.tts_provider = "xiaomi"       # 当前 TTS 提供商
        self.tts_voice = "Xiao Yan"        # 当前 TTS 音色
        self.stt_provider = "siliconflow"  # 当前 STT 提供商
        self.audio_source = "local"        # local / glasses
        self.enabled = True                # 是否开启语音管道可视化
        self.real = True                   # 真实语音模式默认开启: AI 回复全部真声播报（输入 real 可关闭）
        self.voice_default = True          # 默认语音输入: 直接回车=录音（真实语音可用时）
        self.debug = False                 # 显示完整管道逐块细节（默认只显示一行摘要）
        self.output_device = None          # 扬声器设备索引（None=系统默认；spk 命令查看/切换）

    def toggle(self):
        self.enabled = not self.enabled
        return self.enabled

    def cycle_provider(self):
        providers = ["xiaomi", "dashscope", "siliconflow", "local_cosyvoice", "browser"]
        idx = providers.index(self.tts_provider) if self.tts_provider in providers else 0
        self.tts_provider = providers[(idx + 1) % len(providers)]
        return self.tts_provider

    def cycle_source(self):
        self.audio_source = "glasses" if self.audio_source == "local" else "local"
        return self.audio_source

    def status_str(self):
        src = "眼镜" if self.audio_source == "glasses" else "本地扬声器"
        mode = "🔴真实语音" if self.real else "📝模拟"
        vdef = " | 回车=语音输入" if (self.voice_default and REAL_AUDIO_AVAILABLE) else ""
        return f"{mode} | TTS={self.tts_provider}/{self.tts_voice} | STT={self.stt_provider} | 播放={src}{vdef}"


audio_mode = AudioMode()


def _fmt_bytes(n):
    """把字节数变成人类可读"""
    if n < 1024: return f"{n} B"
    if n < 1024 * 1024: return f"{n/1024:.1f} KB"
    return f"{n/(1024*1024):.1f} MB"


def print_tts_pipeline(text: str, provider: str | None = None, voice: str | None = None):
    """TTS 管道摘要（一行）；输入 debug 显示完整逐块细节"""
    if not audio_mode.enabled:
        return
    provider = provider or audio_mode.tts_provider

    cleaned = clean_text_for_tts(text)
    segments = _split_text_for_tts(cleaned)
    n_chunks = sum(max(1, len(s) // 20) for s in segments)
    kb = n_chunks * 24000 * 2 // 25
    src = " → 转发眼镜" if audio_mode.audio_source == "glasses" else ""

    if audio_mode.debug:
        _print_tts_pipeline_full(text, provider, voice)
        return

    print(f"{C.DIM}🗣️ TTS[{provider}] 清理{len(text)}→{len(cleaned)}字 · 切{len(segments)}段 · "
          f"{n_chunks}块PCM {_fmt_bytes(kb)} @24kHz{src}{C.RESET}")


def _print_tts_pipeline_full(text: str, provider: str | None = None, voice: str | None = None):
    """完整 TTS 管道可视化：清理 → 切分 → 逐段合成 → PCM chunk → 推送"""
    if not audio_mode.enabled:
        return
    provider = provider or audio_mode.tts_provider
    voice = voice or audio_mode.tts_voice

    print(f"\n{C.BOLD}{C.CYAN}╔══ 🗣️  TTS 合成管道 ════════════════════════════════════════╗{C.RESET}")

    # ── Step 1: 文本清理 ──
    print(f"{C.CYAN}║{C.RESET} ① 原始文本")
    print(f"{C.DIM}║{C.RESET}    {text[:100]}{'...' if len(text) > 100 else ''}")

    cleaned = clean_text_for_tts(text)
    print(f"{C.CYAN}║{C.RESET} ② clean_text_for_tts() → {C.GREEN}清理后{C.RESET} ({len(text)}→{len(cleaned)} 字符)")
    for line in cleaned.split("\n"):
        print(f"{C.DIM}║{C.RESET}    {line}")

    # ── Step 2: 文本切分 ──
    segments = _split_text_for_tts(cleaned)
    print(f"{C.CYAN}║{C.RESET} ③ _split_text_for_tts() → {C.GREEN}{len(segments)} 段{C.RESET}")
    for i, seg in enumerate(segments, 1):
        print(f"{C.DIM}║{C.RESET}    [{i}] ({len(seg):2d}字) {seg}")

    # ── Step 3: 流式管道 relay ──
    streaming_chunks = _split_text_for_streaming(cleaned)
    print(f"{C.CYAN}║{C.RESET} ④ 流式管道(LLM逐字吐+标点断句) → {C.GREEN}{len(streaming_chunks)} 个 flush 块{C.RESET}")
    buf = ""
    for i, chunk in enumerate(streaming_chunks, 1):
        buf += chunk
        should_flush = _should_flush_tts_buffer(buf, None)
        tag = f"{C.GREEN}FLUSH{C.RESET}" if should_flush else f"{C.YELLOW}HOLD{C.RESET}"
        print(f"{C.DIM}║{C.RESET}    [{i}] {tag}  buffer={buf[:40]}{'...' if len(buf) > 40 else ''}")

    # ── Step 4: 模拟合成 + PCM 推送 ──
    print(f"{C.CYAN}║{C.RESET} ⑤ 调用 {provider} TTS (voice={voice})...")
    total_pcm_chunks = 0
    total_pcm_bytes = 0
    for i, seg in enumerate(segments, 1):
        # 模拟：每 20 个字产生 1 个 24000Hz 16bit mono PCM 块
        chars_per_chunk = 20
        chunk_count = max(1, len(seg) // chars_per_chunk)
        # 24000 * 2 bytes * ~0.04s ≈ 每个 chunk ~2KB
        pcm_per_chunk = 24000 * 2 // 25  # ~1.92KB
        seg_pcm_bytes = chunk_count * pcm_per_chunk
        total_pcm_chunks += chunk_count
        total_pcm_bytes += seg_pcm_bytes
        for ci in range(chunk_count):
            print(f"{C.DIM}║{C.RESET}    WS→前端 tts_pcm  segment={i}/{len(segments)} chunk={ci+1}/{chunk_count} pcm_b64={_fmt_bytes(pcm_per_chunk)}")

    end_payload = {"total_chunks": total_pcm_chunks, "sample_rate": 24000}
    print(f"{C.CYAN}║{C.RESET} ⑥ WS→前端 {C.MAGENTA}tts_pcm_end{C.RESET} total_chunks={total_pcm_chunks} sample_rate=24000 total={_fmt_bytes(total_pcm_bytes)}")

    if audio_mode.audio_source == "glasses":
        print(f"{C.CYAN}║{C.RESET}    └→ 同步转发到眼镜音频 WebSocket")

    print(f"{C.CYAN}╚══════════════════════════════════════════════════════════╝{C.RESET}\n")


def print_streaming_tts_demo(text: str, provider: str | None = None):
    """模拟 LLM 流式输出 + 并行 TTS 的管道"""
    if not audio_mode.enabled:
        return
    provider = provider or audio_mode.tts_provider
    print(f"\n{C.BOLD}{C.MAGENTA}≡≡≡ 流式 TTS Relay 演示 (LLM delta → 边吐边播) ≡≡≡{C.RESET}")

    cleaned = clean_text_for_tts(text)
    print(f"  {C.DIM}原始 → cleaned: {C.RESET}{cleaned[:80]}{'...' if len(cleaned) > 80 else ''}")

    # 模拟 LLM 一个字一个字吐出来
    buf = ""
    total_flushed = 0
    for ch in cleaned:
        buf += ch
        flush = _should_flush_tts_buffer(buf, None)
        if flush:
            # 模拟 flush：送去 TTS，生成 PCM 块
            pcm_size = len(buf) * 200  # 粗略估
            print(f"  {C.GREEN}▶ FLUSH{C.RESET} buffer={buf[:30]}... → {provider}.synthesize({len(buf)}字) → PCM {_fmt_bytes(pcm_size)} → tts_pcm WS")
            total_flushed += 1
            buf = ""
        else:
            print(f"  {C.DIM}· HOLD{C.RESET} +={ch!r}  buffer={buf[:30]}...")

    # 最后残留的 buffer 也要 flush
    if buf.strip():
        pcm_size = len(buf) * 200
        print(f"  {C.GREEN}▶ FINAL FLUSH{C.RESET} buffer={buf[:30]}... → {provider}.synthesize() → PCM {_fmt_bytes(pcm_size)}")
        total_flushed += 1

    print(f"  {C.MAGENTA}✓ 完成！累计 {total_flushed} 次 flush{C.RESET}\n")


def print_stt_pipeline(mock_text: str):
    """STT 管道摘要（一行）；输入 debug 显示完整逐块细节"""
    if not audio_mode.enabled:
        return
    if audio_mode.debug:
        _print_stt_pipeline_full(mock_text)
        return
    dur = len(mock_text) / 4  # 粗略估 1秒语音≈4字
    print(f"{C.DIM}🎙️ STT[{audio_mode.stt_provider}] ≈{dur:.1f}s音频 → 识别'{mock_text}' → stt_final{C.RESET}")


def _print_stt_pipeline_full(mock_text: str):
    """模拟 STT 语音识别管道：PCM chunk → 累计 → 识别"""
    if not audio_mode.enabled:
        return
    provider = audio_mode.stt_provider
    print(f"\n{C.BOLD}{C.YELLOW}╔══ 🎙️  STT 语音识别管道 ════════════════════════════════════╗{C.RESET}")

    # 模拟前端发来 PCM chunk
    fake_pcm_bytes = len(mock_text) * 24000 * 2 // 3  # 粗略估 1秒语音≈4字
    chunk_size = 3200  # ~0.1s @ 16kHz 16bit
    total_chunks = max(1, fake_pcm_bytes // chunk_size)
    accumulated = 0

    print(f"{C.YELLOW}║{C.RESET} ① 前端 {C.MAGENTA}audio_chunk{C.RESET} 进来，共 {total_chunks} 个 PCM 块")
    for i in range(total_chunks):
        accumulated += chunk_size
        partial = ""
        if random.random() < 0.3 and i > 0 and i < total_chunks - 1:
            partial = f"  → {C.DIM}stt_partial{C.RESET} 识别中间结果: '我想做番'"
        print(f"{C.DIM}║{C.RESET}    chunk={i+1}/{total_chunks} pcm_b64={_fmt_bytes(chunk_size)} accumulated={_fmt_bytes(accumulated)}{partial}")

    print(f"{C.YELLOW}║{C.RESET} ② 前端 {C.MAGENTA}audio_end{C.RESET} → 累计 PCM {_fmt_bytes(accumulated)}")
    print(f"{C.YELLOW}║{C.RESET} ③ 调 {provider}.transcribe_pcm16(sample_rate=16000)...")
    print(f"{C.YELLOW}║{C.RESET} ④ {provider} 返回 text = {C.GREEN}'{mock_text}'{C.RESET}")
    print(f"{C.YELLOW}║{C.RESET} ⑤ WS→后端 {C.MAGENTA}stt_final{C.RESET} {{text: '{mock_text}'}}")
    print(f"{C.YELLOW}╚═══════════════════════════════════════════════════════════╝{C.RESET}\n")


# ============================================================
# 🎙️ 真实语音：麦克风录音 + 扬声器播放（需要 fastapi 环境 + sounddevice）
# ============================================================

try:
    import sounddevice as _sd
    import numpy as _np
    import threading
    import asyncio
    import concurrent.futures
    import io as _io
    import wave as _wave
    SD_OK = True
except ImportError:
    SD_OK = False

# 真实语音可用 = sounddevice 已装 且 TTS/STT provider 可独立导入
# （与 TTS_FROM_PROJECT 解耦：app.ws 缺依赖只影响管道可视化，不影响真声播放）
try:
    from app.runtime_providers import build_tts_runtime, build_stt_runtime  # noqa: F401
    _PROVIDERS_OK = True
except ImportError:
    _PROVIDERS_OK = False

REAL_AUDIO_AVAILABLE = SD_OK and _PROVIDERS_OK

_audio_loop = None          # 持久事件循环（httpx AsyncClient 不能跨 loop 复用）
_real_stt_map = {}          # STT 模型 -> 实例缓存
_real_tts_map = {}          # provider 名 -> TTS 实例缓存


def _ensure_audio_loop():
    """后台线程跑一个持久 asyncio loop，所有 async provider 调用都扔给它"""
    global _audio_loop
    if _audio_loop is None or _audio_loop.is_closed():
        _audio_loop = asyncio.new_event_loop()
        t = threading.Thread(target=_audio_loop.run_forever, daemon=True)
        t.start()
    return _audio_loop


def run_async(coro, timeout=180):
    """在持久 loop 上执行协程并同步等待结果"""
    loop = _ensure_audio_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def get_real_stt(model: str | None = None):
    """真实 STT: SiliconFlow（Key 从 .env.local 自动加载），按模型缓存"""
    from app.config import settings
    key = model or settings.siliconflow_stt_model
    if key not in _real_stt_map:
        from app.runtime_providers import build_stt_runtime
        _real_stt_map[key] = build_stt_runtime(
            "siliconflow",
            model=key,
        )
    return _real_stt_map[key]


def get_real_tts(provider=None):
    """真实 TTS: 按当前 provider 构建并缓存"""
    provider = provider or audio_mode.tts_provider
    if provider not in _real_tts_map:
        from app.runtime_providers import build_tts_runtime
        _real_tts_map[provider] = build_tts_runtime(provider)
    return _real_tts_map[provider]


def _decode_wav_to_pcm(wav_bytes):
    """WAV bytes → (numpy int16 数组, 采样率, 声道数)。失败返回 None"""
    try:
        with _wave.open(_io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
        audio = _np.frombuffer(frames, dtype=_np.int16)
        if ch > 1:
            audio = audio.reshape(-1, ch)
        return audio, sr, ch
    except Exception:
        return None


def real_speak(text: str, provider: str | None = None):
    """真实 TTS: 合成 → 解码 → sounddevice 播放（阻塞直到播完）
    播放期间静音端侧麦克风，防止 AI 自己的声音被识别（同 cmd_voice_chat.muted）"""
    if not REAL_AUDIO_AVAILABLE:
        print_tts_pipeline(text)
        return
    provider = provider or audio_mode.tts_provider
    edge.set_muted(True)  # 防回声
    try:
        tts = get_real_tts(provider)
        if tts is None:
            print(f"{C.DIM}({provider} TTS 不可用，走模拟){C.RESET}")
            print_tts_pipeline(text)
            return
        print(f"{C.DIM}🔊 [{provider} TTS] 合成中...{C.RESET}")
        wav = run_async(tts.synthesize(text))
        if not wav:
            print(f"{C.DIM}(TTS 返回空音频，走模拟){C.RESET}")
            print_tts_pipeline(text)
            return
        decoded = _decode_wav_to_pcm(wav)
        if decoded is None:
            print(f"{C.DIM}(音频格式非 WAV/PCM，无法本地播放: {len(wav)} bytes，走模拟){C.RESET}")
            print_tts_pipeline(text)
            return
        audio, sr, ch = decoded
        dur = len(audio) / sr
        _sd.play(audio, samplerate=sr, device=audio_mode.output_device)
        _sd.wait()
        print(f"{C.DIM}🔊 播放完毕 {dur:.1f}s ({sr}Hz){C.RESET}")
    except Exception as exc:
        print(f"{C.RED}真实 TTS 失败: {exc}{C.RESET} {C.DIM}(走模拟){C.RESET}")
        print_tts_pipeline(text)
    finally:
        edge.set_muted(False)


def real_record_and_transcribe() -> str | None:
    """真实 STT: 回车开始录音 → 回车结束 → SiliconFlow 识别 → 返回文字
    503/网络错误自动重试，主模型不行自动切备用模型"""
    if not REAL_AUDIO_AVAILABLE:
        print(f"{C.RED}真实语音不可用（需 sounddevice + app.runtime_providers 可导入）{C.RESET}")
        return None
    from app.config import settings

    chunks = []

    def _cb(indata, frames, time_info, status):
        chunks.append(indata.copy())

    print(f"{C.YELLOW}🎙️  请开始说话（说完按回车结束录音）...{C.RESET}")
    try:
        with _sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                             blocksize=1600, callback=_cb):
            input()  # 主线程等回车，callback 线程持续录音
    except Exception as exc:
        print(f"{C.RED}录音失败: {exc}{C.RESET}")
        return None

    pcm = b"".join(c.tobytes() for c in chunks)
    duration = len(pcm) / 32000  # 16000Hz * 2bytes
    if duration < 0.3:
        print(f"{C.DIM}(录音太短，忽略){C.RESET}")
        return None

    print(f"{C.DIM}🧠 识别中... 录音 {duration:.1f}s / {len(pcm)} bytes{C.RESET}")

    # 识别链: 主模型重试2次 → 备用模型重试1次
    chain = [
        (settings.siliconflow_stt_model, 2),   # SenseVoiceSmall，503时重试
        ("TeleAI/TeleSpeechASR", 1),           # 备用模型
    ]
    last_err = None
    for model, retries in chain:
        stt = get_real_stt(model)
        for attempt in range(1, retries + 1):
            tag = f"{model}" + (f" (第{attempt}次)" if retries > 1 else "")
            try:
                text = run_async(stt.transcribe_pcm16(pcm, 16000))
                text = (text or "").strip()
                print(f"{C.DIM}🧠 [{tag}] 识别结果: {text!r}{C.RESET}")
                if text:
                    return text
                # 识别为空不算失败，但换下一个链路也没意义，直接返回
                return None
            except Exception as exc:
                last_err = exc
                print(f"{C.YELLOW}⚠ {tag} 识别失败: {str(exc)[:80]}{C.RESET}")
                if attempt < retries:
                    time.sleep(1.5)  # 503 多为服务端瞬时过载，稍等重试

    print(f"{C.RED}STT 识别失败（所有模型都试过了）: {last_err}{C.RESET}")
    return None


# ============================================================
# 端侧语音模块（方案复刻 backend/cmd_voice_chat.py）
#   唤醒词 KWS: ws://<RK3588>:8767/kws  推16k PCM16 → {"event":"wakeword","keyword":..}
#   流式 ASR : ws://<RK3588>:8768/asr   推16k PCM16 → {"type":"asr","isPartial":false,"text":..}
#   TTS      : 小米 MIMO（与 cmd_voice_chat 同一服务，demo 走 provider 封装）
# ============================================================
EDGE_KWS_URL = os.environ.get("EDGE_KWS_URL", "ws://127.0.0.1:8767/kws")
EDGE_ASR_URL = os.environ.get("EDGE_ASR_URL", "ws://127.0.0.1:8768/asr")
EDGE_WAKE_WORDS = ["小云小云", "小云", "晓云", "小芸小芸"]
EDGE_HANGUP_PHRASES = ["退出", "挂断", "结束对话", "先这样"]  # 说了回待命（需重新唤醒）
ASR_SAMPLE_RATE = 16000

CURRENT_STATE = {"s": None}  # demo 主状态，供语音回调线程读取


class _StreamResampler:
    """设备原生采样率 ≠ 16k 时自动重采样（复刻 cmd_voice_chat.StreamResampler）"""
    def __init__(self, src_sr, dst_sr):
        self.src_sr, self.dst_sr = src_sr, dst_sr
        self._carry = None

    def process(self, chunk_int16):
        import numpy as np
        x = chunk_int16.astype(np.float32) / 32768.0
        if self._carry is not None and len(self._carry):
            x = np.concatenate([self._carry, x])
        if self.src_sr == self.dst_sr:
            self._carry = None
            return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
        n_dst = int(round(len(x) * self.dst_sr / self.src_sr))
        if n_dst == 0:
            self._carry = x
            return np.zeros(0, dtype=np.int16)
        idx = np.linspace(0, len(x) - 1, n_dst)
        y = np.interp(idx, np.arange(len(x)), x)
        used = int(round(n_dst * self.src_sr / self.dst_sr))
        self._carry = x[used:] if 0 < used < len(x) else None
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)


class EdgeVoice:
    """端侧语音：KWS 唤醒 + FunASR 流式识别 + 识别结果注入做饭流程"""

    def __init__(self):
        self.mode = "idle"       # idle=等待唤醒词 / listen=对话识别中
        self.enabled = False     # 端侧服务是否可达
        self.paused = False      # 手动暂停麦克风路由
        self.muted = False       # TTS 播放中静音，防回声
        self.kws_ok = False
        self.asr_ok = False
        self.loop = None
        self.kws_q = None
        self.asr_q = None
        self.resampler = _StreamResampler(ASR_SAMPLE_RATE, ASR_SAMPLE_RATE)

    # ---- 启动：探测端侧服务 → 起后台线程 ----
    async def _probe(self):
        import websockets
        async def _one(url):
            try:
                async with websockets.connect(url, max_size=None, ping_interval=20,
                                              open_timeout=3):
                    return True
            except Exception:
                return False
        return await _one(EDGE_KWS_URL), await _one(EDGE_ASR_URL)

    def start(self):
        try:
            import websockets  # noqa: F401
        except ImportError:
            print(f"{C.DIM}(缺 websockets 库 → 语音输入退回「回车录音+云端STT」){C.RESET}")
            return False
        kws_ok, asr_ok = run_async(self._probe())
        if not (kws_ok or asr_ok):
            print(f"{C.DIM}(端侧语音服务不可达 KWS={EDGE_KWS_URL} / ASR={EDGE_ASR_URL}){C.RESET}")
            print(f"{C.DIM}→ 语音输入退回「回车录音 + 云端STT」模式{C.RESET}")
            return False
        self.kws_ok, self.asr_ok = kws_ok, asr_ok
        self.enabled = True
        import threading
        import asyncio as _aio
        self.loop = _aio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()
        time.sleep(0.3)
        return True

    def _run(self):
        import asyncio as _aio
        _aio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        import asyncio as _aio
        self.kws_q, self.asr_q = _aio.Queue(), _aio.Queue()
        if self.kws_ok:
            _aio.create_task(self._kws_loop())
        if self.asr_ok:
            _aio.create_task(self._asr_loop())
        await self._mic_loop()

    # ---- KWS 唤醒词循环（复刻 cmd_voice_chat.kws_loop）----
    async def _kws_loop(self):
        import websockets
        import asyncio as _aio
        while True:
            try:
                async with websockets.connect(EDGE_KWS_URL, max_size=None, ping_interval=20) as ws:
                    print(f"{C.GREEN}  ✓ KWS 已连接（{EDGE_KWS_URL}）待命中…说「小云小云」唤醒{C.RESET}")
                    send = _aio.create_task(self._sender(ws, self.kws_q))
                    recv = _aio.create_task(self._kws_recv(ws))
                    done, pending = await _aio.wait([send, recv], return_when=_aio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    await _aio.sleep(1)
            except Exception as e:
                print(f"{C.DIM}  KWS 重连: {str(e)[:50]}{C.RESET}")
                await _aio.sleep(2)

    async def _kws_recv(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("event") == "wakeword" and self.mode == "idle":
                self.mode = "listen"
                kw = msg.get("keyword", "")
                print(f"\n{C.BOLD}{C.YELLOW}🔔 唤醒{'(' + kw + ')' if kw else ''}！我在听…{C.RESET}")

    # ---- ASR 识别循环（复刻 cmd_voice_chat.asr_loop，仅 listen 模式连接）----
    async def _asr_loop(self):
        import websockets
        import asyncio as _aio
        while True:
            if self.mode != "listen":
                await _aio.sleep(0.2)
                continue
            try:
                async with websockets.connect(EDGE_ASR_URL, max_size=None, ping_interval=20) as ws:
                    print(f"{C.DIM}  ✓ ASR 已连接（{EDGE_ASR_URL}）{C.RESET}")
                    send = _aio.create_task(self._sender(ws, self.asr_q))
                    recv = _aio.create_task(self._asr_recv(ws))
                    done, pending = await _aio.wait([send, recv], return_when=_aio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
            except Exception as e:
                print(f"{C.DIM}  ASR 重连: {str(e)[:50]}{C.RESET}")
                await _aio.sleep(1)

    async def _asr_recv(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "asr" and not msg.get("isPartial"):
                text = (msg.get("text") or "").strip()
                if text:
                    self.loop.run_in_executor(None, self._on_final, text)

    def _on_final(self, text):
        """识别到一句完整的话 → 注入做饭状态机（独立线程执行，不阻塞收流）"""
        if self.muted:
            return
        print()
        print_user(text)
        st = CURRENT_STATE.get("s")
        if st is not None:
            handle_user_input(st, text)
        # 退出类指令 → 回到待命（与 cmd_voice_chat 挂断逻辑一致）
        if any(p in text for p in EDGE_HANGUP_PHRASES):
            self.mode = "idle"
            print(f"{C.DIM}👋 回到待命（说「小云小云」重新唤醒）{C.RESET}")

    async def _sender(self, ws, q):
        import asyncio as _aio
        while True:
            data = await q.get()
            try:
                await ws.send(data)
            except Exception:
                pass

    # ---- 麦克风路由（复刻 cmd_voice_chat.audio_callback）----
    async def _mic_loop(self):
        import sounddevice as sd
        import numpy as np
        import asyncio as _aio

        def callback(indata, frames, t, status):
            if self.paused or self.muted:
                return
            pcm = indata[:, 0] if indata.ndim > 1 else indata
            if self.resampler.src_sr != ASR_SAMPLE_RATE:
                pcm = self.resampler.process(pcm)
                if pcm.size == 0:
                    return
            data = pcm.astype(np.int16).tobytes()
            if self.mode == "listen":
                self.loop.call_soon_threadsafe(self.asr_q.put_nowait, data)
            else:
                self.loop.call_soon_threadsafe(self.kws_q.put_nowait, data)

        try:
            stream = sd.InputStream(samplerate=ASR_SAMPLE_RATE, channels=1,
                                    dtype="int16", blocksize=1024, callback=callback)
            stream.start()
            self.resampler = _StreamResampler(ASR_SAMPLE_RATE, ASR_SAMPLE_RATE)
            print(f"{C.DIM}  ✓ 麦克风 16000Hz 已开启{C.RESET}")
        except Exception:
            stream = sd.InputStream(channels=1, dtype="int16", blocksize=1024, callback=callback)
            stream.start()
            self.resampler = _StreamResampler(int(stream.samplerate), ASR_SAMPLE_RATE)
            print(f"{C.DIM}  ✓ 麦克风 {stream.samplerate}Hz → 自动重采样 16k{C.RESET}")
        while True:
            await _aio.sleep(0.5)

    # ---- 外部控制 ----
    def wake(self):
        """手动唤醒（等价于说唤醒词）"""
        if self.enabled and self.mode == "idle":
            self.mode = "listen"
            print(f"\n{C.BOLD}{C.YELLOW}🔔 手动唤醒！我在听…{C.RESET}")
            return True
        return False

    def standby(self):
        """回到待命"""
        self.mode = "idle"

    def set_muted(self, muted: bool):
        self.muted = muted


edge = EdgeVoice()  # 全局单例


# ============================================================
# 核心模拟逻辑（复刻 cooking.py 的 handle_cooking_user_text）
# ============================================================

def handle_user_input(state: SessionState, text: str):
    """处理一条用户输入，复刻后端完整逻辑"""
    text = text.strip()
    if not text:
        return

    print_user(text)

    # ── 分支 1: 检测退出做饭 ──
    if state.cooking_enabled and any(k in text for k in COOKING_EXIT_KEYWORDS):
        state.cooking_enabled = False
        state.cooking_state = "IDLE"
        state.current_recipe = None
        state.current_step = 0
        state.completed_steps = []
        state.current_servings = None
        state.awaiting_servings = False
        print_ws("cooking_stop", {}, "→后端")
        print_ws("cooking_status", {"enabled": False, "state": "IDLE"}, "后端→")
        print_ai("好的，已退出做饭模式。想做的时候随时告诉我！")
        return

    # ── 分支 2: 还没在做饭 → 检测进入做饭 ──
    if not state.cooking_enabled:
        wants_cook = any(k in text for k in COOKING_START_KEYWORDS) or any(k in text for k in ["做", "炒", "煮", "炖", "烧"])
        wants_steps = any(k in text for k in COOKING_START_GUIDE_KEYWORDS)

        if not wants_cook:
            print_ai(f"收到你的消息：'{text}'（非做饭场景，模拟 LLM 回复）")
            return

        print(f"\n{C.DIM}>>> 后端检测到做饭意图 → 进入 cooking 流程{C.RESET}")

        _reset_cooking_session_state(state)
        print_state(state, "reset 后的状态")

        svc = get_recipe_service()
        recipe = None

        # 方式1: 直接按名字搜
        name_match = svc.search_by_name(text)
        if name_match:
            recipe = recipe_to_dict(name_match[0])
            print(f"{C.DIM}>>> 按名字匹配菜谱: {recipe['name']}{C.RESET}")

        # 方式2: 模糊纠正 —— 关键词交集匹配
        if not recipe:
            all_recipes = svc.get_all_recipes()
            known_names = [recipe_to_dict(r)["name"] for r in all_recipes]
            cleaned = re.sub(r"(教我做|我想做|我要做|步骤式|一步一步|一步步|手把手)", "", text).strip()
            cleaned = re.sub(r"[，。！？\s]", "", cleaned)
            # 把用户输入拆成单字/双字词，和菜名算重合度
            cleaned_chars = set(cleaned)
            # 先找直接子串
            for known in known_names:
                if cleaned and (known in cleaned or cleaned in known):
                    name_match = svc.search_by_name(known)
                    if name_match:
                        recipe = recipe_to_dict(name_match[0])
                        print(f"{C.DIM}>>> 模糊匹配纠正(子串): '{cleaned}' → '{recipe['name']}'{C.RESET}")
                        break
            # 子串没命中，按关键词重合度找最像的
            if not recipe and cleaned:
                best_match = None
                best_score = 0
                for known in known_names:
                    known_chars = set(known)
                    score = len(cleaned_chars & known_chars)  # 交集大小
                    if score > best_score:
                        best_score = score
                        best_match = known
                if best_match and best_score >= 3:  # 至少重合 3 个字符
                    name_match = svc.search_by_name(best_match)
                    if name_match:
                        recipe = recipe_to_dict(name_match[0])
                        print(f"{C.DIM}>>> 模糊匹配纠正(关键词重合): '{cleaned}' → '{recipe['name']}' (重合{best_score}字){C.RESET}")

        # 方式3: 按食材搜
        if not recipe:
            ingred_match = svc.search_by_ingredients([w for w in cleaned if len(w) >= 2])
            if ingred_match:
                recipe = recipe_to_dict(ingred_match[0])
                print(f"{C.DIM}>>> 按食材匹配菜谱: {recipe['name']}{C.RESET}")

        # 方式4: AI 兜底生成（mock，因为 demo 没 API key）
        if not recipe:
            print(f"{C.DIM}>>> 本地菜谱库没找到，调用 AI 大模型兜底生成...{C.RESET}")
            print_ws("llm_request", {
                "prompt": f"用户想做: {text}\n请生成一份结构化菜谱 JSON",
                "system": "你是菜谱生成专家",
            }, "后端→LLM")
            print_ws("llm_response", {"recipe": "(AI 生成的 JSON 菜谱)"}, "LLM→后端")
            # Mock 一份 AI 生成的菜谱
            import re as _re
            dish_name = _re.sub(r"[做炒煮炖烧步骤式我想教]", "", text).strip()[:6] or "创意菜谱"
            recipe = {
                "name": f"{dish_name}(AI生成)",
                "difficulty": "★★☆",
                "ingredients": ["主食材", "辅料", "调料"],
                "prep_items": ["炒锅", "碗", "筷子"],
                "servings_note": "一人份",
                "per_serving_lines": ["主食材：300克", "辅料：100克"],
                "steps": [
                    {"step_number": 1, "title": "准备食材", "content": "主食材洗净切好，辅料备好待用。"},
                    {"step_number": 2, "title": "热锅加油", "content": "炒锅烧热，倒入适量食用油。"},
                    {"step_number": 3, "title": "下锅烹饪", "content": "先炒主食材至变色，再加入辅料翻炒均匀。"},
                    {"step_number": 4, "title": "调味出锅", "content": "加盐、酱油等调味料，翻炒均匀后出锅。"},
                ],
                "tips": "火候很重要，不要炒糊了~",
            }
            print(f"{C.DIM}>>> AI 生成菜谱: {recipe['name']}，共 {len(recipe['steps'])} 步{C.RESET}")

        state.current_recipe = recipe
        state.current_step = 0
        state.awaiting_servings = True
        state.cooking_state = "AWAITING_SERVINGS"
        print_state(state, "菜谱填入后")

        print_ws("cooking_status", {
            "enabled": True,
            "state": "AWAITING_SERVINGS",
            "recipe": {"name": recipe["name"], "steps_count": len(recipe["steps"])},
            "step": 0,
            "servings": None,
            "awaiting_servings": True,
        }, "后端→前端")

        servings_note = recipe.get("servings_note", "")
        if servings_note:
            print_ai(f"找到了菜谱《{recipe['name']}》。{servings_note}\n请告诉我你准备做几人份？")
        else:
            print_ai(f"找到了菜谱《{recipe['name']}》，共 {len(recipe['steps'])} 步。\n请告诉我你准备做几人份？")
        return

    # ── 分支 3: 正在等用户说几人份 ──
    if state.awaiting_servings and state.current_recipe:
        servings = parse_servings_from_text(text)
        if servings is None:
            print_ai("请告诉我具体几人份，比如 '2人' 或 '3人份'。")
            return

        state.current_servings = servings
        state.awaiting_servings = False
        state.current_step = 1
        state.cooking_state = "COOKING"

        # ── 🧮 份量智能计算（复刻项目 cooking_core 的调料/菜量算法）──
        # 主料：线性放大  倍数 = 目标份数 / 原份数
        # 调味料：对数收敛 1 + 0.6×ln(倍数)，上限 2.5 —— 避免做10人份放100g盐
        original_servings = parse_original_servings_from_recipe(state.current_recipe)
        multiplier = servings / original_servings if original_servings > 0 else servings
        print(f"\n{C.BOLD}{C.MAGENTA}╔══ 🧮 份量智能计算（项目算法） ═══════════════════════╗{C.RESET}")
        print(f"{C.MAGENTA}║{C.RESET} 原菜谱基准: {original_servings} 人份 → 目标: {servings} 人份 → 主料倍数 ×{multiplier:.2f}")
        print(f"{C.MAGENTA}║{C.RESET} 调味料收敛: 1 + 0.6 × ln({multiplier:.2f}) = "
              f"{(1 + 0.6 * __import__('math').log(multiplier)) if multiplier > 1 else multiplier:.2f}（上限2.5，盐糖酱醋蒜姜不线性放大）")
        print_ws("servings_calc_start", {
            "servings": servings,
            "items": smart_calc_source_lines(state.current_recipe),
        }, "后端→前端")
        for line in (state.current_recipe.get("per_serving_lines") or [])[:10]:
            clean = re.sub(r"^[\s>*+-]+", "", line).strip()
            is_seasoning = any(kw in clean for kw in _SEASONING_KEYWORDS)
            eff = _seasoning_converged_multiplier(clean, multiplier)
            adjusted = _multiply_amount_in_line(clean, multiplier)
            if is_seasoning and eff != multiplier:
                tag = f"🧂调味收敛 ×{eff:.2f}"
            elif is_seasoning:
                tag = "🧂调味(无需收敛)"
            else:
                tag = f"🥩主料线性 ×{eff:.2f}"
            print(f"{C.MAGENTA}║{C.RESET} {clean}  →  {C.GREEN}{adjusted}{C.RESET}  {C.DIM}[{tag}]{C.RESET}")
        print(f"{C.MAGENTA}╚═══════════════════════════════════════════════════════╝{C.RESET}")
        print(f"{C.DIM}⏳ 份量计算引擎运行中，请稍候...{C.RESET}")
        time.sleep(4)  # 停 4 秒，让观众看清计算过程
        confirmation = build_servings_confirmation_text(state.current_recipe, servings, original_servings)
        print_ai(confirmation)

        print_state(state, "份数确认后")

        print_ws("cooking_status", {
            "enabled": True,
            "state": "COOKING",
            "recipe": {"name": state.current_recipe["name"]},
            "step": 1,
            "servings": servings,
            "awaiting_servings": False,
        }, "后端→前端")

        intro = f"好的！{servings} 人份的 {state.current_recipe['name']}，开始做饭！加油💪"
        guide = build_step_guidance_text(state, intro=intro)
        print_ai(guide)
        return

    # ── 分支 4: 正在做饭中 ──
    if state.cooking_state == "COOKING" and state.current_recipe:
        steps = state.current_recipe.get("steps", [])

        if any(k in text for k in COOKING_EXIT_KEYWORDS):
            state.cooking_enabled = False
            state.cooking_state = "IDLE"
            state.current_recipe = None
            state.current_step = 0
            state.completed_steps = []
            state.awaiting_servings = False
            state.current_servings = None
            print_ws("cooking_status", {"enabled": False, "state": "IDLE"}, "后端→前端")
            print_ai("好的，已退出做饭模式。")
            return

        if any(k in text for k in COOKING_SKIP_KEYWORDS):
            next_step = state.current_step + 1
            if next_step > len(steps):
                state.cooking_state = "FINISHED"
                print_ws("cooking_status", {"state": "FINISHED"}, "后端→前端")
                print_ai("全部步骤跳过啦，也算完成！🎉")
            else:
                state.current_step = next_step
                print_ws("cooking_status", {"step": next_step}, "后端→前端")
                guide = build_step_guidance_text(state, intro="好的，跳过这步，直接看下一步：")
                print_ai(guide)
            print_state(state, "跳过后")
            return

        if any(k in text for k in COOKING_CONFIRM_KEYWORDS):
            if not state.current_recipe:
                print_ai("您还没有选择菜谱呢，请先告诉我您想做什么菜！")
                return

            if state.current_step > len(steps):
                print_ai("恭喜！所有步骤都已完成！🎉")
                return

            if state.current_step not in state.completed_steps:
                state.completed_steps.append(state.current_step)

            next_step = state.current_step + 1

            if next_step > len(steps):
                state.cooking_state = "FINISHED"
                print_ws("cooking_status", {
                    "state": "FINISHED",
                    "step": state.current_step,
                    "completed_steps": state.completed_steps,
                }, "后端→前端")
                print_state(state, "完成！")
                print_ai(
                    f"太棒了！🎉 您的 {state.current_recipe['name']} 做好了！\n\n"
                    f"✨ 全部 {len(steps)} 步已完成\n"
                    f"👨‍🍳 完成步骤：{state.completed_steps}\n\n"
                    f"趁热吃风味更佳！想再做一道的话随时告诉我~"
                )
            else:
                state.current_step = next_step
                print_ws("cooking_status", {
                    "step": next_step,
                    "completed_steps": state.completed_steps,
                }, "后端→前端")
                print_state(state, f"第 {next_step} 步引导")
                guide = build_step_guidance_text(
                    state,
                    intro=f"太棒了！🎉 第{state.current_step - 1}步完成。"
                )
                print_ai(guide)
            return

        print_ws("user_text", {"text": text, "scene": "cooking"}, "←前端→后端")
        print_ws("llm_request", {
            "system": "你是做饭助手...",
            "history": "...压缩后的对话历史...",
            "prompt": f"当前菜谱={state.current_recipe['name']}, 当前步骤={state.current_step}/{len(steps)}, 用户问: {text}",
        }, "后端→LLM")
        print_ws("assistant_delta", {"delta": "（LLM 流式回复...）"}, "LLM→后端→前端")
        print_ai(f"（模拟 LLM 回复）关于'{text}'，我的建议是：跟着菜谱步骤做就没问题！")
        return

    print_ai("好的，我在听。你可以说'教我做番茄炒蛋'来开始做饭引导。")


# ============================================================
# 演示入口
# ============================================================

def demo():
    clear()
    print(f"{C.BOLD}{C.CYAN}{'=' * 65}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}   🍳 智能厨房 AI 助手 - 前后端交互 Demo{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'=' * 65}{C.RESET}")
    print()
    print(f"{C.DIM}这个 demo 模拟完整的 WebSocket 消息流转：{C.RESET}")
    print(f"{C.DIM}  用户输入 → 前端发事件 → 后端路由 → 状态机变更 → 推消息回前端{C.RESET}")
    print(f"{C.DIM}  同时展示 🗣️ 文本/语音转换管道：TTS 清理→切分→合成→PCM 推送{C.RESET}")
    print()
    print(f"{C.DIM}内置菜谱库：{C.RESET}", end="")
    svc = get_recipe_service()
    for r in svc.search_by_ingredients(["番茄", "鸡蛋"])[:3]:
        print(f"{C.GREEN}{recipe_to_dict(r)['name']}{C.RESET}", end="  ")
    print()
    print()
    print(f"{C.BOLD}{C.CYAN}🗣️ 音频模式:{C.RESET} {audio_mode.status_str()} {'✅已开启' if audio_mode.enabled else '❌已关闭'}")
    if not TTS_FROM_PROJECT:
        print(f"{C.DIM}   (app.ws 导入失败(常见: 缺 pyserial)，TTS 管道用本地简化实现，真声播放不受影响){C.RESET}")
    # 端侧语音模块（方案 = backend/cmd_voice_chat.py: KWS唤醒 + FunASR流式识别）
    if REAL_AUDIO_AVAILABLE:
        try:
            _dev = _sd.query_devices(_sd.default.device[1])
            _cur = audio_mode.output_device if audio_mode.output_device is not None else _sd.default.device[1]
            _dname = _sd.query_devices(_cur)["name"]
            print(f"{C.DIM}   当前播放目标: [{_cur}] {_dname}（听不到就输 test / spk 换设备）{C.RESET}")
        except Exception:
            pass
    edge_started = edge.start()
    if edge_started:
        print(f"{C.GREEN}   📡 端侧语音模块已连接: KWS唤醒词(8767) {'✅' if edge.kws_ok else '❌'} + FunASR流式识别(8768) {'✅' if edge.asr_ok else '❌'}{C.RESET}")
        print(f"{C.GREEN}   说「小云小云」唤醒（或按回车手动唤醒）→ 直接说话即可，免按键免回车{C.RESET}")
    elif REAL_AUDIO_AVAILABLE:
        print(f"{C.GREEN}   🔊 AI 回复默认真声播报（小米TTS），输入 {C.BOLD}real{C.RESET}{C.GREEN} 可关闭；语音输入见上方端侧/回车说明{C.RESET}")
    else:
        print(f"{C.DIM}   (真实语音不可用: 需 pip install sounddevice + app.runtime_providers 可导入){C.RESET}")
    print()
    print(f"{C.YELLOW}预设脚本演示（输入 'voice' 切回回车=脚本）：{C.RESET}")
    print(f"  ① 教我做番茄炒蛋 步骤式")
    print(f"  ② 2人")
    print(f"  ③ 好了  (重复 5 次)")
    print(f"  ④ 退出做饭")
    print()
    print(f"{C.DIM}语音/文本转换指令：{C.RESET}")
    print(f"  (直接回车)      {'🔔 手动唤醒端侧语音(等价说小云小云)' if edge_started else '🎙️ 麦克风录音 → 云端STT识别 → 注入做饭流程'}")
    voice_hint = "暂停/恢复端侧语音麦克风" if edge_started else "开关'回车=语音输入'默认行为"
    print(f"  voice           {voice_hint}")
    print(f"  standby         端侧语音回待命(需重新唤醒)")
    print(f"  spk [编号]      查看/切换扬声器设备（没声音就换一个）")
    print(f"  test            播放测试音（验证扬声器通路）")
    print(f"  real            🔴 开关真实语音模式(AI回复真声播放)")
    print(f"  mic             显式录音(云端STT回退通道)")
    print(f"  speak <文字>    手动测试 TTS 管道")
    print(f"  stream <文字>   模拟 LLM 流式输出 + 并行 TTS")
    print(f"  stt <文字>      模拟 STT 语音识别管道")
    print(f"  toggle          开关语音管道可视化")
    print(f"  debug           开关管道完整细节(默认一行摘要)")
    print(f"  provider        切换 TTS 提供商 (xiaomi/dashscope/siliconflow/...)")
    print(f"  source          切换播放源 (本地扬声器 / 眼镜)")
    print()
    print(f"{C.DIM}其他：quit 退出 | reset 重置 | state 查看状态{C.RESET}")
    print()

    state = SessionState()
    CURRENT_STATE["s"] = state  # 语音回调线程通过它拿到主状态

    auto_script = [
        "教我做番茄炒蛋 步骤式",
        "2人",
        "好了", "好了", "好了", "好了", "好了",
    ]
    script_idx = [0]

    while True:  # 显示当前状态提示
        if state.cooking_enabled and state.current_recipe:
            steps_count = len(state.current_recipe.get("steps", []))
            hint = f"{C.YELLOW}[做饭中] step {state.current_step}/{steps_count}{C.RESET}"
        else:
            hint = f"{C.DIM}[空闲]{C.RESET}"
        if edge.enabled and not edge.paused:
            hint += (f" {C.CYAN}[🎤聆听]{C.RESET}" if edge.mode == "listen"
                     else f" {C.CYAN}[🎤待命:小云小云]{C.RESET}")
        if audio_mode.enabled:
            hint += f" {C.CYAN}[🔊]{C.RESET}"

        try:
            raw = input(f"{hint} {C.BOLD}你说:{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.DIM}退出。{C.RESET}")
            break

        if raw.lower() in ("quit", "exit", "q"):
            print(f"{C.DIM}再见！{C.RESET}")
            break
        if raw.lower() == "reset":
            state = SessionState()
            CURRENT_STATE["s"] = state
            print(f"{C.DIM}状态已重置{C.RESET}")
            continue
        if raw.lower() == "standby":
            edge.standby()
            print(f"{C.DIM}端侧语音已回待命（说「小云小云」或回车重新唤醒）{C.RESET}")
            continue
        if raw.lower() == "state":
            print_state(state, "当前状态")
            print(f"  音频: {audio_mode.status_str()} {'✅' if audio_mode.enabled else '❌'}")
            continue
        if raw.lower() == "toggle":
            on = audio_mode.toggle()
            print(f"{C.DIM}语音管道可视化: {'✅ 开启' if on else '❌ 关闭'}{C.RESET}")
            continue
        if raw.lower() == "debug":
            audio_mode.debug = not audio_mode.debug
            print(f"{C.DIM}管道细节: {'🔍 显示完整逐块日志' if audio_mode.debug else '简洁一行摘要'}{C.RESET}")
            continue
        if raw.lower() == "voice":
            if edge.enabled:
                edge.paused = not edge.paused
                print(f"{C.DIM}端侧语音麦克风: {'⏸ 已暂停' if edge.paused else '✅ 已恢复'}{C.RESET}")
            else:
                audio_mode.voice_default = not audio_mode.voice_default
                print(f"{C.DIM}默认语音输入: {'✅ 开启 — 直接回车就是录音' if audio_mode.voice_default else '❌ 关闭 — 回车走预设脚本'}{C.RESET}")
            continue
        if raw.lower() == "real":
            if not REAL_AUDIO_AVAILABLE:
                print(f"{C.RED}真实语音不可用: 需 sounddevice + app.runtime_providers 可导入{C.RESET}")
                continue
            audio_mode.real = not audio_mode.real
            print(f"{C.DIM}真实语音模式: {'🔴 开启 — AI回复会用真声播放' if audio_mode.real else '⬜ 关闭 — 回到模拟管道'}{C.RESET}")
            continue
        if raw.lower() == "mic":
            if not REAL_AUDIO_AVAILABLE:
                print(f"{C.RED}真实语音不可用: 需 sounddevice + app.runtime_providers 可导入{C.RESET}")
                continue
            text = real_record_and_transcribe()
            if text:
                print_user(text)
                print(f"{C.DIM}>>> STT 识别结果注入到 handle_cooking_user_text{C.RESET}")
                handle_user_input(state, text)
            continue
        if raw.lower() == "provider":
            p = audio_mode.cycle_provider()
            print(f"{C.DIM}TTS 提供商 → {C.CYAN}{p}{C.RESET}")
            continue
        if raw.lower() == "source":
            s = audio_mode.cycle_source()
            print(f"{C.DIM}播放源 → {C.CYAN}{'眼镜' if s == 'glasses' else '本地扬声器'}{C.RESET}")
            continue
        if raw.lower() == "spk":
            import numpy as _np
            devs = _sd.query_devices()
            default_out = _sd.default.device[1]
            print(f"{C.CYAN}输出设备列表（当前播放目标: "
                  f"{audio_mode.output_device if audio_mode.output_device is not None else f'系统默认({default_out})'}）:{C.RESET}")
            for i, d in enumerate(devs):
                if d["max_output_channels"] > 0:
                    mark = " ←当前" if i == (audio_mode.output_device if audio_mode.output_device is not None else default_out) else ""
                    print(f"  [{i}] {d['name']} ({d['max_output_channels']}ch){mark}")
            print(f"{C.DIM}切换: spk <编号>，例如 spk 5{C.RESET}")
            continue
        if raw.lower().startswith("spk "):
            try:
                audio_mode.output_device = int(raw[4:].strip())
                name = _sd.query_devices(audio_mode.output_device)["name"]
                print(f"{C.GREEN}✅ 播放设备切换到 [{audio_mode.output_device}] {name}{C.RESET}")
            except Exception as exc:
                print(f"{C.RED}设备切换失败: {exc}{C.RESET}")
            continue
        if raw.lower() == "test":
            import numpy as _np
            sr = 24000
            t = _np.linspace(0, 0.6, int(sr * 0.6), endpoint=False)
            beep = (_np.sin(2 * _np.pi * 440 * t) * 12000).astype("int16")
            _sd.play(beep, samplerate=sr, device=audio_mode.output_device)
            _sd.wait()
            dev = audio_mode.output_device if audio_mode.output_device is not None else "系统默认"
            print(f"{C.GREEN}✅ 测试音已播放（设备: {dev}）。听到'嘟'了吗？没听到就 spk 换设备{C.RESET}")
            continue
        if raw.lower().startswith("speak "):
            text = raw[6:].strip()
            if not text:
                print(f"{C.DIM}用法: speak 你好世界{C.RESET}")
            elif audio_mode.real and REAL_AUDIO_AVAILABLE:
                real_speak(text)
            else:
                print_tts_pipeline(text)
            continue
        if raw.lower().startswith("stream "):
            text = raw[7:].strip()
            if not text:
                print(f"{C.DIM}用法: stream 模拟 LLM 流式输出{C.RESET}")
            else:
                print_streaming_tts_demo(text)
            continue
        if raw.lower().startswith("stt "):
            text = raw[4:].strip()
            if not text:
                print(f"{C.DIM}用法: stt 我想做番茄炒蛋{C.RESET}")
            else:
                print_stt_pipeline(text)
                # 模拟 STT 注入到做饭流程
                print_user(text)
                print(f"{C.DIM}>>> STT 识别结果注入到 handle_cooking_user_text{C.RESET}")
                handle_user_input(state, text)
            continue
        if raw == "":
            # 端侧语音已启用 → 回车 = 手动唤醒（等价说「小云小云」）
            if edge.enabled and not edge.paused:
                edge.wake()
                continue
            # 云端回退: 真声可用 + voice_default 开启 → 空回车=录音
            if audio_mode.voice_default and REAL_AUDIO_AVAILABLE:
                text = real_record_and_transcribe()
                if text:
                    print_user(text)
                    handle_user_input(state, text)
                continue
            if script_idx[0] < len(auto_script):
                raw = auto_script[script_idx[0]]
                script_idx[0] += 1
                print(f"{C.DIM}(自动演示) 第{script_idx[0]}步: {raw}{C.RESET}")
                print_stt_pipeline(raw)  # 自动演示也模拟 STT 语音输入
            else:
                print(f"{C.DIM}演示脚本已播完。随便输入点什么试试？{C.RESET}")
                continue

        handle_user_input(state, raw)


if __name__ == "__main__":
    demo()
