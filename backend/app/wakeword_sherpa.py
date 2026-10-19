"""
sherpa-onnx 唤醒词检测模块
基于开源词汇关键词检测（KWS），支持自定义唤醒词
真正的 KWS 方案，模型仅 3.3MB，延迟 ~100ms

API 参考:
  kws = sherpa_onnx.KeywordSpotter(tokens=..., encoder=..., decoder=..., joiner=..., keywords_file=...)
  stream = kws.create_stream()
  stream.accept_waveform(sample_rate, audio_float32)
  while kws.is_ready(stream): kws.decode_stream(stream)
  result = kws.get_result(stream)  # 返回字符串
"""

import asyncio
import logging
import os
import numpy as np
from pathlib import Path
from typing import Callable, Optional, List

# 尝试导入 sherpa-onnx
try:
    import sherpa_onnx
    SHERPA_AVAILABLE = True
    logging.info("sherpa-onnx 库已加载")
except ImportError:
    SHERPA_AVAILABLE = False
    logging.warning("sherpa-onnx 未安装，此方案不可用")

# 唤醒词配置：多变体提高召回率
DEFAULT_WAKE_WORDS = ["小云小云", "小云", "晓云", "小芸小芸"]

# 模型缓存目录
MODEL_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model-sherpa")
os.makedirs(MODEL_CACHE_DIR, exist_ok=True)

# 默认模型目录（下载的 wenetspeech 3.3M 模型）
DEFAULT_MODEL_DIR = os.path.join(
    MODEL_CACHE_DIR,
    "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
)


def _chinese_to_kws_format(text: str) -> str:
    """将中文转换为 sherpa-onnx KWS 关键词文件格式。

    格式: pinyin_tokens @chinese_text
    例如: x iǎo y ún x iǎo y ún @小云小云
    """
    from pypinyin import lazy_pinyin, Style

    pinyins = lazy_pinyin(text, style=Style.TONE)
    tokens: list[str] = []
    for py in pinyins:
        if len(py) > 2 and py[:2] in ("zh", "ch", "sh"):
            tokens.append(py[:2])
            tokens.append(py[2:])
        elif len(py) > 1 and py[0] in "bpmfdtnlgkhjqxrzcsyw":
            tokens.append(py[0])
            tokens.append(py[1:])
        else:
            tokens.append(py)
    return " ".join(tokens) + " @" + text


def _generate_keywords_file(wake_words: List[str], model_dir: str) -> str:
    """生成关键词文件"""
    kw_path = os.path.join(model_dir, "keywords_custom.txt")
    with open(kw_path, "w", encoding="utf-8") as f:
        for word in wake_words:
            line = _chinese_to_kws_format(word)
            f.write(line + "\n")
            logging.info(f"[sherpa] 关键词: {line}")
    return kw_path


