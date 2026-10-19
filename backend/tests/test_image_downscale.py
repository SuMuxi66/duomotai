"""downscale_jpeg_bytes：摄像头帧超平台尺寸限制时的等比缩放。

背景：USB 摄像头原生 2560x1440，ModelScope 对输入图有 2048x2048 硬限制，
直接上送 400（input size exceed limit）。
"""
import io

import pytest
from PIL import Image

from app.providers.base import downscale_jpeg_bytes


def _make_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(120, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _jpeg_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_oversized_frame_is_scaled_to_limit():
    out = downscale_jpeg_bytes(_make_jpeg(2560, 1440))
    w, h = _jpeg_size(out)
    assert max(w, h) <= 2048
    # 保持 16:9 比例
    assert abs(w / h - 2560 / 1440) < 0.01


def test_within_limit_frame_unchanged():
    original = _make_jpeg(1280, 720)
    assert downscale_jpeg_bytes(original) is original


def test_empty_bytes_passthrough():
    assert downscale_jpeg_bytes(b"") == b""


def test_garbage_bytes_passthrough():
    garbage = b"not-a-jpeg"
    assert downscale_jpeg_bytes(garbage) == garbage


@pytest.mark.parametrize("width,height", [(4000, 3000), (2048, 2048), (100, 3000)])
def test_various_aspect_ratios_capped(width, height):
    out = downscale_jpeg_bytes(_make_jpeg(width, height))
    w, h = _jpeg_size(out)
    assert max(w, h) <= 2048
