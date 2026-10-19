"""画面旋转（CAMERA_ROTATION，每侧各自独立）测试

架构：香橙派推原始帧，后端不转；本机角度只作用于——
- /usb-camera/latest 出帧时转正（视觉抓帧源）；
- 视觉链路 video_frame 转正（vision_handler）；
- 前端 CSS 显示（前端职责，不在后端测）。

注意：
- RemoteCameraFeed 丢弃 < MIN_JPEG_BYTES(1024) 的帧，测试图用 512x384 纯色图
  （实测 JPEG ~3.7KB），左上角白点作方向标记；
- JPEG 有损压缩会把 255 白点压到 ~250，像素断言用阈值而非精确等值。
"""
import base64

import numpy as np
import pytest

from app.camera_rotate import rotation_angle, rotate_jpeg_bytes
from app.config import settings
from app.remote_camera import RemoteCameraFeed
from app.usb_camera import usb_cam

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

jpeg_required = pytest.mark.skipif(cv2 is None, reason="未安装 opencv")

_BRIGHT = 200   # 白点经有损压缩后的亮度下限
_DARK = 60      # 黑场上限


def _tiny_jpeg(width: int = 512, height: int = 384) -> bytes:
    """纯色图 + 左上角白点；体积必须超过 RemoteCameraFeed.MIN_JPEG_BYTES"""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[0, 0] = (255, 255, 255)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 98])
    assert ok
    jpeg = buf.tobytes()
    assert len(jpeg) >= 1024
    return jpeg


def _decode(jpeg: bytes) -> "np.ndarray":
    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    return img


def test_rotation_angle_clamps_invalid(monkeypatch):
    monkeypatch.setattr(settings, "camera_rotation", 45)
    assert rotation_angle() == 0
    monkeypatch.setattr(settings, "camera_rotation", 270)
    assert rotation_angle() == 270
    monkeypatch.setattr(settings, "camera_rotation", 0)
    assert rotation_angle() == 0


@jpeg_required
def test_rotate_jpeg_bytes_swaps_dims_on_90(monkeypatch):
    out = rotate_jpeg_bytes(_tiny_jpeg(), 90)
    assert out is not None and out[:2] == b"\xff\xd8"
    img = _decode(out)
    assert img.shape[:2] == (512, 384)          # 宽高互换
    assert int(img[0, -1, 0]) >= _BRIGHT        # 左上白点 → 顺时针 90° 后到右上
    assert int(img[0, 0, 0]) <= _DARK


def test_rotate_jpeg_bytes_passthrough_cases():
    assert rotate_jpeg_bytes(b"\xff\xd8fake", 0) is None   # 角度 0：无需处理
    assert rotate_jpeg_bytes(b"", 180) is None             # 空帧
    assert rotate_jpeg_bytes(b"\xff\xd8not-a-real-jpeg", 180) is None  # 解码失败


def test_usb_store_frame_is_raw(monkeypatch):
    """采集入库不做旋转（方向由消费侧处理）"""
    monkeypatch.setattr(settings, "camera_rotation", 180)
    with usb_cam._lock:
        usb_cam._latest_jpeg_b64 = None
        usb_cam._latest_jpeg_bytes = None
    frame = np.zeros((4, 6, 3), dtype=np.uint8)
    frame[0, 0] = (255, 255, 255)
    usb_cam._store_frame(frame)
    with usb_cam._lock:
        stored = usb_cam._latest_jpeg_bytes
    img = _decode(stored)
    assert int(img[0, 0, 0]) >= _BRIGHT         # 原始方向：白点仍在左上


def test_remote_feed_update_is_passthrough(monkeypatch):
    """远程帧不做任何旋转/重编码（方向由各消费侧处理）"""
    monkeypatch.setattr(settings, "camera_rotation", 180)
    feed = RemoteCameraFeed()
    jpeg = _tiny_jpeg()
    assert feed.update(jpeg) is True
    assert feed.latest_bytes == jpeg            # 字节级直通


def test_remote_feed_update_keeps_undecodable_frame(monkeypatch):
    """假 JPEG（测试夹具/异常数据）原样入库，不丢帧"""
    monkeypatch.setattr(settings, "camera_rotation", 180)
    feed = RemoteCameraFeed()
    fake = b"\xff\xd8" + b"x" * 4096 + b"\xff\xd9"
    assert feed.update(fake) is True
    assert feed.latest_bytes == fake


def test_usb_latest_view_b64_consistent(monkeypatch):
    monkeypatch.setattr(settings, "camera_rotation", 180)
    with usb_cam._lock:
        usb_cam._latest_jpeg_b64 = None
        usb_cam._latest_jpeg_bytes = None
    usb_cam._store_frame(np.zeros((4, 6, 3), dtype=np.uint8))
    with usb_cam._lock:
        b64 = usb_cam._latest_jpeg_b64
    assert base64.b64decode(b64)[:2] == b"\xff\xd8"
