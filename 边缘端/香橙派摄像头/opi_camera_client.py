#!/usr/bin/env python3
"""
香橙派 USB 摄像头采集客户端（RK3588 边缘端）

把原来插在 Windows 后端机上的 USB 摄像头改接香橙派：本脚本在香橙派上常驻抓帧、
JPEG 编码后经 WebSocket 推流到后端 /ws/opi-cam。后端 remote_camera.remote_feed
保鲜期内自动接管 usb_cam 公开视图，前端 MJPEG 取流 / 拍照 / 视觉分析零改动。

支持多后端同时推流：OPI_CAM_SERVER 逗号分隔多个地址，摄像头只开一次、
JPEG 只编码一次，帧同时分发给所有已连接的后端；每个连接独立自动重连。

USB 热插拔自动恢复：设备拔出（/dev/videoN 消失）立即释放并轮询等插回，
插回后 ~1s 内恢复推流；读帧线程卡死（超时无返回）则退出进程由 systemd
拉起干净实例（Restart=always），不依赖人工干预。

依赖（香橙派上）：
    pip3 install opencv-python-headless websockets -i https://pypi.tuna.tsinghua.edu.cn/simple

配置（环境变量，均有默认值）：
    OPI_CAM_SERVER   后端 WS 地址，逗号分隔可配多个，
                     如 ws://192.168.1.96:8010/ws/opi-cam,ws://192.168.1.252:8010/ws/opi-cam（必填）
    OPI_CAM_DEVICE   摄像头设备，如 /dev/video0；默认自动扫描 /dev/video*
    OPI_CAM_WIDTH    目标宽度（0=不指定，逐档协商）默认 2560
    OPI_CAM_HEIGHT   目标高度，默认 1440
    OPI_CAM_FPS      推流帧率上限，默认 30
    OPI_CAM_QUALITY  JPEG 质量，默认 80
    OPI_CAM_ROTATION 画面旋转（0/90/180/270，顺时针，编码前转原始帧，近零开销）。
                     缺省=跟随后端 CAMERA_ROTATION（每 3s 轮询后端 /usb-camera/status，
                     前端"旋转画面"按钮改后端角度后 ~3s 内生效）；显式设置则固定不走轮询。

运行：
    python3 opi_camera_client.py            # 前台调试
    sudo systemctl start opi-camera         # 生产（见 opi-camera.service）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Optional

import cv2
import websockets

# ---------------- 配置 ----------------
SERVERS = [s.strip() for s in os.getenv("OPI_CAM_SERVER", "").split(",") if s.strip()]
DEVICE = os.getenv("OPI_CAM_DEVICE", "")
TARGET_W = int(os.getenv("OPI_CAM_WIDTH", "2560") or 0)
TARGET_H = int(os.getenv("OPI_CAM_HEIGHT", "1440") or 0)
FPS_LIMIT = max(1.0, float(os.getenv("OPI_CAM_FPS", "30") or 30))
JPEG_QUALITY = min(100, max(30, int(os.getenv("OPI_CAM_QUALITY", "80") or 80)))
ROTATION_ENV = os.getenv("OPI_CAM_ROTATION", "").strip()

# 旋转角（顺时针度数）。固定模式用 OPI_CAM_ROTATION；跟随模式由轮询线程刷新，
# 采集循环每帧读取——旋转在编码前的原始帧上做（memcpy 级 ~3ms），后端对远程
# 画面零重编码直通转发，2K 高帧率才不会卡在后端。
_rotation = 0
_rotation_lock = threading.Lock()
_rotation_source = "init"


def get_rotation() -> int:
    with _rotation_lock:
        return _rotation


def set_rotation(angle: int, source: str) -> None:
    global _rotation, _rotation_source
    if angle not in (0, 90, 180, 270):
        angle = 0
    with _rotation_lock:
        old, old_src = _rotation, _rotation_source
        _rotation, _rotation_source = angle, source
    if old != angle:
        log.info(f"旋转角变更：{old}°({old_src}) → {angle}°({source})")


def rotate_frame_cv(frame, angle: int):
    """编码前旋转原始 BGR 帧；0°/异常原样返回（与后端 camera_rotate 同向：顺时针）"""
    if not angle or frame is None:
        return frame
    try:
        if angle == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if angle == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    except Exception:
        return frame


def status_url(ws_url: str) -> str:
    """ws://host:port/ws/opi-cam → http://host:port/usb-camera/status"""
    base = ws_url.split("://", 1)[-1].split("/", 1)[0]
    return f"http://{base}/usb-camera/status"


