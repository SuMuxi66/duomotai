"""
画面旋转（每侧各自独立，CAMERA_ROTATION 只管本机）

摄像头物理倒装（需要 180 转正）。旋转是**每台后端自己的显示/识别方向偏好**，
不在画面源（香橙派推流原始帧）上做——双后端共享同一路原始流，任何源头旋转
都会同时影响两边（2026-09-11 用户需求：各侧旋转互不影响）：

- 显示：前端按本机 CAMERA_ROTATION（GET /config 下发）做 CSS 旋转，仅影响本前端；
- AI 视觉：前端抓帧走 /usb-camera/latest（出帧时按本机角度转正）与视觉链路
  （vision_handler 收到 video_frame 后转正）——两者都是 0.2fps 级低频，编解码
  成本可忽略；MJPEG 流 /usb-camera/stream 保持原始帧直通（多前端共享、高频）；
- 本地 USB 采集（local 模式兜底）：同样保持原始帧，方向处理同上。

角度存 CAMERA_ROTATION（0/90/180/270，顺时针，默认 180），前端"旋转画面"按钮经
POST /env/update 持久化到本机 .env.local——只影响本机，不影响另一台后端。
"""
from __future__ import annotations

import threading

from .config import settings
from .logger import backend_log

_VALID_ANGLES = (0, 90, 180, 270)

_last_logged_angle: int = -1
_angle_log_lock = threading.Lock()


def rotation_angle() -> int:
    """本机旋转角度（0/90/180/270）；配置缺失/非法时按 0（不旋转）处理"""
    try:
        angle = int(settings.camera_rotation)
    except Exception:
        return 0
    if angle not in _VALID_ANGLES:
        return 0
    global _last_logged_angle
    if angle != _last_logged_angle:
        with _angle_log_lock:
            if angle != _last_logged_angle:
                if _last_logged_angle >= 0:
                    backend_log(f"[画面旋转] 本机角度变更：{_last_logged_angle}° → {angle}°")
                _last_logged_angle = angle
    return angle


def rotate_jpeg_bytes(jpeg: bytes, angle: int):
    """按本机角度旋转 JPEG 并重编码（质量 85）。

    返回新的 bytes；无需旋转 / 解码失败 / cv2 缺失时返回 None（调用方沿用原始帧）。
    只用于低频路径（/usb-camera/latest 出帧、视觉链路 0.2fps）——高频 MJPEG 流
    由前端 CSS 旋转显示，不走这里。
    """
    if not angle or not jpeg:
        return None
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        if angle == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif angle == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif angle == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            return None
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else None
    except Exception:
        return None
