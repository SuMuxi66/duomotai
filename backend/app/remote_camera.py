"""
远程摄像头接入（香橙派采集端）

摄像头接在香橙派 RK3588 上：采集客户端抓帧、JPEG 编码后经 WebSocket 推流到后端，
本模块接收帧并写入 RemoteCameraFeed 单例。usb_camera.UsbCameraCapture 的公开视图
（latest_bytes / latest_jpeg_b64 / latest_timestamp / is_connected / status）在远程帧
保鲜期内优先返回远程帧、本地捕获线程自动挂起——前端 MJPEG 取流、拍照接口
（/usb-camera/latest）与视觉分析链路零改动跟随远程画面；远程断流超时后自动回退本地捕获。

协议（ws://host:port/ws/opi-cam）：
- 二进制帧 = 一帧完整 JPEG（SOI 魔数校验）；
- 文本帧 = JSON 控制消息：{"type":"hello",...} 仅记日志，{"type":"ping"} 回 {"type":"pong"}。

单设备演示场景：多客户端同时推流时按"最后写入者胜"覆盖，不做会话隔离。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import settings
from .logger import backend_log

# 远程帧保鲜期：超过该时长没有新帧即视为远程源失效（本地捕获自动恢复探测）。
# 客户端默认 ~10fps，6s 足够容忍网络抖动/摄像头瞬时占用，又不至于让画面冻太久。
REMOTE_FRESH_SECONDS = float(os.getenv("REMOTE_CAMERA_FRESH_SECS", "6") or 6)

# 单帧最小合法体积：低于此值或缺少 JPEG SOI 魔数的帧直接丢弃（防半包/垃圾数据）
MIN_JPEG_BYTES = 1024
JPEG_SOI = b"\xff\xd8"


class RemoteCameraFeed:
    """线程安全的远程最新帧仓：WS 接收端写入，usb_cam 视图与 MJPEG 流读取"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest_jpeg_bytes: Optional[bytes] = None
        self._latest_jpeg_b64: Optional[str] = None
        self._latest_timestamp: int = 0      # 毫秒级 epoch，随每帧更新
        self._frame_count: int = 0
        self._dropped_count: int = 0
        self._last_arrival: float = 0.0      # monotonic
        self._fps: float = 0.0               # 指数滑动平均
        self._connected: bool = False
        self._client_addr: str = ""
        self._connected_at: float = 0.0      # monotonic
        self._connect_count: int = 0

    # ---------- 写入（WS 接收端） ----------
    def update(self, jpeg_bytes: bytes) -> bool:
        """写入一帧；非法帧（过小/无 SOI）丢弃并计数，返回是否被采纳。

        不做旋转：画面方向由采集端在编码前保证（香橙派客户端按 CAMERA_ROTATION
        旋转原始帧后推流），后端对远程帧纯转发——2K@30 下"解码→旋转→重编码"
        每帧 45~70ms 会把吞吐卡死在 ~15fps。
        """
        if not jpeg_bytes or len(jpeg_bytes) < MIN_JPEG_BYTES or not jpeg_bytes.startswith(JPEG_SOI):
            with self._lock:
                self._dropped_count += 1
            return False
        now = time.monotonic()
        import base64
        jpeg_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        with self._lock:
            if self._last_arrival > 0:
                dt = now - self._last_arrival
                if dt > 0:
                    inst = 1.0 / dt
                    self._fps = inst if self._fps <= 0 else self._fps * 0.9 + inst * 0.1
            self._last_arrival = now
            self._latest_jpeg_bytes = jpeg_bytes
            self._latest_jpeg_b64 = jpeg_b64
            self._latest_timestamp = int(time.time() * 1000)
            self._frame_count += 1
        return True

    def mark_connected(self, client_addr: str) -> None:
        with self._lock:
            self._connected = True
            self._client_addr = client_addr
            self._connected_at = time.monotonic()
            self._connect_count += 1

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False
            # 保留最后一帧：靠 is_fresh 过期判定，断连后画面自然过渡而不是立刻黑屏

    # ---------- 读取（usb_cam 视图 / 状态接口） ----------
    def is_fresh(self, max_age: Optional[float] = None) -> bool:
        """最近 max_age 秒内有没有新帧（不看连接状态，只看数据新鲜度）"""
        limit = REMOTE_FRESH_SECONDS if max_age is None else max_age
        with self._lock:
            return self._last_arrival > 0 and (time.monotonic() - self._last_arrival) <= limit

    @property
    def latest_bytes(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg_bytes

    @property
    def latest_jpeg_b64(self) -> Optional[str]:
        with self._lock:
            return self._latest_jpeg_b64

    @property
    def latest_timestamp(self) -> int:
        with self._lock:
            return self._latest_timestamp

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    def status(self) -> dict:
        with self._lock:
            age = (time.monotonic() - self._last_arrival) if self._last_arrival > 0 else -1.0
            return {
                "connected": self._connected,
                "client": self._client_addr,
                "frame_count": self._frame_count,
                "dropped_count": self._dropped_count,
                "fps": round(self._fps, 1),
                "age_seconds": round(age, 1) if age >= 0 else None,
                "fresh": self._last_arrival > 0 and (time.monotonic() - self._last_arrival) <= REMOTE_FRESH_SECONDS,
                "connect_count": self._connect_count,
            }


# 模块级单例
remote_feed = RemoteCameraFeed()

# 活跃推流连接注册表：应用关闭（含 --reload 热重载）时主动断开，
# 否则长连 WS 会卡住 uvicorn 优雅关闭（"Waiting for connections to close"），拖死重启
_active_streams: set = set()
_local_reject_count = 0


async def close_active_streams() -> None:
    """主动断开全部推流连接（main.py shutdown 事件调用，不阻塞正常启动/重启）"""
    for ws in list(_active_streams):
        try:
            await ws.close()
        except Exception:
            pass
    _active_streams.clear()


async def handle_opi_camera_ws(ws: WebSocket) -> None:
    """香橙派采集端 WebSocket：循环接收二进制 JPEG 帧写入 remote_feed"""
    # USB 摄像头模式（local）下网络摄像头链路整体不启用：直接拒收，
    # Pi 端收到关闭帧自动退避重试（不占用任何帧处理资源）
    if str(settings.camera_source_mode).strip().lower() == "local":
        await ws.accept()
        await ws.close(code=1008, reason="camera source mode is local")
        global _local_reject_count
        _local_reject_count += 1
        if _local_reject_count == 1 or _local_reject_count % 30 == 0:
            backend_log(f"[远程摄像头] local 模式拒收推流连接（累计 {_local_reject_count} 次）")
        return
    await ws.accept()
    client = ws.client.host if ws.client else "unknown"
    _active_streams.add(ws)
    remote_feed.mark_connected(client)
    backend_log(f"[远程摄像头] 采集端已连接: {client}")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                # update 里大帧 b64 编码是纯 CPU 拷贝活，放线程池跑不阻塞事件循环；
                # receive 在 await 后才继续，帧顺序天然保持
                if await asyncio.to_thread(remote_feed.update, data):
                    # 每 ~150 帧（约 15s@10fps）打一次点，避免刷屏
                    if remote_feed.frame_count % 150 == 0:
                        st = remote_feed.status()
                        backend_log(
                            f"[远程摄像头] 已接收 {st['frame_count']} 帧 "
                            f"(fps≈{st['fps']}, 丢弃 {st['dropped_count']})"
                        )
                continue
            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except json.JSONDecodeError:
                    continue
                ctrl_type = ctrl.get("type", "")
                if ctrl_type == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif ctrl_type == "hello":
                    backend_log(
                        f"[远程摄像头] hello: {client} "
                        f"dev={ctrl.get('device', '?')} {ctrl.get('width', '?')}x{ctrl.get('height', '?')}"
                        f"@{ctrl.get('fps', '?')}fps"
                    )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        backend_log(f"[远程摄像头] 连接异常: {type(e).__name__}: {e}")
    finally:
        _active_streams.discard(ws)
        remote_feed.mark_disconnected()
        backend_log(f"[远程摄像头] 采集端已断开: {client}")