def rotation_poller(stop: threading.Event) -> None:
    """跟随模式：每 3s 拉后端 status 的 rotation 字段（前端"旋转画面"按钮驱动）。
    拉取失败保持上次角度（推流 WS 与 HTTP status 通常同生共死，无需特殊处理）。"""
    url = status_url(SERVERS[0])
    fails = 0
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                angle = int(json.load(resp).get("rotation") or 0)
            set_rotation(angle, "backend")
            fails = 0
        except Exception as e:
            fails += 1
            if fails == 1 or fails % 10 == 0:
                log.warning(f"拉取后端旋转角失败(累计{fails}次): {type(e).__name__}: {e}，沿用 {get_rotation()}°")
        stop.wait(3.0)


def init_rotation() -> None:
    """固定模式：直接用 env。跟随模式：先同步拉一次（保证 hello 用上正确朝向的宽高），
    再起后台轮询线程。"""
    if ROTATION_ENV:
        set_rotation(int(ROTATION_ENV or 0), "env")
        return
    try:
        with urllib.request.urlopen(status_url(SERVERS[0]), timeout=2.0) as resp:
            angle = int(json.load(resp).get("rotation") or 0)
        set_rotation(angle, "backend")
    except Exception as e:
        log.warning(f"启动时拉取后端旋转角失败: {type(e).__name__}: {e}，先按 0° 推流，3s 后重试")
    stop = threading.Event()
    threading.Thread(target=rotation_poller, args=(stop,), name="rotation-poll", daemon=True).start()

MAX_DEVICE_INDEX = 8
# 分辨率协商顺序：目标档 → 1080p → 720p → 摄像头默认。
# 每档需同时满足：能读到非黑帧、1 秒内实测帧率 >= 3fps（避免锁进 1fps 慢档）。
RESOLUTION_CANDIDATES = (
    [(TARGET_W, TARGET_H)] if TARGET_W > 0 and TARGET_H > 0 else []
) + [(1920, 1080), (1280, 720), (0, 0)]

FREEZE_THRESHOLD = 10.0   # 连续 N 秒读不到新帧 → 释放设备重开（V4L2 偶发停帧）
READ_TIMEOUT = 5.0        # 单次读帧硬超时：正常 <100ms，超时=读线程卡死，退出进程交 systemd 重启
RECONNECT_BACKOFF_MAX = 10.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("opi-cam")


def open_device(device: str, width: int, height: int) -> Optional[tuple]:
    """打开指定设备并协商到可用档位。返回 (VideoCapture, 实际宽, 实际高) 或 None。

    每档先空读数帧等自动曝光稳定，黑帧/低帧率档跳过——与后端 usb_camera 的
    Windows DSHOW 协商策略同源，参数按 Linux V4L2 调整。
    """
    # OpenCV 部分构建无法按设备名打开（报 "can't be used to capture by name"）：
    # /dev/videoN 等价于索引 N，按名失败后退按索引（V4L2 → CAP_ANY）逐个尝试
    index: Optional[int] = None
    if device.startswith("/dev/video"):
        try:
            index = int(device[len("/dev/video"):])
        except ValueError:
            index = None
    openers = [lambda: cv2.VideoCapture(device, cv2.CAP_V4L2)]
    if index is not None:
        openers.append(lambda: cv2.VideoCapture(index, cv2.CAP_V4L2))
        openers.append(lambda: cv2.VideoCapture(index))
    cap = None
    for opener in openers:
        try:
            candidate = opener()
        except Exception:
            continue
        if candidate is not None and candidate.isOpened():
            cap = candidate
            break
        if candidate is not None:
            candidate.release()
    if cap is None:
        return None
    try:
        # MJPEG 输入模式：多数 UVC 摄像头高分辨率仅支持 MJPEG，YUYV 会锁低帧率
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if width > 0 and height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # 空读数帧等曝光稳定；黑帧（全 0/极暗）视为档位不可用
        frame = None
        for _ in range(6):
            ok, frame = cap.read()
            if not ok or frame is None:
                frame = None
            time.sleep(0.1)
        if frame is None or float(frame.mean()) <= 1.0:
            log.warning(f"{device} @{width or '原生'}x{height or '原生'} 无有效帧/黑帧")
            return None
        # 实测 1 秒帧率，过慢档（如 2560x1440@1 的坏档）跳过
        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < 1.0:
            ok, _ = cap.read()
            if ok:
                n += 1
        if n < 3:
            log.warning(f"{device} @{width or '原生'}x{height or '原生'} 帧率过慢({n}fps)")
            return None
        log.info(f"采用档位 {device} {frame.shape[1]}x{frame.shape[0]} @ ~{n}fps")
        return cap, frame.shape[1], frame.shape[0]
    except Exception:
        cap.release()
        return None