class SherpaWakeWordDetector:
    """基于 sherpa-onnx 的唤醒词检测器

    特点：
    - 开放式词汇，可自定义任意中文唤醒词
    - 模型体积小（3.3MB）
    - 延迟低（~100ms）
    - 纯 CPU 运行，无需 GPU/NPU
    """

    def __init__(self):
        self.kws: Optional[sherpa_onnx.KeywordSpotter] = None
        self.stream = None  # 持久流，持续接收音频
        self.is_initialized = False
        self.sample_rate = 16000
        self.wake_words: List[str] = DEFAULT_WAKE_WORDS.copy()
        self.on_wake_word_detected: Optional[Callable[[str], None]] = None

        # 音频缓冲区（audio_handler 兼容）
        self.audio_buffer = bytearray()
        self.buffer_duration_ms = 2000
        self.vad_threshold = 30
        self.has_speech = False

    def _calculate_volume(self, pcm_data: bytes) -> float:
        """计算音频音量（PCM16 样本的 RMS 值）"""
        if len(pcm_data) < 2:
            return 0.0
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        return float(rms)

    def _find_model_files(self, model_dir: str) -> dict:
        """在模型目录中查找 encoder/decoder/joiner/tokens 文件"""
        model_path = Path(model_dir)
        # 优先用 int8 量化版（更快）
        encoder = None
        decoder = None
        joiner = None
        tokens = None

        for pattern in ["encoder*int8.onnx", "encoder*.onnx"]:
            files = list(model_path.glob(pattern))
            if files:
                encoder = str(files[0])
                break
        for pattern in ["decoder*int8.onnx", "decoder*.onnx"]:
            files = list(model_path.glob(pattern))
            if files:
                decoder = str(files[0])
                break
        for pattern in ["joiner*int8.onnx", "joiner*.onnx"]:
            files = list(model_path.glob(pattern))
            if files:
                joiner = str(files[0])
                break
        for pattern in ["tokens.txt"]:
            files = list(model_path.glob(pattern))
            if files:
                tokens = str(files[0])
                break

        return {"encoder": encoder, "decoder": decoder, "joiner": joiner, "tokens": tokens}

    async def initialize(
        self,
        model_dir: Optional[str] = None,
        wake_words: Optional[List[str]] = None,
        keywords_file: Optional[str] = None,
    ) -> bool:
        """异步初始化唤醒词检测器

        Args:
            model_dir: 模型目录路径，默认使用 DEFAULT_MODEL_DIR
            wake_words: 唤醒词列表
            keywords_file: 关键词文件路径（覆盖自动生成）

        Returns:
            bool: 初始化是否成功
        """
        if not SHERPA_AVAILABLE:
            logging.error("[sherpa] sherpa-onnx 库不可用")
            return False

        try:
            if wake_words:
                self.wake_words = wake_words

            # 确定模型目录
            effective_model_dir = model_dir or DEFAULT_MODEL_DIR
            if not os.path.isdir(effective_model_dir):
                logging.error(f"[sherpa] 模型目录不存在: {effective_model_dir}")
                return False

            logging.info(f"[sherpa] 使用模型目录: {effective_model_dir}")

            # 查找模型文件
            files = self._find_model_files(effective_model_dir)
            if not all(files.values()):
                logging.error(f"[sherpa] 模型文件不完整: {files}")
                return False

            logging.info(f"[sherpa] encoder: {Path(files['encoder']).name}")

            # 生成或使用关键词文件
            if keywords_file is None:
                keywords_file = _generate_keywords_file(self.wake_words, effective_model_dir)

            # 创建 KeywordSpotter
            self.kws = sherpa_onnx.KeywordSpotter(
                tokens=files["tokens"],
                encoder=files["encoder"],
                decoder=files["decoder"],
                joiner=files["joiner"],
                keywords_file=keywords_file,
                num_threads=4,
                max_active_paths=4,
            )

            # 创建持久流
            self.stream = self.kws.create_stream()

            self.is_initialized = True
            logging.info("[sherpa] 唤醒词检测器初始化成功")
            return True

        except Exception as e:
            logging.error(f"[sherpa] 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def add_audio(self, pcm_data: bytes) -> bool:
        """添加音频数据到缓冲区（VAD 过滤）

        Returns:
            bool: 是否有人声
        """
        volume = self._calculate_volume(pcm_data)
        if volume < self.vad_threshold:
            return False

        if not self.has_speech:
            logging.info(f"[sherpa] 检测到人声，音量: {volume:.1f}")
            self.has_speech = True

        self.audio_buffer.extend(pcm_data)

        # 保持缓冲区大小
        max_bytes = int(self.sample_rate * 2 * self.buffer_duration_ms / 1000)
        if len(self.audio_buffer) > max_bytes:
            self.audio_buffer = self.audio_buffer[-max_bytes:]

        return True

    async def detect(self, pcm_data: bytes) -> bool:
        """异步检测唤醒词

        将音频送入持久流并检测关键词。
        所有音频（含静音）都送入流以保持时间连续性。

        Args:
            pcm_data: PCM16 音频数据（单个块，非整个缓冲区）

        Returns:
            bool: 是否检测到唤醒词
        """
        if not self.is_initialized or not SHERPA_AVAILABLE:
            return False

        try:
            # 转换为 float32 并送入持久流
            audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
            self.stream.accept_waveform(self.sample_rate, audio_array)

            # 解码
            while self.kws.is_ready(self.stream):
                self.kws.decode_stream(self.stream)

            # 检查结果
            result = self.kws.get_result(self.stream)
            if result:
                # result 是匹配到的关键词字符串
                keyword = result.strip() if isinstance(result, str) else str(result).strip()
                if keyword:
                    logging.info(f"[sherpa] 检测到关键词: {keyword}")
                    if self.on_wake_word_detected:
                        self.on_wake_word_detected(keyword)
                    return True

        except Exception as e:
            logging.error(f"[sherpa] 检测错误: {e}")

        return False

    def reset(self):
        """重置检测器状态"""
        self.audio_buffer = bytearray()
        self.has_speech = False
        # 重置流（创建新流以清除状态）
        if self.kws and self.is_initialized:
            try:
                self.stream = self.kws.create_stream()
            except Exception:
                pass

    def close(self):
        """关闭检测器"""
        self.stream = None
        self.kws = None
        self.is_initialized = False


# 全局检测器实例
_sherpa_detector: Optional[SherpaWakeWordDetector] = None


async def get_sherpa_wake_word_detector(
    model_dir: Optional[str] = None,
    wake_words: Optional[List[str]] = None,
) -> Optional[SherpaWakeWordDetector]:
    """获取或创建 sherpa-onnx 唤醒词检测器"""
    global _sherpa_detector

    if _sherpa_detector is None:
        _sherpa_detector = SherpaWakeWordDetector()
        await _sherpa_detector.initialize(model_dir=model_dir, wake_words=wake_words)

    return _sherpa_detector if _sherpa_detector.is_initialized else None


def reset_sherpa_wake_word_detector():
    """重置全局检测器"""
    global _sherpa_detector
    if _sherpa_detector:
        _sherpa_detector.reset()


def set_sherpa_wake_words(wake_words: List[str]):
    """设置唤醒词（需要重新初始化才能生效）"""
    global _sherpa_detector
    if _sherpa_detector and _sherpa_detector.is_initialized:
        logging.warning("[sherpa] 唤醒词将在下次初始化时生效")
    _sherpa_detector = None
