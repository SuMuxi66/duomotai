"""
sherpa-onnx 关键词唤醒(KWS)WebSocket 服务
部署于 RK3588: /home/orangepi/rokid-edge/scripts/kws_server.py

协议:
  - WS /kws: 客户端发二进制 PCM16 16kHz mono 帧(~100ms/帧),服务端命中时回 JSON
  - GET /health: 返回模型与关键词状态

命中 JSON: {"event":"wakeword","keyword":"小云小云","timestamp":<ms>,"conf":1.0}
"""

import asyncio
import json
import logging
import os
import time
import numpy as np
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("kws_server")

import sherpa_onnx

# ---------------- 配置 ----------------
KWS_DIR = os.environ.get(
    "KWS_MODEL_DIR",
    "/home/orangepi/rokid-edge/models/kws/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
)
KEYWORDS_FILE = os.environ.get(
    "KWS_KEYWORDS_FILE",
    "/home/orangepi/rokid-edge/models/kws/keywords.txt",
)
HOST = "0.0.0.0"
PORT = int(os.environ.get("KWS_PORT", "8767"))
SAMPLE_RATE = 16000
NUM_THREADS = int(os.environ.get("KWS_NUM_THREADS", "2"))
MAX_ACTIVE_PATHS = int(os.environ.get("KWS_MAX_ACTIVE_PATHS", "4"))
# 命中后冷却(ms),避免同一句重复触发
COOLDOWN_MS = int(os.environ.get("KWS_COOLDOWN_MS", "1200"))
# 关键词最大触发数(0 = 无限);sherpa 用 keywords_file 时此项作用于内部图
MAX_TRIGGERS = int(os.environ.get("KWS_MAX_TRIGGERS", "0"))

DEFAULT_KEYWORDS = ["小云小云", "小芸小芸"]

MODEL_NAME = "kws-zipformer-wenetspeech-3.3M"


# ---------------- 关键词文件生成 ----------------
def _chinese_to_kws_format(text: str) -> str:
    from pypinyin import lazy_pinyin, Style

    pinyins = lazy_pinyin(text, style=Style.TONE)
    tokens = []
    for py in pinyins:
        if len(py) > 2 and py[:2] in ("zh", "ch", "sh"):
            tokens.append(py[:2])
            tokens.append(py[2:])
        elif len(py) > 1 and py[0] in "bpmfdtnlgkhjqxrzcsyw":
            tokens.append(py[0])
            tokens.append(py[1:])
        else:
            tokens.append(py)
    return " ".join(tokens) + " @" + text


def ensure_keywords_file(words):
    if os.path.exists(KEYWORDS_FILE):
        log.info("使用已有关键词文件: %s", KEYWORDS_FILE)
        return KEYWORDS_FILE
    os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        for w in words:
            line = _chinese_to_kws_format(w)
            f.write(line + "\n")
            log.info("关键词行: %s", line)
    return KEYWORDS_FILE


# ---------------- 模型文件查找 ----------------
def _pick(files, prefer_substr="epoch-12-avg-2"):
    """从候选文件中选择,优先含 prefer_substr 的(且 int8 优先)。"""
    if not files:
        return None
    # 优先 int8
    int8 = [f for f in files if "int8" in f.name]
    pool = int8 if int8 else files
    pref = [f for f in pool if prefer_substr in f.name]
    if pref:
        return str(pref[0])
    return str(pool[0])


def find_model_files(model_dir: str) -> dict:
    p = Path(model_dir)
    enc = _pick(list(p.glob("encoder*.onnx")))
    dec = _pick(list(p.glob("decoder*.onnx")))
    joi = _pick(list(p.glob("joiner*.onnx")))
    tok = _pick(list(p.glob("tokens.txt")))
    return {"encoder": enc, "decoder": dec, "joiner": joi, "tokens": tok}


