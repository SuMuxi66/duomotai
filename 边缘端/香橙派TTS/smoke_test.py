"""smoke_test.py — 板上冒烟测试：加载模型、合成两句、打印符号/时长/RTF。"""
import sys
import torch
torch.set_num_threads(6)
import time

sys.path.insert(0, "/home/orangepi/tts")
import ttsfrd_compat  # noqa: E402

sys.modules["ttsfrd"] = ttsfrd_compat

TEXT = "好的，开始做番茄炒蛋。第一步，打3个鸡蛋。"

fe = ttsfrd_compat.TtsFrontendEngine()
lines = fe.gen_tacotron_symbols(TEXT)
print("=== frontend output ===")
for ln in lines.splitlines():
    print(ln[:220])
n_groups = sum(len(l.split("\t")[1].split()) for l in lines.splitlines())
print("symbol groups:", n_groups)

from modelscope.pipelines import pipeline  # noqa: E402
from modelscope.utils.constant import Tasks  # noqa: E402

MD = "/home/orangepi/.cache/modelscope/hub/models/damo/speech_sambert-hifigan_tts_zh-cn_16k"
t0 = time.time()
pipe = pipeline(task=Tasks.text_to_speech, model=MD)
print("model load: %.1fs, voices=%s" % (time.time() - t0, list(pipe.model.voices)))

import wave  # noqa: E402
import io  # noqa: E402

for text in ["好的，开始做番茄炒蛋。", "第一步，打3个鸡蛋，搅拌均匀。"]:
    t1 = time.time()
    out = pipe(text)
    dt = time.time() - t1
    wav = out["output_wav"]
    with wave.open(io.BytesIO(wav)) as w:
        dur = w.getnframes() / w.getframerate()
        rate = w.getframerate()
        ch = w.getnchannels()
    raw = wav
    print("text=%s synth=%.2fs audio=%.2fs rtf=%.2f rate=%d ch=%d bytes=%d" %
          (text, dt, dur, dt / dur, rate, ch, len(raw)))
    fn = "/home/orangepi/tts/out_%d.wav" % int(time.time())
    with open(fn, "wb") as f:
        f.write(wav)
    print("saved", fn)
print("SMOKE_OK")
