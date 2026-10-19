"""
USB 摄像头代理

用 OpenCV 在后端常驻捕获本地 USB 摄像头，保存最新 JPEG 帧，
供任意多个前端通过 HTTP(MJPEG 流 / latest) 共享同一路画面，
避免多个浏览器网页 getUsermedia 抢占同一摄像头。

- 设备索引从 0 起自动轮询，找不到可用摄像头时持续重试。
- 后台守护线程持续读帧、编码 JPEG、更新最新帧；线程安全。
"""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import Optional

from .logger import backend_log


# =========================================================
# ★ 演示要点（小任务1：后端 OpenCV 采集）
#   实操按 4 步走，对应下方 ★ 标记：
#   ① 打开摄像头 cap = cv2.VideoCapture(0)
#   ② 设置目标分辨率 1280x720
#   ③ 循环读帧 cap.read()
#   ④ JPEG 编码压缩 (cv2.imencode)
# =========================================================

# OpenCV 延迟导入：缺少依赖时服务仍可启动，仅该功能不可用
try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


class UsbCameraCapture:
    """
    单例：后台线程用 OpenCV 捕获 USB 摄像头，暴露最新 JPEG 帧。
    设计上同一进程仅占用一个摄像头设备，供所有会话/前端共享。
    """

    # 探测设备索引上限
    MAX_DEVICE_INDEX = 8
    # 目标分辨率优先，失败则回退
    TARGET_WIDTH = 2560
    TARGET_HEIGHT = 1440
    FPS = 60 # 抓帧频率上限

    def __init__(self) -> None:
        self._cap: Optional["cv2.VideoCapture"] = None
        self._device_index: int = -1
        self._latest_jpeg_bytes: Optional[bytes] = None
        self._latest_jpeg_b64: Optional[str] = None
        self._latest_timestamp: int = 0
        self._frame_count: int = 0
        self._connected: bool = False
        self._running: bool = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # ---------- 状态 ----------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def device_index(self) -> int:
        with self._lock:
            return self._device_index

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    @property
    def latest_timestamp(self) -> int:
        with self._lock:
            return self._latest_timestamp

    @property
    def latest_bytes(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg_bytes

    @property
    def latest_jpeg_b64(self) -> Optional[str]:
        with self._lock:
            return self._latest_jpeg_b64

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "connected": self._connected,
                "device_index": self._device_index,
                "frame_count": self._frame_count,
                "timestamp": self._latest_timestamp,
                "has_frame": self._latest_jpeg_b64 is not None,
            }

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        """幂等启动守护线程"""
        with self._lock:
            if self._running:
                return True
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="usb-camera", daemon=True)
        self._thread.start()
        backend_log("[UsbCam] 摄像头代理启动")
        return True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._close_capture()
        backend_log("[UsbCam] 摄像头代理停止")

    # ---------- 内部 ----------
    def _close_capture(self) -> None:
        cap = self._cap
        self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        with self._lock:
            self._connected = False
            self._device_index = -1

    def _open_capture_for(self, index: int) -> Optional["cv2.VideoCapture"]:
        if cv2 is None:
            return None
        try:
            # ★演示①: 打开摄像头（设备索引）
            cap = cv2.VideoCapture(index)
        except Exception:
            return None
        if cap is None or not cap.isOpened():
            return None
        # 尝试设置分辨率
        try:
            # ★演示②: 设置目标分辨率
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.TARGET_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.TARGET_HEIGHT)
        except Exception:
            pass
        return cap

    def _run_loop(self) -> None:
        while self._running:
            cap = self._cap
            device_index = self._device_index
            # 未连上：探测可用设备索引
            if cap is None:
                opened = False
                for index in range(self.MAX_DEVICE_INDEX):
                    if not self._running:
                        return
                    candidate = self._open_capture_for(index)
                    if candidate is not None:
                        cap = candidate
                        device_index = index
                        self._cap = cap
                        self._device_index = index
                        self._connected = True
                        self._last_read_fail = 0.0
                        backend_log(f"[UsbCam] 已打开 USB 摄像头，设备索引 {index}")
                        opened = True
                        break
                    # 短暂延迟，避免瞬间打满 CPU 探测
                    time.sleep(0.2)
                if not opened:
                    time.sleep(1.0)
                    continue
                # 先读一帧预热，确认能取到帧；若失败继续探测
                ok, frame = cap.read()
                if not ok or frame is None:
                    backend_log("[UsbCam] 摄像头已打开但读不到帧，重新探测")
                    self._close_capture()
                    self._last_read_fail = time.monotonic()
                    time.sleep(0.5)
                    continue
                self._store_frame(frame)

            # 已连上：循环读帧
            try:
                # ★演示③: 循环读帧
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None

            if not ok or frame is None:
                # 读帧失败计数，连续失败则释放并进入重新探测
                now = time.monotonic()
                if now - getattr(self, "_last_read_fail", 0) > 2.0:
                    backend_log("[UsbCam] 连续读帧失败，释放摄像头并重新探测")
                    self._close_capture()
                else:
                    self._last_read_fail = now
                time.sleep(0.2)
                continue

            self._last_read_fail = 0.0
            self._store_frame(frame)

            # 按帧率节流
            interval = 1.0 / self.FPS
            time.sleep(interval)

    def _store_frame(self, frame) -> None:
        if cv2 is None:
            return
        try:
            # ★演示④: JPEG 编码压缩（此处见压缩后的帧体积极小）
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                return
            jpeg_bytes = buf.tobytes()
            jpeg_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
            with self._lock:
                self._latest_jpeg_bytes = jpeg_bytes
                self._latest_jpeg_b64 = jpeg_b64
                self._latest_timestamp = int(time.time() * 1000)
                self._frame_count += 1
        except Exception:
            pass


# 模块级单例
usb_cam = UsbCameraCapture()