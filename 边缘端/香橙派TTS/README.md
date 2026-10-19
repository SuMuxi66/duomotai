# 香橙派 Sambert TTS 服务（192.168.1.248:8769）

RK3588（orangepi5max）本地中文语音合成服务，基于阿里 ModelScope 的
`damo/speech_sambert-hifigan_tts_zh-cn_16k`（Sambert 声学模型 + HiFTGAN 声码器），
纯 CPU 推理、完全离线。2026-09-09 部署完成。

## 部署位置（板上）

| 路径 | 内容 |
| --- | --- |
| `/home/orangepi/tts/venv` | Python 3.10 venv（`--system-site-packages`，复用系统 torch 2.2.0） |
| `/home/orangepi/tts/tts_server.py` | FastAPI 服务（含启动预热线程） |
| `/home/orangepi/tts/ttsfrd_compat.py` | 开源前端（替代闭源 ttsfrd，jieba+pypinyin+cn2an） |
| `/home/orangepi/tts/py2phoneMap.txt` | 拼音→音素映射表（KAN-TTS 自带） |
| `/home/orangepi/tts/smoke_test.py` | 冒烟测试脚本 |
| `/home/orangepi/.cache/modelscope/hub/models/damo/speech_sambert-hifigan_tts_zh-cn_16k` | 模型（4 音色） |
| `/etc/systemd/system/tts-sambert.service` | systemd 单元（副本在 `/home/orangepi/tts/tts-sambert.service`） |

## 服务管理

```bash
# 板上（SSH orangepi/orangepi）
systemctl status tts-sambert     # 状态
sudo systemctl restart tts-sambert
journalctl -u tts-sambert -f     # 日志
```

- 开机自启（`systemctl enable` 已做）、崩溃自动拉起（`Restart=always`）。
- 启动后后台线程预热加载模型（约 20~25s），期间 `/health` 返回 `loading`，之后返回 `ok`。
- 进程绑 A76 大核（cpu4-7，`taskset`），`TTS_THREADS=4`。

## HTTP 接口

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 健康检查，返回音色列表 |
| `GET /voices` | 音色列表 |
| `GET /tts?text=<中文>&voice=<音色>&emotion=<情绪>&speaker=<F7>` | 合成，返回 WAV（16k 单声道 16bit） |
| `POST /tts` | body: `{"text": "...", "voice": "...", "emotion": "...", "speaker": "..."}` |

响应头带性能指标：`X-Synth-Seconds`（合成耗时）、`X-Audio-Seconds`（音频时长）、`X-RTF`（两者之比）、`X-Voice`。

```powershell
# Windows 侧示例
curl.exe -o test.wav "http://192.168.1.248:8769/tts?text=%E5%A5%BD%E7%9A%84"
```

## 音色表

| voice | 说明 |
| --- | --- |
| `zhitian_emo` | 女声（默认） |
| `zhibei_emo` | 女声 |
| `zhizhe_emo` | 男声 |
| `zhiyan_emo` | 女声 |

`emotion` 支持 `emotion_neutral` 等（需在音色 emo 字典内，默认 neutral）；
`speaker` 符号固定 F7 与官方管线一致，仅实验口子。

## 性能实测（2026-09-09）

- 模型加载：约 20~25s（systemd 启动预热，不占首请求）
- 稳态合成 RTF ≈ **3.0**（A76 大核 + performance governor；纯 ondemand 下约 3.6）
- 例句："好的，开始做饭"（1.54s 音频）合成 4.55s；语速约 4 字/秒，正常范围
- 一句话延迟量级：2~3 秒的语音需 7~9 秒合成——**实时对话偏慢，演示场景可用**；
  如需接入后端请按句拆分、流式下发或接受等待
- int8 动态量化实测仅提速 7%（RTF 2.98→2.76）且改输出，未采纳；
  瓶颈在 FSMN/注意力/Conv 声码器，torch 动态量化覆盖不到

## 已知坑（复建/排障必读）

1. **scipy/kaiser**：系统 `~/.local` 的 scipy 1.15.3 移除了 `scipy.signal.kaiser`，
   `import SambertHifigan` 直接炸。必须在 venv 里装 `scipy==1.12.0` 遮蔽系统版。
2. **kantts PyPI 空壳包**：PyPI 的 `kantts 0.1.0` 是占位空包，必须用
   modelscope/KAN-TTS@main 真源码拷入 venv site-packages。
3. **EOS 不能裸写**：kantts 各字段 encode 时自动在序列末尾追加 `~`（EOS id）。
   前端符号序列**不能**再带裸 `~`，否则 `encode_symbol_sequence` 内
   `this_lfeat_symbol[index]` IndexError（`~` 按 `$` split 只有 1 个字段，lfeat 有 6 个）。
4. **torchaudio ABI**：系统 torchaudio 2.9.1 与 torch 2.2.0 不匹配会崩，
   venv 内锁 `torchaudio==2.2.0`。
5. **ttsfrd 闭源**：阿里 ttsfrd 只有 x86 wheel，aarch64 用不了，所以有
   `ttsfrd_compat.py`（jieba 分词 + pypinyin 转拼音 + py2phoneMap 转音素 +
   cn2an 数字转中文）。注意它必须在 modelscope 之前 `sys.modules["ttsfrd"]=...` 注入。
6. **venv 里没有 uvicorn 入口脚本**：`./venv/bin/uvicorn` 不存在，
   用 `./venv/bin/python -m uvicorn`。
7. **pydantic v2**：`voice: str = None` 会校验失败，要写 `Optional[str] = None`。
8. **文件传输**：orangepi 用户的 paramiko SFTP `put` 实测可用（root 曾不可用，
   不可用时走 base64 分块：`base64 -d` 拼文件）。
9. **Windows 下 rk_ssh.py 传命令**：PowerShell 双引号会吞 `$var`；
   bash 内层命令带 `$` 时用 python 拼接（如 chr() 拼 governor 设置）绕过。
   `pkill -f 'xxx'` 会匹配到外层 bash 自身命令行导致自杀断连，按 PID kill。
10. **CPU governor**：默认 ondemand。设 performance 可再快约 15%：
    ```bash
    echo orangepi | sudo -S cpupower frequency-set -g performance  # cpupower 不存在时见下
    # 板上无 cpupower，用 python 写 sysfs：
    echo orangepi | sudo -S python3 -c "import glob; [open(p,'w').write('performance') for p in glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor')]"
    ```
    重启后回 ondemand（未固化，避免影响整机功耗策略）。

## 端口规划（板子）

| 端口 | 服务 |
| --- | --- |
| 8767 | KWS 唤醒词（Docker，前端直连） |
| 8768 | 流式 ASR（Docker，前端直连） |
| **8769** | **TTS（本服务，systemd）** |

## 后续可选（未做）

- 后端 TTS provider 接入 `http://192.168.1.248:8769/tts`（受 RTF≈3 限制，需按句拆分策略）
- CosyVoice-300M 备选（CPU RTF>1，仅答辩备胎）