def find_camera() -> Optional[tuple]:
    """扫描 /dev/video*，逐档协商，返回第一个可用的 (cap, w, h, dev)"""
    devices = [DEVICE] if DEVICE else [f"/dev/video{i}" for i in range(MAX_DEVICE_INDEX)]
    for dev in devices:
        if not os.path.exists(dev):
            continue
        for width, height in RESOLUTION_CANDIDATES:
            result = open_device(dev, width, height)
            if result is not None:
                cap, w, h = result
                return cap, w, h, dev
    return None


class Target:
    """一个后端推送目标：独立连接 + 独立重连，摄像头帧由采集循环统一分发"""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.hello_pending = False      # 连上后等采集循环补发 hello（需实际宽高）
        self.hello_json = ""
        self.suppressed = False         # 被 1008 策略拒收中：60s 一次静默重试，不刷日志

    async def loop(self) -> None:
        """重连循环：保持到该后端的长连接，断开自动重连。

        后端为 local 模式（用户在设置里关了"云端推送摄像头"）会对推流 WS 一律
        accept 后立刻 close(1008)：若按网络故障的 1s 节奏重连，双方日志每天会刷出
        数万行（Pi 端"后端已连接" ×86400 + 后端 uvicorn "connection open/closed"
        ×172000）。识别 1008 后进入抑制态：60s 一次静默重试（成功与否都不写日志），
        后端切回 network 后自动恢复推流，只打一条过渡日志。
        """
        backoff = 1.0
        while True:
            quiet = self.suppressed   # 抑制态下本轮连接成败均不写日志
            try:
                async with websockets.connect(
                    self.url, max_size=None, ping_interval=20, ping_timeout=20
                ) as ws:
                    self.ws = ws
                    self.hello_pending = True
                    if quiet:
                        self.suppressed = False
                        log.info(f"{self.url} 后端已恢复接收推流，重新连接")
                    else:
                        log.info(f"后端已连接: {self.url}")
                    backoff = 1.0
                    await ws.wait_closed()   # 断开（含服务端拒收/ping 超时）从这里返回
                    if getattr(ws, "close_code", None) == 1008:
                        # 策略拒收（local 模式等）：不是网络故障，禁止按 1s 节奏连击刷日志
                        self.suppressed = True
                        self.ws = None
                        if not quiet:
                            reason = getattr(ws, "close_reason", None) or "策略拒绝"
                            log.info(f"{self.url} 被 1008 拒收（{reason}），转入 60s 静默重试")
                        await asyncio.sleep(60)
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not quiet:
                    log.warning(f"{self.url} 连接失败: {type(e).__name__}: {e}")
            self.ws = None
            await asyncio.sleep(60 if self.suppressed else backoff)
            if not self.suppressed:
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


