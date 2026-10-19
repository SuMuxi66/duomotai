# 智能烹饪助手 · Smart Cooking Assistant

> 一套面向厨房场景的多模态 AI 助手：**语音对话 + 做饭步骤引导 + 视觉危险检测 + 传感器读数 + 硬件控制**，
> 由 FastAPI 后端、React 前端与香橙派（RK3588）边缘语音节点三部分组成。

**本仓库为源码快照**，可直接构建运行；**不含**真实密钥、演示材料（答辩讲稿 / PPT）与副本前端。

---

## ✨ 功能概览

| 能力 | 说明 |
| --- | --- |
| 🎙️ 语音对话 | 唤醒词（端侧 KWS）→ 流式 ASR → LLM 对话 → TTS 播报，支持打断（barge-in） |
| 🍳 做饭引导 | 报菜名 → 后端本地菜谱库检索 → 份数确认 → 逐步骤引导，状态机驱动，可语音确认 / 跳过 / 退出 |
| 🔥 视觉安全 | 摄像头帧送 VLM 分析，识别**明火 / 烟雾 / 油烟**，多帧时序聚合防误报，危险时弹窗 + 语音告警 |
| 🌡️ 传感器 | 红外测温（MLX90640）、称重（HX711）、油温监测（超温提醒） |
| 🔧 硬件控制 | 电磁阀下料（按克数自动投放）、排气扇、继电器 |
| 🤖 多模型接入 | ModelScope / DashScope / DeepSeek / 智谱 GLM / 商汤 SenseNova / SiliconFlow，可自定义 OpenAI 兼容端点 |
| 🧰 MCP 工具 | 把温度 / 重量 / 下料 / 风扇 / 菜谱封装成 MCP Server，供任意 MCP 客户端调用 |

---

## 🧱 技术栈

**后端** `backend/`：Python 3.12 · FastAPI 0.115 · Uvicorn · Pydantic 2 · OpenCV · WebSocket
**前端** `frontend/`：React 18 · TypeScript 5 · Vite 5 · react-markdown / KaTeX
**边缘端** `边缘端/`：香橙派 RK3588（ARM64 Linux）· systemd · Docker · PyTorch/ONNX

---

## 📁 目录结构

```
backend/                FastAPI 后端（端口 8010，入口 app.main:app）
  app/
    main.py             HTTP 接口 + WebSocket 入口
    config.py           全部环境变量配置（pydantic Settings）
    ws/                 WebSocket 分发、对话、音频、视觉、TTS 处理
    ws_handlers/        做饭 / 控制 / 配置 / 计时器等业务处理
    providers/          模型供应商适配层（VLM / STT / TTS）
    context_engine/     对话历史压缩与向量召回
    services/           菜谱库检索
    prompts/            提示词模板
  tests/                pytest 测试集
  .env.example          后端配置模板（密钥键全部留空）
frontend/               主前端（React + Vite，端口 5179）
  src/
    hooks/              useChatEngine / useVoiceOrchestration / useTTS / useModelConfig …
    components/         ChatPane / VideoPane / CookingStepsPanel / DangerAlarmOverlay …
    lib/                ws.ts / audio-playback.ts / wakeword-stt.ts / voice/
  .env.example          前端配置模板
边缘端/                  香橙派侧程序（部署到 Pi）
  香橙派摄像头/          推流 + 红外 + 称重/电磁阀客户端，systemd 单元
  香橙派TTS/            端侧语音合成服务
  温度测量/ 重量获取/ 电磁阀/   硬件标定与验证工具
  start_edge_services.sh        Pi 侧幂等拉起脚本
  香橙派一件启动.py              Windows 侧一键上传并拉起
buzhou/HowToCook/       内置菜谱库（Markdown，按菜系分类）
data/                   食物成分表、会话闸门状态
docs/                   架构说明等文档
start_main.py           一键启动后端 8010 + 主前端 5179
start_copies.py         一键启动副本前端 5180-5184（需自行补充副本目录）
kill_servers.py         强杀全部服务端口
check_demo_ready.py     演示前环境体检
```

---

## 🚀 快速开始

### 1. 环境要求

- **Python 3.12**
- **Node.js 18+**（含 npm）
- Windows 10/11（本项目在 Windows 主场开发；后端代码本身跨平台）

### 2. 安装依赖

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

> 说明：`python-miio` 不要直接 `pip install`（其依赖 `netifaces` 在 Python 3.12 无预编译 wheel），
> 详见 `backend/requirements.txt` 内注释。

### 3. 配置（**必读**）

```bash
# 后端：复制模板后填入你自己的 API Key
cp backend/.env.example backend/.env.local

# 前端：可选，不配也能跑（走内置默认值）
cp frontend/.env.example frontend/.env
```

- `.env.local` / `.env` 已被 `.gitignore` 排除，**不会被提交**。
- 模板中所有密钥键均为空或 `sk-your-xxx-key` 占位符，**请填入你自己的 Key**。
- 前端**不保存任何模型密钥**：所有 provider 的 Key 以**后端** `backend/.env.local` 为权威，
  由后端 `GET /config` 下发。

### 4. 启动

**方式 A：一键启动（推荐，Windows）**

```bash
python start_main.py          # 后端 8010 + 主前端 5179，自动清端口、探活
```

**方式 B：分别启动**

