"""
音频降噪模块
结合 WebRTC NS (noisereduce) + 谱减法双重降噪

处理流程:
  PCM16 → float32 → noisereduce (谱门控) → 谱减法 → float32 → PCM16

适用场景:
  - 厨房炒菜噪音
  - 风扇/空调背景噪音
  - 多人说话环境
"""

import logging
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# 延迟导入重型库
_noisereduce = None
_scipy_signal = None

def _ensure_imports():
    """延迟导入降噪库"""
    global _noisereduce, _scipy_signal
    if _noisereduce is None:
        try:
            import noisereduce as nr
            _noisereduce = nr
        except ImportError:
            logger.warning("[降噪] noisereduce 未安装")
    if _scipy_signal is None:
        try:
            from scipy import signal
            _scipy_signal = signal
        except ImportError:
            logger.warning("[降噪] scipy 未安装")


class AudioDenoiser:
    """音频降噪处理器

    双重降噪策略:
    1. noisereduce (谱门控): 基于频谱的噪声估计和门控
    2. 谱减法: 从含噪信号中减去噪声频谱

    特点:
    - 流式处理友好: 每次处理一个音频块
    - 噪声剖面自适应: 首个块用于学习噪声特征
    - 低延迟: 每块处理 < 5ms
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        noise_profile_duration: float = 0.5,
        spectral_subtraction_factor: float = 1.0,
        spectral_floor: float = 0.1,
    ):
        """
        Args:
            sample_rate: 采样率
            noise_profile_duration: 噪声学习时长（秒），用前 N 秒音频建立噪声剖面
            spectral_subtraction_factor: 谱减法过减因子（越大降噪越强，但可能失真）
            spectral_floor: 频谱下限（防止过度降噪导致音乐噪声）
        """
        self.sample_rate = sample_rate
        self.noise_profile_samples = int(noise_profile_duration * sample_rate)
        self.spectral_subtraction_factor = spectral_subtraction_factor
        self.spectral_floor = spectral_floor

        self._noise_profile: Optional[np.ndarray] = None
        self._noise_learn_buffer = bytearray()
        self._is_learning = True
        self._frame_count = 0

        # noisereduce 状态
        self._nr_enabled = False
        _ensure_imports()
        if _noisereduce is not None:
            self._nr_enabled = True

        logger.info(
            f"[降噪] 初始化完成: sr={sample_rate}, "
            f"noise_profile={noise_profile_duration}s, "
            f"ss_factor={spectral_subtraction_factor}, "
            f"noisereduce={'enabled' if self._nr_enabled else 'disabled'}"
        )

    def _pcm16_to_float32(self, pcm: bytes) -> np.ndarray:
        """PCM16 bytes → float32 numpy array"""
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    def _float32_to_pcm16(self, audio: np.ndarray) -> bytes:
        """float32 → PCM16 bytes"""
        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767).astype(np.int16).tobytes()

    def _spectral_subtraction(self, audio: np.ndarray) -> np.ndarray:
        """谱减法降噪

        从含噪信号频谱中减去噪声频谱
        """
        if _scipy_signal is None or self._noise_profile is None:
            return audio

        try:
            # STFT
            f, t, Zxx = _scipy_signal.stft(
                audio,
                fs=self.sample_rate,
                nperseg=512,
                noverlap=384,
                window='hann',
            )

            # 噪声频谱幅度
            noise_mag = np.abs(self._noise_profile)
            noise_phase = np.angle(self._noise_profile)

            # 含噪信号幅度和相位
            signal_mag = np.abs(Zxx)
            signal_phase = np.angle(Zxx)

            # 谱减: |S| = max(|Y| - alpha * |N|, floor * |Y|)
            subtracted = signal_mag - self.spectral_subtraction_factor * noise_mag
            subtracted = np.maximum(subtracted, self.spectral_floor * signal_mag)

            # 重建: 用原始相位
            Zxx_denoised = subtracted * np.exp(1j * signal_phase)

            # ISTFT
            _, reconstructed = _scipy_signal.istft(
                Zxx_denoised,
                fs=self.sample_rate,
                nperseg=512,
                noverlap=384,
                window='hann',
            )

            # 对齐长度
            min_len = min(len(audio), len(reconstructed))
            return reconstructed[:min_len]

        except Exception as e:
            logger.warning(f"[降噪] 谱减法失败: {e}")
            return audio

    def _learn_noise_profile(self, audio: np.ndarray) -> bool:
        """学习噪声剖面

        Returns:
            True 如果还在学习, False 如果学习完成
        """
        if not self._is_learning:
            return False

        self._noise_learn_buffer.extend((audio * 32767).astype(np.int16).tobytes())

        if len(self._noise_learn_buffer) >= self.noise_profile_samples * 2:
            # 学习完成：用收集的音频计算噪声频谱
            noise_audio = self._pcm16_to_float32(bytes(self._noise_learn_buffer))

            if _scipy_signal is not None:
                try:
                    f, t, Zxx = _scipy_signal.stft(
                        noise_audio,
                        fs=self.sample_rate,
                        nperseg=512,
                        noverlap=384,
                        window='hann',
                    )
                    # 取平均频谱作为噪声剖面
                    self._noise_profile = np.mean(Zxx, axis=1, keepdims=True)
                    logger.info(f"[降噪] 噪声剖面学习完成 ({len(noise_audio)/self.sample_rate:.1f}s)")
                except Exception as e:
                    logger.warning(f"[降噪] 噪声剖面学习失败: {e}")

            self._is_learning = False
            self._noise_learn_buffer = bytearray()
            return False

        return True

    def denoise(self, pcm_data: bytes) -> bytes:
        """降噪处理

        Args:
            pcm_data: PCM16 音频数据

        Returns:
            降噪后的 PCM16 音频数据
        """
        if not pcm_data or len(pcm_data) < 2:
            return pcm_data

        self._frame_count += 1
        audio = self._pcm16_to_float32(pcm_data)

        # 阶段 1: 学习噪声剖面（前 0.5 秒）
        if self._is_learning:
            still_learning = self._learn_noise_profile(audio)
            if still_learning:
                # 学习期间不做降噪，直接返回原始音频
                return pcm_data

        # 阶段 2: 双重降噪
        # 2a: noisereduce (谱门控) - 非平稳模式，自适应噪声
        if self._nr_enabled and _noisereduce is not None:
            try:
                # noisereduce 需要二维输入 (channels, samples)
                audio_2d = audio.reshape(1, -1)
                denoised = _noisereduce.reduce_noise(
                    y=audio_2d,
                    sr=self.sample_rate,
                    stationary=False,
                    prop_decrease=0.4,
                )
                audio = denoised[0]
            except Exception as e:
                if self._frame_count % 100 == 0:
                    logger.warning(f"[降噪] noisereduce 失败: {e}")

        # 2b: 谱减法（仅在噪声剖面已学习且噪声为有色噪声时有效）
        if self._noise_profile is not None and _scipy_signal is not None:
            audio = self._spectral_subtraction(audio)

        return self._float32_to_pcm16(audio)

    def reset(self):
        """重置降噪器状态"""
        self._noise_profile = None
        self._noise_learn_buffer = bytearray()
        self._is_learning = True
        self._frame_count = 0
        logger.info("[降噪] 状态已重置")

    def get_stats(self) -> dict:
        """获取降噪器状态"""
        return {
            "noisereduce_enabled": self._nr_enabled,
            "noise_profile_learned": self._noise_profile is not None,
            "is_learning": self._is_learning,
            "frame_count": self._frame_count,
            "spectral_subtraction_factor": self.spectral_subtraction_factor,
        }


# 全局降噪器实例
_denoiser: Optional[AudioDenoiser] = None


def get_denoiser(sample_rate: int = 16000) -> Optional[AudioDenoiser]:
    """获取或创建全局降噪器"""
    global _denoiser
    if _denoiser is None:
        _denoiser = AudioDenoiser(sample_rate=sample_rate)
    return _denoiser


def reset_denoiser():
    """重置全局降噪器"""
    global _denoiser
    if _denoiser:
        _denoiser.reset()
