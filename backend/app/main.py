from pathlib import Path
import re
import time
import ipaddress
import urllib.parse
from typing import Dict, List, Optional
import logging

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import httpx

from .chat_storage import clear_messages, list_sessions, load_messages, rename_session, save_messages
from .log_api import router as log_api_router
from .providers.registry import PROVIDER_REGISTRY
from .ws import get_tts_model, get_tts_voice, ws_loop
from .temperature import get_temperature, start_temperature_monitoring, stop_temperature_monitoring
from .weight import get_weight, start_weight_monitoring, stop_weight_monitoring, tare_weight, relay_control
from .dispense import start_dispense, stop_dispense, get_dispense_status, pulse_dispense
from .fan import fan_status, fan_on, fan_off
from .voice_mode import voice_continuous_start, voice_continuous_stop, voice_continuous_status
from .services.recipe_service import get_recipe_service

app = FastAPI()


class AccessLogFilter(logging.Filter):
    # 高频轮询端点不写入访问日志，避免刷屏
    QUIET_PATHS = (
        "/temperature", "/weight", "/fan",
        "/glasses-frame/latest", "/glasses-frame/stream",
        "/glasses-audio/status",
        "/usb-camera/status", "/usb-camera/latest", "/usb-camera/stream",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in self.QUIET_PATHS)


uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(AccessLogFilter())