```bash
# 后端（在 backend/ 目录下）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload --reload-dir app

# 前端（在 frontend/ 目录下）
npm run dev
```

打开 **http://localhost:5179** 即可使用。健康检查：`curl http://127.0.0.1:8010/health`

---

## 🔌 端口一览

| 组件 | 端口 | 说明 |
| --- | --- | --- |
| 后端 API / WebSocket | **8010** | `/ws` 为主对话通道；`/health` 探活 |
| 主前端 | **5179** | Vite Dev Server |
| 副本前端 | 5180–5184 | 分步演示用（本仓库不含，见 `start_copies.py`） |
| 边缘 KWS | 8767 | 唤醒词，前端直连 `ws://<pi>:8767/kws` |
| 边缘流式 ASR | 8768 | 前端直连 `ws://<pi>:8768/asr` |
| 边缘 TTS | 8769 | 端侧语音合成 |

---

## 🔁 通信协议

### WebSocket（`/ws`）

前端发送 JSON 事件，`type` 字段决定路由：

| type | 作用 |
| --- | --- |
| `hello` | 会话初始化，携带前端当前模型/语音配置 |
| `user_text` | 文本输入（等效语音输入，便于调试） |
| `audio_chunk` / `audio_end` | 整段录音上传与结束 |
| `audio_stream` / `audio_stream_end` | 流式音频上行（实时转写） |
| `video_frame` | 单帧图像（Base64）送视觉分析 |
| `control` | 设备控制（风扇 / 下料 / 继电器等） |
| `timer` | 倒计时操作 |
| `interrupt` | 打断当前播报 |

后端下行事件包括 `hello_ack`、`text`、`text_delta`、`tts_audio`、`cooking_status`、
`danger_detected`、`timer_done`、`servings_calc_*`、`weight_data`、`temperature_data` 等。

### HTTP（节选）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/config` | 读取当前配置（含 api_keys 状态） |
| GET | `/providers/catalog` | 供应商与模型目录 |
| POST | `/models/validate` | 模型可用性实测校验 |
| GET | `/temperature` · `/weight` | 传感器读数 |
| POST | `/weight/tare` | 去皮 |
| POST | `/dispense/start` · `/dispense/pulse` · `/dispense/stop` | 下料控制 |
| GET | `/fan/status` · POST `/fan/on` · `/fan/off` | 排气扇 |
| GET | `/api/recipes/search` | 菜谱搜索 |
| GET | `/api/recipes/{name}` | 菜谱详情 |
| GET | `/usb-camera/stream` | MJPEG 画面流 |
| POST | `/proxy/vlm` | VLM 调用代理 |

完整列表见 `backend/app/main.py`。

---

## 🍳 做饭状态机（简述）

```
IDLE → COLLECTING_INGREDIENTS / RECOMMENDING → AWAITING_SERVINGS
     → COOKING / STEP_WAITING → FINISHED
```

- 用户报菜名 → 后端**本地菜谱库**检索（同义词归一 + 模糊匹配）→ 确认 → 计算份数 → 逐步引导
- 确认词分「整句相等」与「子串」两级匹配，避免无关语句误跳步
- 关键词未命中时可由轻量 LLM 做意图分类兜底（`AI_INTENT_FALLBACK_ENABLED`）

细节见 [docs/架构说明.md](docs/架构说明.md)。

---

## 🥧 边缘端（香橙派）部署

```bash
python 边缘端/香橙派一件启动.py            # 一键上传并幂等拉起 Pi 上全部服务
python 边缘端/香橙派一件启动.py status     # 只看状态不拉起
```

Pi 侧运行 4 个 systemd 服务（`opi-camera` / `opi-ir` / `opi-sensors` / `tts-sambert`）
与 2 个 Docker 容器（KWS / ASR）。串口设备按「应答特征」自动分类，不依赖固定的 `ttyUSB` 编号。

---

## 🧰 MCP 工具

后端可被封装为 MCP Server，把硬件能力开放给任意 MCP 客户端：

- Server 名称：`gs-kitchen-hardware`
- 传输方式：stdio
- 服务定义：`backend/mcp_server.py`
- 客户端配置示例见 `MCP使用说明.md`

---

## 📚 文档

- [架构说明](docs/架构说明.md) — 分层设计、消息链路、状态机、扩展点
- [部署说明](部署说明.md) — 打包与部署流程
- [MCP 使用说明](MCP使用说明.md) — MCP Server 挂载方法

---

## ⚠️ 注意事项

1. **密钥安全**：`.env.local` 与各 `.env` 含真实密钥，已被 `.gitignore` 排除；请勿用 `git add -f` 强制添加。
2. **模型图片尺寸**：部分平台对图片输入有 2048×2048 硬限制，带图请求会先等比缩放再上送。
3. **配置回写**：前端设置页会经 `/config` 把模型选择回写到后端 `.env.local`，修改配置建议按「停后端 → 刷新页面 → 改配置 → 启后端」顺序操作。
4. **演示材料不在本仓库**：答辩讲稿、PPT、演示脚本等仅本地保留。

---

## 📄 许可

本项目为竞赛 / 课程实践项目，代码仅供学习与交流参考。
内置菜谱数据来自开源项目 [HowToCook](https://github.com/Anduin2017/HowToCook)，遵循其原始许可。