async def capture_loop(targets: list) -> None:
    """抓帧 + 编码一次，向所有已连接的后端分发；摄像头管理逻辑同单目标版"""
    opened = await asyncio.to_thread(find_camera)
    if opened is None:
        raise RuntimeError("找不到可用摄像头设备（/dev/video*）")
    cap, real_w, real_h, cur_dev = opened
    log.info(
        f"开始推流 {real_w}x{real_h}@<={FPS_LIMIT:.0f}fps q={JPEG_QUALITY} "
        f"rot={get_rotation()}°({_rotation_source}) → {len(targets)} 个后端"
    )

    def build_hello(w: int, h: int) -> str:
        # hello 宽高按旋转后口径上报（90°/270° 宽高互换；仅日志展示用，后端不依赖）
        out_w, out_h = (h, w) if get_rotation() in (90, 270) else (w, h)
        return (
            f'{{"type":"hello","device":"{os.path.basename(DEVICE) if DEVICE else "auto"}",'
            f'"width":{out_w},"height":{out_h},"fps":{FPS_LIMIT:.0f}}}'
        )

    hello_json = build_hello(real_w, real_h)

    frame_interval = 1.0 / FPS_LIMIT
    last_success = time.monotonic()
    sent = 0
    bytes_total = 0
    last_report = time.monotonic()

    def _rotate_encode(frame_bgr, angle: int):
        """旋转+编码合并到线程池一次跑：原始帧旋转 memcpy 级开销（~3ms）。
        注意不要做"预读下一帧"式流水线——实测两个 OpenCV 重线程并发会因
        GIL/内存争用把吞吐从 13.6fps 打到 7fps（2026-09-11 实测踩坑回退）。"""
        fr = rotate_frame_cv(frame_bgr, angle)
        return cv2.imencode(".jpg", fr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])

    while True:
        loop_t0 = time.monotonic()
        try:
            ok, frame = await asyncio.wait_for(asyncio.to_thread(cap.read), timeout=READ_TIMEOUT)
        except asyncio.TimeoutError:
            # 读线程卡死（USB 栈楔死/拔出瞬间驱动挂起）：线程池里挂着永远不返回的
            # 阻塞读，进程内无法恢复——退出进程由 systemd 拉起干净实例（Restart=always）
            log.error(f"cap.read 超时 {READ_TIMEOUT:.0f}s，读线程卡死，退出进程交由 systemd 重启")
            os._exit(1)
        if not ok or frame is None:
            # 读帧失败分两路：设备节点消失（USB 拔出）立即释放轮询等插回；
            # 节点还在（V4L2 停帧/read 假成功返回 None）走冻结阈值后重开
            dev_gone = bool(cur_dev) and not os.path.exists(cur_dev)
            if not dev_gone and time.monotonic() - last_success < FREEZE_THRESHOLD:
                await asyncio.sleep(0.1)
                continue
            reason = f"设备节点 {cur_dev} 消失（USB 已拔出）" if dev_gone else f"{FREEZE_THRESHOLD:.0f}s 无有效帧"
            log.warning(f"{reason}，释放设备等待重开")
            try:
                cap.release()
            except Exception:
                pass
            cap = None
            while True:
                await asyncio.sleep(0.5 if dev_gone else 1.0)
                reopened = await asyncio.to_thread(find_camera)
                if reopened is not None:
                    break
                if dev_gone and os.path.exists(cur_dev):
                    dev_gone = False   # 节点已插回但暂打不开，转慢节奏重试
                elif not dev_gone:
                    raise RuntimeError("摄像头重开失败")
            cap, real_w, real_h, cur_dev = reopened
            last_success = time.monotonic()
            hello_json = build_hello(real_w, real_h)   # 重开档位可能变化，重发 hello
            for t in targets:
                t.hello_pending = True
            log.info(f"摄像头已重开 {cur_dev} {real_w}x{real_h}，恢复推流")
            continue

        last_success = time.monotonic()
        angle = get_rotation()
        ok_enc, buf = await asyncio.to_thread(_rotate_encode, frame, angle)
        if not ok_enc:
            continue
        payload = buf.tobytes()

        # 分发给所有已连接的后端；单个目标发送失败不影响其它目标
        for t in targets:
            if t.ws is None:
                continue
            try:
                if t.hello_pending:
                    await t.ws.send(hello_json)
                    t.hello_pending = False
                await t.ws.send(payload)
            except Exception as e:
                log.warning(f"{t.url} 发送失败: {type(e).__name__}: {e}，等重连")
                t.ws = None

        # 帧级统计（不按目标重复计，否则多后端时日志 fps 会翻倍）
        sent += 1
        bytes_total += len(payload)

        now = time.monotonic()
        if now - last_report >= 15.0:
            log.info(
                f"已推流 {sent} 帧，平均 {sent / (now - last_report):.1f}fps，"
                f"{bytes_total / (now - last_report) / 1024:.0f}KB/s"
            )
            sent = 0
            bytes_total = 0
            last_report = now

        # 帧率节流（扣除本轮耗时）
        spent = time.monotonic() - loop_t0
        if spent < frame_interval:
            await asyncio.sleep(frame_interval - spent)


# 子网自动发现兜底见 opi_discovery.py（2026-09-21 与 sensors/ir 共享同一模块）

from opi_discovery import discovery_loop


async def main() -> None:
    if not SERVERS:
        log.error("未配置 OPI_CAM_SERVER（逗号分隔可配多个，如 ws://192.168.1.96:8010/ws/opi-cam,ws://192.168.1.252:8010/ws/opi-cam）")
        return
    targets = [Target(url) for url in SERVERS]
    init_rotation()   # 同步拉一次后端旋转角（失败则 0° 起步），并起 3s 轮询线程
    conn_tasks = [asyncio.create_task(t.loop()) for t in targets]
    conn_tasks.append(asyncio.create_task(discovery_loop(
        targets, "/ws/opi-cam", log,
        make_target=Target,
        spawn=lambda t: asyncio.create_task(t.loop()),
    )))
    backoff = 1.0
    camera_fail_streak = 0
    try:
        while True:
            try:
                await capture_loop(targets)
                backoff = 1.0
                camera_fail_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"推流会话结束: {type(e).__name__}: {e}，{backoff:.0f}s 后重连")
                # 连续找不到相机：高概率是本进程僵死的读帧线程仍占用 V4L2 设备，
                # 进程内重试永远打不开——退出进程让 systemd 拉起干净实例（Restart=always, 3s）
                if "找不到可用摄像头设备" in str(e) or "摄像头重开失败" in str(e):
                    camera_fail_streak += 1
                    if camera_fail_streak >= 3:
                        log.error("连续 3 次找不到相机，退出进程交由 systemd 重启以释放设备占用")
                        os._exit(1)
                else:
                    camera_fail_streak = 0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
    finally:
        for task in conn_tasks:
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("手动退出")