app.include_router(log_api_router, prefix="", tags=["log"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://localhost:5173",
        "http://127.0.0.1:5173",
        "https://127.0.0.1:5173",
        "http://localhost:5174",
        "https://localhost:5174",
        "http://127.0.0.1:5174",
        "https://127.0.0.1:5174",
        "file://",
        "app://",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_ENV_KEYS = {
    "AUDIO_DEVICE_ID",
    "AUDIO_OUTPUT_DEVICE_ID",
    "CAMERA_DEVICE_ID",
    "CAMERA_SOURCE_MODE",
    "CAMERA_ROTATION",
}


class MessagesRequest(BaseModel):
    messages: List[Dict]
    title: Optional[str] = None


class SessionRenameRequest(BaseModel):
    title: str


class EnvUpdateRequest(BaseModel):
    key: str
    value: str


@app.on_event("startup")
async def start_oil_overtemp_monitor():
    """应用启动即开启油温超温监控（200°C 上限，全局常驻，与是否盯油温无关）"""
    from .oil_watch import start_overtemp_monitor

    start_overtemp_monitor()


@app.on_event("startup")
async def start_usb_camera_proxy():
    """应用启动即常驻启动 USB 摄像头代理：懒启动（等前端首次请求流才探测）会让
    页面首开白等数秒——探测期需逐设备开摄像头、空读等曝光、测帧率，实测约 5s；
    常驻启动后帧持续就绪，前端打开即出画面。
    捕获在守护线程完成、异常只记日志——摄像头任何问题都不允许阻塞应用启动。"""
    from .usb_camera import usb_cam

    try:
        usb_cam.start()
    except Exception as e:
        logging.error(f"[启动] USB 摄像头代理启动失败（不影响其他功能）: {e}")


@app.on_event("shutdown")
async def close_remote_camera_streams():
    """应用关闭（含 --reload 热重载）时主动断开香橙派推流长连 WS：
    否则优雅关闭会一直等连接断开（"Waiting for connections to close"），拖死重启。"""
    try:
        from .remote_camera import close_active_streams
        from .remote_ir import close_active_ws

        await close_active_streams()
        await close_active_ws()
    except Exception as e:
        logging.error(f"[关闭] 断开远程摄像头/红外推流连接失败: {e}")


@app.on_event("startup")
async def prewarm_mcp_client():
    """启动即后台预热 MCP 客户端（常驻 stdio 子进程跑 mcp_server.py）：
    大模型下料工具 dispense_start/stop 经真实 MCP 协议执行，预热后首次调用零冷启动。
    后台跑不阻塞启动；失败只记日志，首次真实调用时自动重试。"""
    import asyncio

    from . import mcp_client

    if not mcp_client.ENABLED:
        return
    asyncio.create_task(mcp_client.prewarm())


@app.on_event("startup")
async def start_edge_voice_discovery():
    """应用启动即后台预热端侧语音服务（KWS/ASR）地址发现。

    香橙派 IP 由 DHCP 分配会漂移，前端写死地址易失效；这里在后端侧扫描局域网
    并缓存，前端经 GET /edge/voice 取用。用 create_task 后台跑，不阻塞启动
    （全网段扫描约 1~2 秒，且预热未完成也不影响接口按需现扫）。
    """
    import asyncio

    from .edge_discovery import background_refresh

    asyncio.create_task(background_refresh())


@app.get("/edge/voice")
async def edge_voice(force: str = "0"):
    """返回端侧语音服务（KWS/ASR）地址；未找到时 found=false，前端保留原配置"""
    from .edge_discovery import discover

    return await discover(force=(force not in ("0", "", "false", "False")))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/config")
def get_config():
    import os

    from .config import settings

    return {
        "backend_port": settings.backend_port,
        "camera_source_mode": settings.camera_source_mode,
        # 画面旋转角度（后端源头转正，"旋转画面"按钮经 /env/update 持久化）
        "camera_rotation": settings.camera_rotation,
        "stt_provider": settings.stt_provider,
        # 前端保存过的 STT 模型已持久化到 STT_MODEL（.env.local），优先返回（set 时 reload_settings
        # 已同步运行中 settings）；未持久化时按 provider 给默认描述，保持向后兼容
        "stt_model": (
            os.getenv("STT_MODEL")
            or (
                "FunASR 流式（香橙派端侧）" if settings.stt_provider == "edge"
                else settings.siliconflow_stt_model if settings.stt_provider == "siliconflow"
                else "Web Speech API"
            )
        ),
        "tts_provider": settings.tts_provider,
        "tts_model": get_tts_model(settings.tts_provider),
        "tts_voice": get_tts_voice(settings.tts_provider),
        "vision_enabled": settings.vision_enabled,
        "vision_fps": settings.vision_fps,
        "vlm_provider": settings.vlm_provider,
        "vlm_model": _resolve_model_name(settings, settings.vlm_provider),
        "api_keys": {
            spec.name: (getattr(settings, spec.key_env.lower(), None) or "")
            for spec in PROVIDER_REGISTRY.values()
        },
        "custom_providers": settings.custom_providers or {},
    }



def _resolve_model_name(settings, provider: str) -> str:
    """根据 provider 动态解析对应的 VLM 模型配置名"""
    field_name = f"{provider}_vlm_model"
    name = getattr(settings, field_name, None)
    if name:
        return name
    return "unknown"


# 以下三张表全部由 provider 注册表（providers/registry.py）派生——单一数据源，
# 新增供应商只需在注册表加一条 ProviderSpec，这里零改动。

# 系统预设模型列表（支持 VLM 的 provider，远程目录拉取失败时的回退）
PRESET_MODELS: dict[str, list[str]] = {
    name: list(spec.preset_models)
    for name, spec in PROVIDER_REGISTRY.items()
    if spec.supports_vlm and spec.preset_models
}


# 各 provider 默认 base_url（OpenAI 兼容接口）
DEFAULT_PROVIDER_BASE_URL: dict[str, str] = {
    name: spec.base_url for name, spec in PROVIDER_REGISTRY.items()
}

# 允许的模型端点域名白名单（防止 SSRF 探测内网）
ALLOWED_MODEL_HOSTS: tuple[str, ...] = tuple(dict.fromkeys(
    spec.allowed_host for spec in PROVIDER_REGISTRY.values() if spec.allowed_host
))


def _is_safe_models_url(url: str) -> bool:
    """防止 SSRF：仅允许 https，且目标 host 非内网/环回地址或已知白名单域名"""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.hostname
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            # 域名：按后缀白名单匹配
            if not any(host == d or host.endswith("." + d) for d in ALLOWED_MODEL_HOSTS):
                return False
        return True
    except Exception:
        return False


async def _fetch_models_from_provider(
    provider: str,
    api_key: str,
    base_url: str,
    api_format: str,
) -> tuple[list[str], str]:
    """从 provider 的 /v1/models 端点拉取模型列表。

    返回 (models, source)，source 为 "fetched" 表示成功拉取，"preset" 表示回退到预设列表。
    """
    effective_base_url = base_url.rstrip("/") if base_url else DEFAULT_PROVIDER_BASE_URL.get(provider, "")
    if not effective_base_url:
        return PRESET_MODELS.get(provider, []), "preset"
    if not _is_safe_models_url(effective_base_url):
        logging.warning(f"[/models] 拒绝非白名单 base_url: {effective_base_url}")
        return PRESET_MODELS.get(provider, []), "preset"

    headers: dict[str, str] = {}
    if api_key:
        if api_format == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{effective_base_url}/models", headers=headers)
            if response.status_code != 200:
                logging.warning(f"[/models] {provider} 拉取失败: {response.status_code}")
                return PRESET_MODELS.get(provider, []), "preset"

            data = response.json()
            models: list[str] = []
            candidates = data.get("data") or data.get("models") or data
            if isinstance(candidates, list):
                for item in candidates:
                    if isinstance(item, str):
                        models.append(item)
                    elif isinstance(item, dict) and item.get("id"):
                        models.append(str(item["id"]))

            if models:
                return models, "fetched"
    except Exception as e:
        logging.warning(f"[/models] 拉取 {provider} 模型列表失败: {e}")

    return PRESET_MODELS.get(provider, []), "preset"


@app.get("/providers/catalog")
def providers_catalog():
    """Provider 目录（由注册表单一来源派生）。

    前端下拉（VLM/STT/TTS 的 provider 与默认模型）从这里自动读取，
    后端改默认模型/新增供应商时前端零改动。只读无副作用。
    """
    from .providers.registry import STT_CATALOG, TTS_REGISTRY

    return {
        # VLM 供应商（supports_vlm 条目，custom provider 由前端自行追加）
        "providers": [
            {
                "name": spec.name,
                "display_name": spec.display_name or spec.name,
                "supports_vlm": spec.supports_vlm,
                "base_url": spec.base_url,
                "default_model": spec.default_model,
                "preset_models": list(spec.preset_models),
                "allowed_host": spec.allowed_host,
            }
            for spec in PROVIDER_REGISTRY.values()
        ],
        # STT 供应商（默认模型/展示文案）
        "stt": [
            {"name": e.name, "display_name": e.display_name,
             "model": e.model, "has_api_key": e.has_api_key}
            for e in STT_CATALOG
        ],
        # TTS 供应商（TTS_REGISTRY + browser 特例）
        "tts": [
            {"name": "browser", "display_name": "浏览器内置",
             "model": "SpeechSynthesis API", "has_api_key": False},
            *[
                {"name": spec.name, "display_name": spec.display_name or spec.name,
                 "model": spec.default_model, "has_api_key": bool(spec.key_env)}
                for spec in TTS_REGISTRY.values()
            ],
        ],
    }


@app.get("/models")
async def list_models(
    provider: str = "",
    api_key: str = "",
    base_url: str = "",
    api_format: str = "openai",
):
    """获取指定 provider 的可用模型列表。

    - 优先从 provider 的 /v1/models 端点拉取。
    - 拉取失败或 provider 不支持时，返回系统预设列表。
    - 自定义 provider 通过 provider=custom 并传入 base_url/api_format/api_key。
    """
    effective_provider = provider or "custom"
    models, source = await _fetch_models_from_provider(
        effective_provider,
        api_key,
        base_url,
        api_format,
    )
    return {"provider": effective_provider, "models": models, "source": source}


@app.post("/models/validate")
async def validate_model(request: Request):
    """端点实测：用一张 16x16 测试图调用指定模型，验证其可对话且可识图。

    通过 → 该模型允许进入前端模型列表；失败 → 返回错误原因（前端排除并缓存）。
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "请求体不是合法 JSON"}

    provider = (body.get("provider") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    if not provider or not model:
        return {"ok": False, "error": "缺少 provider 或 model"}

    effective_base_url = base_url.rstrip("/") if base_url else DEFAULT_PROVIDER_BASE_URL.get(provider, "")
    if base_url and not _is_safe_models_url(effective_base_url):
        return {"ok": False, "error": "base_url 不在允许的域名白名单内"}

    # 生成 16x16 测试图（白色小图，足够触发视觉链路）
    try:
        import base64
        import asyncio

        import cv2
        import numpy as np

        img = np.full((16, 16, 3), 235, dtype=np.uint8)
        ok_enc, buf = cv2.imencode(".jpg", img)
        if not ok_enc:
            return {"ok": False, "error": "生成测试图失败"}
        jpeg_bytes = buf.tobytes()
    except Exception as exc:
        return {"ok": False, "error": f"生成测试图失败: {exc}"}

    from .runtime_providers import build_vlm_runtime

    try:
        vlm = build_vlm_runtime(provider=provider, api_key=api_key, model=model, base_url=base_url or None)
    except Exception as exc:
        return {"ok": False, "error": f"构建 VLM 运行时失败: {exc}"}
    if vlm is None or not hasattr(vlm, "analyze_image"):
        return {"ok": False, "error": f"provider {provider} 不支持视觉调用"}

    async def _run():
        return await vlm.analyze_image(jpeg_bytes, "端点校验测试：请只回复两个字母 OK")

    try:
        reply = await asyncio.wait_for(_run(), timeout=30.0)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "校验超时（30 秒无响应）"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    reply_text = (reply or "").strip()
    # provider 层错误约定：失败/繁忙信息被包裹在全角括号里
    if not reply_text or reply_text.startswith("（") or "失败" in reply_text or "繁忙" in reply_text:
        return {"ok": False, "error": reply_text or "空回复"}

    return {"ok": True, "reply": reply_text[:120]}


@app.post("/env/update")
def update_env(request: Request, body: EnvUpdateRequest):
    client_host = request.client.host if request.client else ""
    # 允许本地地址和局域网地址
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}
    is_local = client_host in allowed_hosts
    is_lan = (
        client_host.startswith("192.168.") or
        client_host.startswith("10.") or
        client_host.startswith("172.") and any(
            client_host.startswith(f"172.{i}.") for i in range(16, 32)
        ) or
        client_host.startswith("::ffff:192.168.") or
        client_host.startswith("::ffff:10.") or
        client_host.startswith("::ffff:172.")
    )
    if not (is_local or is_lan):
        raise HTTPException(status_code=403, detail="Forbidden")

    if body.key not in ALLOWED_ENV_KEYS:
        raise HTTPException(status_code=400, detail=f"Unsupported env key: {body.key}")

    if "\n" in body.value or "\r" in body.value:
        raise HTTPException(status_code=400, detail="Invalid env value")

    try:
        env_path = Path(__file__).parent.parent / ".env.local"
        if not env_path.exists():
            return {"success": False, "error": ".env.local file not found"}

        from .config import persist_env_value, reload_settings

        persist_env_value(body.key, body.value)
        reload_settings()

        return {"success": True, "message": f"Updated {body.key}"}
    except Exception as e:
        logging.error(f"更新环境变量失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # 从 query 读取会话 ID，用于按会话恢复 AI 记忆（非法值回退 default）
    session_id = _validate_session_id(ws.query_params.get("session_id", "default"))
    await ws_loop(ws, session_id)


@app.post("/glasses-frame")
async def receive_glasses_frame(body: dict):
    import time
    from .ws import glasses_handler
    jpeg_b64 = body.get("jpeg_b64", "")
    if jpeg_b64:
        glasses_handler.latest_glasses_frame = jpeg_b64
        glasses_handler.latest_glasses_frame_time = time.time()
        return {"success": True}
    return {"success": False, "error": "no jpeg_b64"}


@app.get("/glasses-frame/latest")
async def get_glasses_frame():
    from .ws.glasses_handler import get_latest_frame, has_glasses_connected
    return {
        "frame": get_latest_frame(),
        "connected": has_glasses_connected(),
    }


@app.get("/glasses-frame/stream")
async def stream_glasses_frame():
    """MJPEG 流端点，用于前端显示眼镜摄像头画面"""
    from .ws.glasses_handler import latest_glasses_frame
    from fastapi.responses import StreamingResponse
    import asyncio

    async def frame_generator():
        last_frame = None
        while True:
            current_frame = latest_glasses_frame
            if current_frame and current_frame != last_frame:
                last_frame = current_frame
                import base64
                try:
                    image_bytes = base64.b64decode(current_frame)
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(image_bytes)).encode() + b"\r\n\r\n"
                        + image_bytes + b"\r\n"
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.1)

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/glasses-cam")
async def glasses_camera_endpoint(ws: WebSocket):
    from .ws.glasses_handler import handle_glasses_ws
    await handle_glasses_ws(ws)


@app.websocket("/ws/opi-cam")
async def opi_camera_endpoint(ws: WebSocket):
    """香橙派采集端推流入口：USB 摄像头接在香橙派上抓帧，经本 WS 推到后端。

    remote_camera.remote_feed 保鲜期内 usb_cam 公开视图自动切换为远程帧，
    前端 MJPEG 取流/拍照/视觉分析零改动跟随；断流超时自动回退本地捕获。
    """
    from .remote_camera import handle_opi_camera_ws
    await handle_opi_camera_ws(ws)


@app.websocket("/ws/opi-ir")
async def opi_ir_endpoint(ws: WebSocket):
    """香橙派红外测温推流入口：MLX90640 接在香橙派串口上，原始帧推到后端。

    remote_ir.process_ir_frame 复用 temperature.reader 解析入库，
    /temperature、温度快答、油温盯守零改动跟随远程数据。
    """
    from .remote_ir import handle_opi_ir_ws
    await handle_opi_ir_ws(ws)


@app.websocket("/ws/opi-sensors")
async def opi_sensors_endpoint(ws: WebSocket):
    """香橙派外设通道（2026-09-12 硬件迁移：称重板卡+电磁阀移到 Pi）：

    上行为称重板卡串口原始文本行（weight.process_remote_line 复用本地解析管线，
    写 opi 源槽；MLX90640 的温度 opi 槽 fresh 优先，行内温度字段仅兜底）；
    下行为电磁阀控制命令（valve.py 检测到本通道在线即转发，Pi 端执行并异步回报）。
    """
    from .remote_sensors import handle_opi_sensors_ws
    await handle_opi_sensors_ws(ws)


# ===================== USB 摄像头代理（多前端共享） =====================
@app.post("/usb-camera/start")
async def usb_camera_start():
    from .usb_camera import usb_cam
    usb_cam.start()
    return usb_cam.status()


@app.post("/usb-camera/stop")
async def usb_camera_stop():
    from .usb_camera import usb_cam
    usb_cam.stop()
    return usb_cam.status()


@app.get("/usb-camera/status")
async def usb_camera_status():
    from .usb_camera import usb_cam
    return usb_cam.status()


@app.get("/usb-camera/latest")
async def usb_camera_latest(max_w: int = 0, quality: int = 0):
    """取当前最新帧（base64 JPEG）。

    - `max_w`>0：把长边缩到该值并重编码（放视觉注入用）——2K 原图 base64 后约 450KB，
      每次发消息都带上会同时挤占前端主线程与 WS 带宽，把 MJPEG 流也拖卡（2026-09-16 实测）。
    - `quality`>0：配合 max_w 指定重编码质量（默认 80）。
    不传参数时保持原行为（原图直出），MJPEG 流端点不受影响。
    """
    import asyncio
    import base64

    from .usb_camera import usb_cam

    frame = usb_cam.latest_jpeg_b64
    # 本机方向偏好：出帧时按 CAMERA_ROTATION 转正（0.2fps 级低频，成本可忽略）；
    # MJPEG 流保持原始帧直通，显示旋转由前端 CSS 做。
    # ⚠️ 旋转/缩放是同步 CPU 活（2K→720p 用 LANCZOS 要 200ms+），**必须丢线程池**，
    #    否则会卡住事件循环、连 MJPEG 流一起顿（2026-09-16 实测）。
    if frame:
        src_b64 = frame

        def _rotate_encode() -> bytes:
            from PIL import Image

            from .camera_rotate import rotation_angle, rotate_jpeg_bytes
            from .providers.base import downscale_jpeg_bytes

            raw = rotate_jpeg_bytes(base64.b64decode(src_b64), rotation_angle())
            if raw is None:
                raw = base64.b64decode(src_b64)
            if max_w and max_w > 0:
                # BILINEAR：抓帧出图不追求画质，比 LANCZOS 快 2~3 倍
                raw = downscale_jpeg_bytes(
                    raw, max_side=int(max_w), quality=int(quality or 80), resample=Image.BILINEAR
                )
            return raw

        try:
            raw = await asyncio.to_thread(_rotate_encode)
        except Exception:
            raw = base64.b64decode(src_b64)   # 失败退回原始帧，不影响取帧
        frame = base64.b64encode(raw).decode("ascii")
    return {
        "frame": frame,
        "connected": usb_cam.is_connected,
        "running": usb_cam.is_running,
        "frame_count": usb_cam.frame_count,
        "timestamp": usb_cam.latest_timestamp,
    }


@app.get("/usb-camera/stream")
async def stream_usb_camera():
    """MJPEG 流端点：后端常驻捕获的 USB 摄像头画面，任意前端可共享"""
    from fastapi.responses import StreamingResponse
    import asyncio
    from .usb_camera import usb_cam

    usb_cam.start()

    async def frame_generator():
        last = None
        last_push_ts = 0.0
        while True:
            try:
                current = usb_cam.latest_bytes
                now = time.monotonic()
                # 内容变化立即推；静止画面也按至少 1fps 心跳推送——
                # Chromium 渲染 multipart 图片需要下一帧的边界分隔符才会画出上一帧，
                # 只推一帧的流会导致浏览器永远显示"连接中"
                if current and (current != last or now - last_push_ts > 1.0):
                    last = current
                    last_push_ts = now
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(current)).encode() + b"\r\n\r\n"
                        + current + b"\r\n"
                    )
                # 客户断开 Exception 由 StreamingResponse 统一捕获并结束
                # 轮询间隔 10ms（100Hz）：远程源提到 30-60fps 后，50ms 轮询会把
                # 转发层卡在 ~20fps，源帧率再高也出不来
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/glasses-audio")
async def glasses_audio_endpoint(ws: WebSocket):
    """眼镜端音频 WebSocket：接收 PCM → STT → LLM → TTS → 推送 PCM 回眼镜端"""
    from .ws.glasses_audio_handler import handle_glasses_audio_ws
    await handle_glasses_audio_ws(ws)


@app.get("/glasses-audio/status")
async def glasses_audio_status():
    """前端轮询眼镜音频连接状态"""
    from .ws.glasses_audio_handler import is_glasses_audio_connected
    return {"connected": is_glasses_audio_connected()}


@app.get("/chat/history")
def get_chat_history(q: Optional[str] = None):
    sessions = list_sessions(q)
    return {"sessions": sessions}


def _validate_session_id(session_id: str) -> str:
    """校验 session_id 格式，非法时返回 'default'"""
    if session_id and re.fullmatch(r'[a-zA-Z0-9_-]+', session_id):
        return session_id
    return "default"


@app.get("/chat/history/{session_id}")
def get_chat_session(session_id: str = "default"):
    safe_id = _validate_session_id(session_id)
    messages = load_messages(safe_id)
    return {"session_id": safe_id, "messages": messages}


@app.post("/chat/history/{session_id}")
def save_chat_session(session_id: str, request: MessagesRequest):
    safe_id = _validate_session_id(session_id)
    success = save_messages(request.messages, safe_id, request.title)
    return {"success": success, "session_id": safe_id}


@app.patch("/chat/history/{session_id}/rename")
def rename_chat_session(session_id: str, request: SessionRenameRequest):
    safe_id = _validate_session_id(session_id)
    success = rename_session(safe_id, request.title)
    return {"success": success, "session_id": safe_id, "title": request.title}


@app.delete("/chat/history/{session_id}")
def delete_chat_session(session_id: str = "default"):
    safe_id = _validate_session_id(session_id)
    success = clear_messages(safe_id)
    return {"success": success}


@app.get("/fan/status")
def get_fan_status():
    return fan_status()


@app.post("/fan/on")
def post_fan_on():
    return fan_on()


@app.post("/fan/off")
def post_fan_off():
    return fan_off()


# ===== 语音模式（持续识别）远程控制：向前端 WS 广播 voice_mode 指令 =====

@app.post("/voice/continuous/start")
async def post_voice_continuous_start():
    return await voice_continuous_start(source="api")


@app.post("/voice/continuous/stop")
async def post_voice_continuous_stop():
    return await voice_continuous_stop(source="api")


@app.get("/voice/continuous/status")
def get_voice_continuous_status():
    return voice_continuous_status()


@app.get("/temperature")  #先建立一个get名
def get_temperature_data():  #然后定义返回温度数据的方法
    return get_temperature()

@app.post("/temperature/stop")
def stop_temperature():
    return stop_temperature_monitoring()


@app.get("/weight")
def get_weight_data():
    return get_weight()



@app.post("/weight/tare")
async def weight_tare():
    """手动去皮：以当前读数为新零点（秤面需为空）"""
    return tare_weight()

@app.post("/weight/start")
def start_weight():
    return start_weight_monitoring()


@app.post("/weight/stop")
def stop_weight():
    return stop_weight_monitoring()


class DispenseRequest(BaseModel):
    target_g: float


@app.post("/relay/on")
def relay_on():
    """继电器吸合 '1'：NC 断开 = 下料机断电（停止）"""
    return relay_control("on")


@app.post("/relay/off")
def relay_off():
    """继电器断开 '0'：NC 导通 = 下料机通电（下料）"""
    return relay_control("off")


@app.post("/relay/pulse")
def relay_pulse():
    """继电器脉冲 '3'：断开50ms后回弹吸合（点动，量太小不推荐用于下料）"""
    return relay_control("pulse")


@app.post("/dispense/start")
async def dispense_start(req: DispenseRequest):
    """启动下料：目标 = 当前重量 + target_g（克），自动监测达标后关闭下料机"""
    return await start_dispense(req.target_g)


@app.post("/dispense/pulse")
async def dispense_pulse(seconds: float = 3.0):
    """定时脉冲出料：通电 `seconds` 秒后自动断电（放盐/放调料的定时出料用）。

    与 /dispense/start 的区别：不走向称重达标闭环，按固定时长出料。
    2026-09-16 新增：供 MCP 的 dispense_start(pulse_seconds=...) 与放盐流程调用。
    """
    return await pulse_dispense(seconds)


@app.post("/dispense/stop")
async def dispense_stop():
    """手动停止下料：立即关闭下料机"""
    return await stop_dispense()


@app.get("/dispense/status")
def dispense_status():
    """查询下料状态"""
    return get_dispense_status()


# 电磁阀通道（2026-09-15 双阀：香橙派侧现场有 2 个下料阀，通道 1=电磁阀1、2=电磁阀2）


@app.get("/valve/status")
def valve_status(channel: int = 0, refresh: bool = False):
    """查询电磁阀通道：Pi 上报的通道清单 + 各通道最近一次执行结果。

    - channel: >0 只看该通道；0（默认）看全部
    - refresh=true 顺带请 Pi 重新回报一次通道清单（异步，下次查询可见）
    """
    from . import remote_sensors

    if refresh:
        remote_sensors.request_valve_list()
    stats = remote_sensors.stats()
    channels = stats.get("valve_channels") or []
    events = stats.get("valve_events") or {}
    if channel > 0:
        channels = [c for c in channels if int(c.get("channel", 0)) == channel]
        events = {k: v for k, v in events.items() if k == str(channel)}
    return {
        "ok": True,
        "pi_connected": remote_sensors.is_connected(),
        "channels": channels,
        "last_events": events,
        "commands_sent": stats.get("commands_sent", 0),
    }


@app.post("/valve/open")
def valve_open_channel(channel: int = 1):
    """开指定电磁阀通道（默认 1 = 电磁阀1）。执行结果由 Pi 异步回报，用 /valve/status 查。"""
    from . import valve as valve_mod

    if not valve_mod.valve_is_ready():
        return {"ok": False, "error": "valve_not_ready"}
    ok = valve_mod.valve_open(channel=channel)
    return {"ok": bool(ok), "channel": channel, "action": "open"}


@app.post("/valve/close")
def valve_close_channel(channel: int = 0):
    """关电磁阀通道。channel=0（默认）= 关闭**全部**通道（安全兜底，防多阀漏关遗留通电）。"""
    from . import valve as valve_mod

    ok = valve_mod.valve_close(channel=channel)
    return {"ok": bool(ok), "channel": channel, "action": "close"}


# 菜谱相关 API


@app.get("/api/recipes/search")
def search_recipes(ingredients: str = ""):
    """搜索菜谱
    - ingredients: 逗号分隔的食材列表，如 "土豆,鸡蛋"，为空时返回所有菜谱
    """
    try:
        service = get_recipe_service()
        # 解析食材列表
        ingredient_list = [i.strip() for i in ingredients.split(",") if i.strip()]

        if not ingredient_list:
            # 没有提供食材时，返回所有菜谱（限制50个）
            all_recipes = service.get_all_recipes()[:50]
            data = [
                {
                    "name": r.name,
                    "category": r.category,
                    "matched_ingredients": [],
                    "difficulty": r.difficulty,
                    "total_steps": None,
                }
                for r in all_recipes
            ]
            return {"success": True, "data": data}

        results = service.search_by_ingredients(ingredient_list)

        # 转换为响应格式
        data = [
            {
                "name": r.name,
                "category": r.category,
                "matched_ingredients": r.matched_ingredients,
                "difficulty": r.difficulty,
                "total_steps": None,  # 搜索结果不包含步骤数
            }
            for r in results
        ]

        return {"success": True, "data": data}
    except Exception as e:
        logging.error(f"搜索菜谱失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/recipes/{name}")
def get_recipe(name: str):
    """获取菜谱详情"""
    try:
        service = get_recipe_service()
        recipe = service.get_recipe(name)

        if not recipe:
            return {"success": False, "error": f"菜谱 '{name}' 未找到"}

        # 转换为响应格式
        data = {
            "name": recipe.name,
            "category": recipe.category,
            "difficulty": recipe.difficulty,
            "time": None,  # 菜谱数据中暂无此字段
            "ingredients": recipe.ingredients,
            "steps": [
                {
                    "order": step.step_number,
                    "title": step.title,
                    "content": step.content,
                    "tip": None,  # 步骤中暂无单独的 tip 字段
                }
                for step in recipe.steps
            ],
            "tips": recipe.tips,
        }

        return {"success": True, "data": data}
    except Exception as e:
        logging.error(f"获取菜谱详情失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.post("/api/cooking/sync")
def sync_cooking_state(data: dict):
    """同步做饭状态到后端（WebSocket方式，推荐）或HTTP轮询"""
    try:
        logging.info(f"[做饭状态同步] 收到同步请求: {data}")
        return {"success": True, "message": "状态已同步"}
    except Exception as e:
        logging.error(f"同步做饭状态失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.post("/proxy/vlm")
async def proxy_vlm(request: Request):
    """代理前端的 VLM 分析请求，复用统一模型提供组件（build_vlm_runtime）。

    按请求携带的 provider/model 路由（缺省使用后端 settings 中用户选择的视觉模型），
    不再硬编码 DashScope 地址或厂商 API Key。返回 {"content", "model", "provider"}。
    """
    import base64 as _b64

    from .runtime_providers import build_vlm_runtime

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    image_b64 = body.get("image_b64") or ""
    provider = (body.get("vlm_provider") or "").strip() or None
    model = (body.get("model") or "").strip() or None
    api_key = (body.get("api_key") or "").strip() or None
    base_url = (body.get("base_url") or "").strip() or None
    api_format = (body.get("api_format") or "").strip() or None

    if not prompt:
        return {"error": "prompt 不能为空"}, 400
    try:
        image_bytes = _b64.b64decode(image_b64) if image_b64 else None
    except Exception as e:
        return {"error": f"image_b64 解码失败: {e}"}, 400
    if image_bytes is None:
        return {"error": "缺少图像帧 image_b64"}, 400

    vlm = build_vlm_runtime(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        api_format=api_format,
    )
    if vlm is None or not hasattr(vlm, "analyze_image"):
        return {"error": "VLM 未配置或当前 provider 不可用"}, 400

    try:
        content = await vlm.analyze_image(image_bytes, prompt, enable_thinking=False)
    except Exception as e:
        return {"error": str(e)}, 502

    return {
        "content": content,
        "model": getattr(vlm, "model", None) or model or None,
        "provider": provider,
    }