# ---------------- 全局 spotter ----------------
class KWSEngine:
    def __init__(self):
        self.kws = None
        self.keywords = list(DEFAULT_KEYWORDS)
        self.last_trigger_ts = 0.0

    def init(self):
        if not os.path.isdir(KWS_DIR):
            raise RuntimeError(f"模型目录不存在: {KWS_DIR}")
        files = find_model_files(KWS_DIR)
        if not all(files.values()):
            raise RuntimeError(f"模型文件不完整: {files}")
        log.info(
            "模型文件: encoder=%s decoder=%s joiner=%s tokens=%s",
            Path(files["encoder"]).name,
            Path(files["decoder"]).name,
            Path(files["joiner"]).name,
            Path(files["tokens"]).name,
        )
        ensure_keywords_file(self.keywords)
        kwargs = dict(
            tokens=files["tokens"],
            encoder=files["encoder"],
            decoder=files["decoder"],
            joiner=files["joiner"],
            keywords_file=KEYWORDS_FILE,
            num_threads=NUM_THREADS,
            max_active_paths=MAX_ACTIVE_PATHS,
        )
        if MAX_TRIGGERS > 0:
            kwargs["max_triggers"] = MAX_TRIGGERS
        self.kws = sherpa_onnx.KeywordSpotter(**kwargs)
        log.info("KeywordSpotter 初始化完成, num_threads=%d", NUM_THREADS)
        return True

    def detect_pcm(self, pcm_bytes: bytes):
        """送入一帧 PCM16,返回命中关键词字符串或 None。"""
        if not self.kws:
            return None
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return None
        stream = self.kws.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        # 尾部 padding,提升句末召回(sherpa 推荐)
        tail = np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.float32)
        stream.accept_waveform(SAMPLE_RATE, tail)
        while self.kws.is_ready(stream):
            self.kws.decode_stream(stream)
        result = self.kws.get_result(stream)
        if result:
            kw = result.strip() if isinstance(result, str) else str(result).strip()
            # 去掉可能的 @ 前缀
            if kw.startswith("@"):
                kw = kw[1:].strip()
            # 取 @ 后中文标注
            if "@" in kw:
                kw = kw.split("@")[-1].strip()
            if kw:
                now = time.time() * 1000.0
                if now - self.last_trigger_ts < COOLDOWN_MS:
                    return None
                self.last_trigger_ts = now
                return kw
        return None


engine = KWSEngine()


# ---------------- HTTP /health ----------------
async def handle_health(scope, receive, send):
    body = json.dumps(
        {
            "status": "ok",
            "model": MODEL_NAME,
            "keywords": engine.keywords,
            "model_dir": KWS_DIR,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"access-control-allow-origin", b"*"),
            ],
        }
    )
    send({"type": "http.response.body", "body": body})


# ---------------- WS /kws ----------------
async def handle_kws(websocket):
    log.info("WS 客户端连接: %s", websocket.remote_address)
    # 每连接独立流(流不能跨连接共享)
    stream = engine.kws.create_stream()
    last_trigger_ts = 0.0
    try:
        async for msg in websocket:
            # 文本帧: 可选初始化 {"action":"start","keywords":[...]}
            if isinstance(msg, str):
                try:
                    obj = json.loads(msg)
                    if obj.get("action") == "start":
                        log.info("客户端 start: %s", obj)
                except Exception:
                    pass
                continue
            # 二进制帧: PCM16
            pcm = bytes(msg)
            if len(pcm) < 2:
                continue
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                continue
            stream.accept_waveform(SAMPLE_RATE, audio)
            while engine.kws.is_ready(stream):
                engine.kws.decode_stream(stream)
            result = engine.kws.get_result(stream)
            if result:
                kw = result.strip() if isinstance(result, str) else str(result).strip()
                if kw.startswith("@"):
                    kw = kw[1:].strip()
                if "@" in kw:
                    kw = kw.split("@")[-1].strip()
                if kw:
                    now = time.time() * 1000.0
                    if now - last_trigger_ts < COOLDOWN_MS:
                        # 冷却中,跳过(重置流避免连续触发)
                        stream = engine.kws.create_stream()
                        continue
                    last_trigger_ts = now
                    payload = {
                        "event": "wakeword",
                        "keyword": kw,
                        "timestamp": int(now),
                        "conf": 1.0,
                    }
                    log.info("命中唤醒词: %s", payload)
                    await websocket.send(json.dumps(payload, ensure_ascii=False))
                    # 命中后重置流,清除内部状态
                    stream = engine.kws.create_stream()
    except Exception as e:
        log.warning("WS 异常: %s", e)


async def main():
    engine.init()
    # 用 websockets 库同时服务 WS + HTTP
    import websockets
    from websockets.server import serve

    async def handler(websocket):
        path = websocket.path
        if path == "/kws":
            await handle_kws(websocket)
        else:
            await websocket.close(code=1008, reason="unknown path")

    async def process_request(path, request_headers):
        if path == "/health":
            return (200, [
                ("content-type", "application/json; charset=utf-8"),
                ("access-control-allow-origin", "*"),
            ], json.dumps(
                {
                    "status": "ok",
                    "model": MODEL_NAME,
                    "keywords": engine.keywords,
                    "model_dir": KWS_DIR,
                },
                ensure_ascii=False,
            ).encode("utf-8"))
        if path == "/kws":
            return None  # 交由 ws handler
        return (404, [("content-type", "text/plain")], b"not found")

    log.info("KWS WS 服务启动: ws://%s:%d/kws  (health: GET /health)", HOST, PORT)
    async with serve(
        handler,
        HOST,
        PORT,
        process_request=process_request,
        max_size=2 ** 20,
        ping_interval=20,
        ping_timeout=20,
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
