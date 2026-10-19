"""TTS 公共 WAV 封装工具。

原 dashscope_tts.py 与 xiaomi_tts.py 各持一份逐字相同的 _add_wav_header /
_build_wav_header 实现，收敛于此；两个 provider 类保留同名方法做薄委托，
调用点零改动。
"""

from __future__ import annotations

import io
import struct
import wave

from ..logger import backend_log


def build_wav_header(
    data_size: int,
    sample_rate: int = 24000,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    """手动构建 44 字节标准 WAV 头（RIFF/fmt/data）。"""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    chunk_size = 36 + data_size

    header = struct.pack("<4sI4s", b"RIFF", chunk_size, b"WAVE")
    header += struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += struct.pack("<4sI", b"data", data_size)
    return header


def pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bits: int = 16,
    label: str = "TTS",
) -> bytes:
    """裸 PCM16 数据封装为 WAV 容器；wave 模块失败时回退手动构建头。"""
    if not pcm_data:
        backend_log(f"[{label}] Empty PCM data, cannot build WAV")
        return b""

    if len(pcm_data) % 2 != 0:
        pcm_data = pcm_data + b"\x00"

    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(bits // 8)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
    except Exception as e:
        backend_log(f"[{label}] wave write failed, fallback to manual WAV header: {e}")
        buffer = io.BytesIO()
        buffer.write(build_wav_header(len(pcm_data), sample_rate, channels, bits))
        buffer.write(pcm_data)

    return buffer.getvalue()
