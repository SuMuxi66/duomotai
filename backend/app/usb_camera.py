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
import os
import platform
import threading
import time
from typing import Optional

from .logger import backend_log
from .remote_camera import RemoteCameraFeed, remote_feed
from .camera_rotate import rotation_angle
from .config import settings


# OpenCV 的 DSHOW 按索引探测失败会在 C++ 层刷
# "can't be used to capture by index" WARN（OpenCV5 已移除该方式且必败）；
# 无摄像头时探测循环会把单条 WARN 放大成刷屏，这里把 videoio 日志压到 ERROR。
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

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
    # 目标分辨率：默认 0=不强制（由下面候选档决定）。
    # 需要自定义时通过环境变量 CAM_PROXY_WIDTH / CAM_PROXY_HEIGHT 覆盖。
    TARGET_WIDTH = int(os.getenv("CAM_PROXY_WIDTH", "0") or 0)
    TARGET_HEIGHT = int(os.getenv("CAM_PROXY_HEIGHT", "0") or 0)
    FPS = 60 # 抓帧频率上限
    # 分辨率协商顺序：先按配置请求，否则默认 3840x2160 起逐档下探，
    # 末档 (0,0) 表示不指定、用摄像头默认分辨率。
    # 实测档位帧率：640x480@30、1280x720@10、1920x1080@5、2560x1440@1(直接set)。
    # 2026-09-15 修复：候选档曾被削成 [(2560,1440), (0,0)]，首选档正是下面这条
    # 坏档 → 每次协商必黑帧 → 永久回退 DSHOW 默认 640x480。
    # 修复后实测（MF500 + MJPG）：请求 2560x1440 仍黑帧，请求 3840x2160 由 DSHOW
    # 回退到原生 2560x1440@31fps —— 所以 3840 必须放首位，2560 作为次选。
    RESOLUTION_CANDIDATES = (
        [(TARGET_WIDTH, TARGET_HEIGHT)] if TARGET_WIDTH > 0 and TARGET_HEIGHT > 0
        else [(3840, 2160), (2560, 1440)]
    ) + [(1920, 1080), (1280, 720), (0, 0)]

    # 低档自动升级：探测期被临时占用/驱动未就绪而落到低档时，定期重议。
    # 升级目标宽度：配置了目标分辨率用配置值，否则按 2K(2560) 判档。
    UPGRADE_BASE_INTERVAL = 15.0   # 首次升级检查间隔（秒）
    UPGRADE_MAX_INTERVAL = 300.0   # 失败退避上限（秒）

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
        self._last_upgrade_check: float = 0.0   # 上次升级判档时间
        self._last_upgrade_at: float = 0.0      # 上次实际执行升级的时间
        self._upgrade_attempts: int = 0         # 连续升级失败次数（指数退避）
        self._last_read_fail: float = 0.0       # 上次读帧失败时间（monotonic）
        self._last_success_read: float = 0.0    # 上次成功读帧时间（monotonic，冻结看门狗）
        self._watchdog_thread: Optional[threading.Thread] = None
        self.FREEZE_THRESHOLD: float = 10.0     # 帧冻结判定阈值（秒）

    # ---------- 状态 ----------
    # 公开视图统一走"远程优先"：摄像头数据源模式为 network（前端设置面板可切换，
    # settings.camera_source_mode 持久化于 .env.local）且远程帧保鲜期内（香橙派推流中），
    # 所有消费者（MJPEG 流 / latest 拍照 / 前端画面与视觉分析）自动跟随远程画面；
    # 模式切到 local 或远程断流超时后自动回落本地捕获，前端零改动。
    def _remote_if_fresh(self) -> Optional[RemoteCameraFeed]:
        if str(settings.camera_source_mode).strip().lower() == "local":
            return None
        return remote_feed if remote_feed.is_fresh() else None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        if self._remote_if_fresh() is not None:
            return True
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
        remote = self._remote_if_fresh()
        if remote is not None:
            return remote.latest_timestamp
        with self._lock:
            return self._latest_timestamp

    @property
    def latest_bytes(self) -> Optional[bytes]:
        remote = self._remote_if_fresh()
        if remote is not None:
            return remote.latest_bytes
        with self._lock:
            return self._latest_jpeg_bytes

    @property
    def latest_jpeg_b64(self) -> Optional[str]:
        remote = self._remote_if_fresh()
        if remote is not None:
            return remote.latest_jpeg_b64
        with self._lock:
            return self._latest_jpeg_b64

    def status(self) -> dict:
        remote_fresh = remote_feed.is_fresh() and str(settings.camera_source_mode).strip().lower() != "local"
        with self._lock:
            status = {
                "running": self._running,
                "connected": self._connected or remote_fresh,
                "device_index": self._device_index,
                "frame_count": self._frame_count,
                "timestamp": remote_feed.latest_timestamp if remote_fresh else self._latest_timestamp,
                "has_frame": (remote_feed.latest_jpeg_b64 if remote_fresh else self._latest_jpeg_b64) is not None,
            }
        status["source"] = "remote" if remote_fresh else ("local" if status["connected"] else "none")
        status["mode"] = str(settings.camera_source_mode).strip().lower() or "network"
        status["rotation"] = rotation_angle()
        status["remote"] = remote_feed.status()
        return status

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        """幂等启动守护线程"""
        with self._lock:
            if self._running:
                return True
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="usb-camera", daemon=True)
        self._thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name="usb-camera-watchdog", daemon=True)
        self._watchdog_thread.start()
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
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=3)
            self._watchdog_thread = None
        self._close_capture()
        backend_log("[UsbCam] 摄像头代理停止")

    # ---------- 内部 ----------
    def _upgrade_interval(self) -> float:
        """升级失败后的退避间隔：15s 起、指数增长、5min 封顶"""
        return min(
            self.UPGRADE_BASE_INTERVAL * (2 ** self._upgrade_attempts),
            self.UPGRADE_MAX_INTERVAL,
        )

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

    def _watchdog_loop(self) -> None:
        """独立看门狗线程：检测帧冻结，强制释放捕获以打破 cap.read() 可能的阻塞。

        Windows 上 USB 摄像头长时间运行后 cap.read() 可能永久阻塞，
        导致捕获线程卡死、latest_bytes 不再更新，但 MJPEG 心跳继续推旧帧——
        前端看到"运行中"但画面冻结。本线程从外部强制 release()，
        使阻塞的 read() 返回失败，_run_loop 随之走重新探测分支恢复。
        """
        while self._running:
            time.sleep(2.0)
            if self._cap is None:
                continue
            ts = self._latest_timestamp
            if ts == 0:
                continue
            age_ms = int(time.time() * 1000) - ts
            if age_ms > self.FREEZE_THRESHOLD * 1000:
                backend_log(f"[UsbCam] 看门狗：帧已冻结 {age_ms / 1000:.1f}s，强制释放捕获以触发重连")
                self._close_capture()

    @staticmethod
    def _prefer_mjpeg(cap) -> None:
        """打开设备后立即切 MJPEG 输入模式。

        UVC 摄像头（含 MF500）在 YUYV 下带宽不足：请求 2K 只会得到黑帧或 1fps 坏档，
        必须先设 FOURCC=MJPG 才能协商上高分辨率——香橙派端 opi_camera_client.py 同源做法。
        少数只有 YUYV 的摄像头设置会失败，此时保持驱动默认，不影响后续档位判断。
        """
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass

    @staticmethod
    def _open_raw(index: int) -> Optional["cv2.VideoCapture"]:
        if platform.system() != "Windows":
            try:
                cap = cv2.VideoCapture(index)
            except Exception:
                return None
            UsbCameraCapture._prefer_mjpeg(cap)
            return cap
        # OpenCV 5（opencv-python>=5）移除了 DSHOW 按索引打开方式
        # （报 "can't be used to capture by index"），且 MSMF 在部分摄像头上
        # 会输出全黑帧：逐后端尝试，DSHOW 失败回退默认后端（CAP_ANY）。
        # OpenCV 主版本 >=5 时 DSHOW 按索引必败且每次必刷 WARN，直接跳过。
        try:
            opencv_major = int((getattr(cv2, "__version__", "") or "0").split(".")[0])
        except Exception:
            opencv_major = 4
        backends = (cv2.CAP_DSHOW, cv2.CAP_ANY) if opencv_major < 5 else (cv2.CAP_ANY,)
        for backend in backends:
            try:
                cap = cv2.VideoCapture(index, backend)
                if cap is not None and cap.isOpened():
                    UsbCameraCapture._prefer_mjpeg(cap)
                    return cap
            except Exception:
                continue
        return None

    def _open_capture_for(self, index: int) -> Optional["cv2.VideoCapture"]:
        if cv2 is None:
            return None
        # 逐档尝试目标分辨率：读到非黑帧才采用（部分摄像头高档位损坏输出全黑）。
        # 每档先空读数帧等自动曝光稳定——刚打开时首帧常偏暗，单帧判断会误跳档
        for width, height in self.RESOLUTION_CANDIDATES:
            cap = self._open_raw(index)
            if cap is None or not cap.isOpened():
                continue
            try:
                if width > 0 and height > 0:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                # 空读数帧等自动曝光稳定——刚打开时首帧常偏暗，单帧判断会误判全黑跳档
                frame = None
                for _ in range(6):
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        frame = None
                    time.sleep(0.1)
                if frame is None or float(frame.mean()) <= 1.0:
                    backend_log(f"[UsbCam] {width or '原生'}x{height or '原生'} 无有效帧/黑帧，尝试下一档")
                    cap.release()
                    continue
                # 驱动静默回退护栏：请求 3840x2160 却被 DSHOW 给到远低的档位时继续下探，
                # 避免在低档位就"通过"校验、错过摄像头原生高分辨率（末档 (0,0) 不参与判断）。
                if width > 0 and frame.shape[1] < width * 0.6:
                    backend_log(
                        f"[UsbCam] 请求 {width}x{height} 实得 {frame.shape[1]}x{frame.shape[0]}（驱动回退），尝试下一档"
                    )
                    cap.release()
                    continue
                # 档位帧率过低（<3fps）视为不可用，如 2560x1440 的 1fps 慢档
                t0 = time.monotonic()
                n = 0
                while time.monotonic() - t0 < 1.0:
                    r_ok, _ = cap.read()
                    if r_ok:
                        n += 1
                if n < 3:
                    backend_log(f"[UsbCam] {width or '原生'}x{height or '原生'} 帧率过慢({n}fps)，尝试下一档")
                    cap.release()
                    continue
                backend_log(f"[UsbCam] 采用档位 {width or '原生'}x{height or '原生'}（实际帧 {frame.shape[1]}x{frame.shape[0]} @ ~{n}fps）")
                return cap
            except Exception:
                pass
        # 全部候选档位不可用：回退摄像头默认分辨率
        cap = self._open_raw(index)
        if cap is not None and cap.isOpened():
            backend_log("[UsbCam] 各候选档位均不可用，采用摄像头默认分辨率")
        return cap

    def _run_loop(self) -> None:
        while self._running:
            # 摄像头数据源模式为 network 且远程源保鲜期内挂起本地捕获：释放本地设备并跳过探测，
            # 避免空探测刷日志/抢 CPU；切到 local 模式或远程断流超时后自然恢复本地探测
            if str(settings.camera_source_mode).strip().lower() != "local" and remote_feed.is_fresh():
                if self._cap is not None or self._device_index != -1:
                    backend_log("[UsbCam] 远程摄像头帧接入中，挂起本地捕获")
                    self._close_capture()
                time.sleep(1.0)
                continue
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
                        self._probe_fails = 0
                        break
                    # 短暂延迟，避免瞬间打满 CPU 探测
                    time.sleep(0.2)
                if not opened:
                    # 全索引探测失败：指数退避（5s 起、上限 30s）。
                    # 无摄像头/没插 USB 时若仍每秒空转，会反复触发 OpenCV 探测与日志。
                    self._probe_fails = getattr(self, "_probe_fails", 0) + 1
                    time.sleep(min(5.0 * self._probe_fails, 30.0))
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
                self._last_success_read = time.monotonic()

            # 已连上：循环读帧
            # 轻量看门狗：上次成功读帧距今过久（read 返回假成功/重复帧），强制重连
            if self._last_success_read > 0 and time.monotonic() - self._last_success_read > self.FREEZE_THRESHOLD:
                backend_log(f"[UsbCam] 读帧看门狗：{self.FREEZE_THRESHOLD}s 无有效新帧，释放重连")
                self._close_capture()
                time.sleep(0.5)
                continue

            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None

            if not ok or frame is None:
                # 读帧失败计数，连续失败则释放并进入重新探测
                now = time.monotonic()
                if now - self._last_read_fail > 2.0:
                    backend_log("[UsbCam] 连续读帧失败，释放摄像头并重新探测")
                    self._close_capture()
                self._last_read_fail = now
                time.sleep(0.2)
                continue

            self._last_read_fail = 0.0
            self._last_success_read = time.monotonic()
            self._store_frame(frame)

            # 低档自动升级：探测期被临时占用/驱动未就绪而落到低档时，
            # 定期解除连接重协商（重探测优先候选最高档）。失败按指数退避。
            frame_w = 0 if frame is None else frame.shape[1]
            target_w = self.TARGET_WIDTH if self.TARGET_WIDTH > 0 else 2560
            now = time.monotonic()
            if now - self._last_upgrade_check >= self.UPGRADE_BASE_INTERVAL:
                self._last_upgrade_check = now
                if frame_w >= target_w:
                    # 已达标：视为升级成功，重置退避
                    self._upgrade_attempts = 0
                elif now - self._last_upgrade_at >= self._upgrade_interval():
                    backend_log(f"[UsbCam] 当前档位 {frame_w}px < 目标 {target_w}px，重新协商以升级")
                    self._upgrade_attempts += 1
                    self._last_upgrade_at = now
                    self._close_capture()
                    time.sleep(0.5)
                    continue

            # 按帧率节流
            interval = 1.0 / self.FPS
            time.sleep(interval)

    def _store_frame(self, frame) -> None:
        if cv2 is None:
            return
        try:
            # 不做旋转：画面方向是每台后端自己的偏好（CAMERA_ROTATION），
            # 由消费侧处理——/usb-camera/latest 出帧与视觉链路转正、前端 CSS 显示。
            # 采集与远程推流一律保持原始帧。
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