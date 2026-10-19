"""远程摄像头帧仓与 usb_cam 视图切换测试

覆盖：
- RemoteCameraFeed 合法帧写入 / 非法帧丢弃 / 保鲜判定
- usb_cam 公开视图"远程优先、断流回落本地"的切换行为
"""
import time

import app.remote_camera as remote_camera_module
from app.config import settings
from app.remote_camera import RemoteCameraFeed, remote_feed
from app.usb_camera import usb_cam


JPEG_A = b"\xff\xd8" + b"a" * 4096 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"b" * 4096 + b"\xff\xd9"
GARBAGE = b"not-a-jpeg" * 100


def _reset_singletons() -> None:
    """清空两个单例的帧状态，测试间互不污染"""
    with remote_feed._lock:
        remote_feed._latest_jpeg_bytes = None
        remote_feed._latest_jpeg_b64 = None
        remote_feed._latest_timestamp = 0
        remote_feed._frame_count = 0
        remote_feed._dropped_count = 0
        remote_feed._last_arrival = 0.0
        remote_feed._fps = 0.0
    with usb_cam._lock:
        usb_cam._latest_jpeg_bytes = None
        usb_cam._latest_jpeg_b64 = None
        usb_cam._latest_timestamp = 0
        usb_cam._connected = False


def test_feed_accepts_valid_jpeg():
    _reset_singletons()
    assert remote_feed.update(JPEG_A) is True
    assert remote_feed.latest_bytes == JPEG_A
    assert remote_feed.frame_count == 1
    assert remote_feed.latest_timestamp > 0
    assert remote_feed.is_fresh() is True
    assert remote_feed.latest_jpeg_b64  # b64 视图同步生成


def test_feed_rejects_garbage_and_tiny_frames():
    _reset_singletons()
    assert remote_feed.update(GARBAGE) is False          # 无 SOI 魔数
    assert remote_feed.update(b"\xff\xd8short") is False  # 过小
    assert remote_feed.frame_count == 0
    assert remote_feed.status()["dropped_count"] == 2
    assert remote_feed.latest_bytes is None


def test_usb_cam_view_prefers_remote_when_fresh(monkeypatch):
    _reset_singletons()
    # 显式固定 network：机器 .env.local 可能持久化了 local（用户开关状态），测试不依赖环境
    monkeypatch.setattr(settings, "camera_source_mode", "network")
    with usb_cam._lock:
        usb_cam._latest_jpeg_bytes = b"local-frame"
        usb_cam._connected = True
    remote_feed.update(JPEG_A)

    assert usb_cam.is_connected is True
    assert usb_cam.latest_bytes == JPEG_A
    assert usb_cam.latest_timestamp == remote_feed.latest_timestamp
    status = usb_cam.status()
    assert status["source"] == "remote"
    assert status["connected"] is True
    assert status["remote"]["fresh"] is True


def test_usb_cam_view_falls_back_to_local_when_stale(monkeypatch):
    _reset_singletons()
    with usb_cam._lock:
        usb_cam._latest_jpeg_bytes = b"local-frame"
        usb_cam._connected = True
    remote_feed.update(JPEG_A)
    # 保鲜期设为负值 → 任何帧都立即过期，模拟远程断流超时（确定性，不受时钟粒度影响）
    monkeypatch.setattr(remote_camera_module, "REMOTE_FRESH_SECONDS", -1.0)

    assert remote_feed.is_fresh() is False
    assert usb_cam.latest_bytes == b"local-frame"
    assert usb_cam.status()["source"] == "local"


def test_usb_cam_source_none_when_neither():
    _reset_singletons()
    status = usb_cam.status()
    assert status["source"] == "none"
    assert status["has_frame"] is False


def test_usb_cam_local_mode_ignores_remote(monkeypatch):
    """local 模式：即使远程帧保鲜也忽略，视图回落本机捕获"""
    _reset_singletons()
    monkeypatch.setattr(settings, "camera_source_mode", "local")
    with usb_cam._lock:
        usb_cam._latest_jpeg_bytes = b"local-frame"
        usb_cam._connected = True
    remote_feed.update(JPEG_A)

    assert usb_cam.latest_bytes == b"local-frame"
    status = usb_cam.status()
    assert status["source"] == "local"
    assert status["mode"] == "local"


def test_usb_cam_network_mode_prefers_remote(monkeypatch):
    """network 模式（默认）：远程帧保鲜期内视图跟随远程画面"""
    _reset_singletons()
    monkeypatch.setattr(settings, "camera_source_mode", "network")
    with usb_cam._lock:
        usb_cam._latest_jpeg_bytes = b"local-frame"
    remote_feed.update(JPEG_A)

    assert usb_cam.latest_bytes == JPEG_A
    status = usb_cam.status()
    assert status["source"] == "remote"
    assert status["mode"] == "network"
