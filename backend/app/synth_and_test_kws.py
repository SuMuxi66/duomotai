"""用百度 TTS 合成"小云小云"音频,用 KWS 检测命中,再通过 WS 端到端验证。
用法: python3 synth_and_test_kws.py
"""
import os
import subprocess
import wave
import time
import asyncio
import json
import glob
import urllib.request
import urllib.parse
import numpy as np
import sherpa_onnx
import websockets

KWS_DIR = "/home/orangepi/rokid-edge/models/kws/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
KW_FILE = "/home/orangepi/rokid-edge/models/kws/keywords.txt"
DATA_DIR = "/home/orangepi/rokid-edge/data"
MP3_PATH = os.path.join(DATA_DIR, "xiaoyun_baidu.mp3")
WAV_PATH = os.path.join(DATA_DIR, "xiaoyun_test.wav")


def synth_baidu(text="小云小云"):
    """用百度翻译 TTS 合成中文音频。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    q = urllib.parse.urlencode({"lan": "zh", "text": text, "spd": 3, "source": "web"})
    url = "https://fanyi.baidu.com/gettts?" + q
    print(f"百度 TTS 合成: {text}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    with open(MP3_PATH, "wb") as f:
        f.write(data)
    print(f"  mp3 保存: {MP3_PATH} ({len(data)} bytes)")
    # ffmpeg 转 16kHz mono pcm wav
    if os.path.exists(WAV_PATH):
        os.remove(WAV_PATH)
    subprocess.run(
        ["ffmpeg", "-y", "-i", MP3_PATH, "-ar", "16000", "-ac", "1",
         "-acodec", "pcm_s16le", WAV_PATH],
        check=True, capture_output=True,
    )
    w = wave.open(WAV_PATH, "rb")
    n = w.getnframes()
    sr = w.getframerate()
    w.close()
    print(f"  wav: sr={sr} dur={n/sr:.2f}s")
    return True


def test_kws_local():
    """本地 KWS 检测。"""
    enc = glob.glob(KWS_DIR + "/encoder-epoch-12*int8*.onnx")[0]
    dec = glob.glob(KWS_DIR + "/decoder-epoch-12*int8*.onnx")[0]
    joi = glob.glob(KWS_DIR + "/joiner-epoch-12*int8*.onnx")[0]
    tok = KWS_DIR + "/tokens.txt"
    kws = sherpa_onnx.KeywordSpotter(
        tokens=tok, encoder=enc, decoder=dec, joiner=joi,
        keywords_file=KW_FILE, num_threads=2, max_active_paths=4,
    )
    print("KWS 初始化完成(小云小云关键词)")

    w = wave.open(WAV_PATH, "rb")
    n = w.getnframes()
    raw = w.readframes(n)
    sr = w.getframerate()
    w.close()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    st = kws.create_stream()
    st.accept_waveform(sr, audio)
    st.accept_waveform(sr, np.zeros(int(sr * 0.4), dtype=np.float32))
    t0 = time.time()
    hits = []
    while kws.is_ready(st):
        kws.decode_stream(st)
        r = kws.get_result(st)
        if r:
            hits.append(str(r))
    dt = (time.time() - t0) * 1000
    print(f"本地 KWS: decode={dt:.0f}ms hits={hits}")
    return hits


async def test_kws_ws():
    """WS 端到端推送,验证 8767 主服务命中。"""
    w = wave.open(WAV_PATH, "rb")
    n = w.getnframes()
    sr = w.getframerate()
    raw = w.readframes(n)
    w.close()
    bpf = int(sr * 0.1) * 2
    frames = [raw[i:i+bpf] for i in range(0, len(raw), bpf)]
    url = "ws://127.0.0.1:8767/kws"
    t_start = time.time()
    hits = []
    async with websockets.connect(url, max_size=2**24) as ws:
        print(f"WS 连接 {url}, 推送 {len(frames)} 帧")
        async def recv_loop():
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue
                    if obj.get("event") == "wakeword":
                        lat = time.time()*1000 - t_start*1000
                        print(f"  WS HIT: {obj} latency={lat:.0f}ms")
                        hits.append(obj)
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                pass
        rt = asyncio.create_task(recv_loop())
        for i, f in enumerate(frames):
            await ws.send(f)
            if i % 5 == 4:
                await asyncio.sleep(0.02)
        await asyncio.sleep(1.5)
        rt.cancel()
    print(f"WS 端到端命中: {len(hits)} 次")
    return hits


def main():
    print("=== 1. 百度 TTS 合成小云小云 ===")
    synth_baidu("小云小云")
    print("\n=== 2. 本地 KWS 检测 ===")
    local_hits = test_kws_local()
    print("\n=== 3. WS 端到端检测(8767)===")
    ws_hits = asyncio.run(test_kws_ws())
    print(f"\n=== 总结 ===")
    print(f"本地命中: {len(local_hits)>0} ({local_hits})")
    print(f"WS 命中: {len(ws_hits)>0} ({ws_hits})")


if __name__ == "__main__":
    main()
