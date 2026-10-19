"""
FunASR 唤醒词检测模块
用于实时检测唤醒词"小云小云"
支持 CPU 和 RK3588 NPU 两种模式
"""

import asyncio
import io
import numpy as np
import logging
import os
from typing import Callable, Optional

# 设置模型缓存目录
MODEL_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model-funasr")
os.environ["MODELSCOPE_CACHE"] = MODEL_CACHE_DIR
os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
logging.info(f"FunASR model cache directory: {MODEL_CACHE_DIR}")

# 导入 FunASR
try:
    from funasr import AutoModel
    FUNASR_AVAILABLE = True
except ImportError:
    FUNASR_AVAILABLE = False
    logging.warning("FunASR not installed, wake word detection will be disabled")

# 导入 NPU 支持（RK3588）
try:
    # 尝试 rknnlite（轻量级推理库）
    from rknnlite.api import RKNNLite
    RKNNLite_AVAILABLE = True
    RKNN_AVAILABLE = True
    logging.info("RKNN Lite 库已加载，NPU 模式可用")
except ImportError:
    RKNNLite_AVAILABLE = False
    try:
        # 回退到 rknn-toolkit2（开发板也可能装这个）
        from rknn.api import RKNN
        RKNN_AVAILABLE = True
        logging.info("RKNN Toolkit2 已加载，NPU 模式可用")
    except ImportError:
        RKNN_AVAILABLE = False
        logging.warning("RKNN 库未安装，NPU 模式不可用")

# 唤醒词配置
WAKE_WORDS = ["小云小云", "小云", "晓云"]

# VAD 音量阈值（PCM16 样本值），低于此值视为静音
VAD_VOLUME_THRESHOLD = 30

# NPU 模型路径
RKNN_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model-rknn")
os.makedirs(RKNN_MODEL_DIR, exist_ok=True)


class WakeWordDetector:
    """基于 FunASR 的唤醒词检测器（支持 CPU 和 NPU 模式）"""

    def __init__(self):
        self.model: Optional[AutoModel] = None
        self.rknn: Optional[RKNNLite] = None
        self.is_initialized = False
        self.use_npu = False  # 是否使用 NPU
        self.npu_available = False  # NPU 是否可用
        self.audio_buffer = bytearray()
        self.buffer_duration_ms = 2000  # 2秒音频缓冲区
        self.sample_rate = 16000
        self.on_wake_word_detected: Optional[Callable[[], None]] = None
        self.vad_threshold = VAD_VOLUME_THRESHOLD
        self.has_speech = False  # 是否检测到人声
        
    def _check_npu_available(self) -> bool:
        """检查 NPU 是否可用（RK3588）"""
        if not RKNN_AVAILABLE:
            return False
        try:
            # 检查 NPU 设备节点
            npu_devices = ["/dev/rknpu", "/dev/rknpu0", "/dev/rknn"]
            for dev in npu_devices:
                if os.path.exists(dev):
                    return True
            # 检查 RKNN 运行时库是否存在
            if os.path.exists("/usr/lib/librknnrt.so"):
                return True
            # 尝试创建 RKNN 实例来验证
            if RKNNLite_AVAILABLE:
                test_rknn = RKNNLite()
                del test_rknn
            else:
                test_rknn = RKNN()
                del test_rknn
            return True
        except:
            return False

    def _init_npu(self) -> bool:
        """初始化 NPU 模式"""
        try:
            # 查找 RKNN 模型
            rknn_model_path = os.path.join(RKNN_MODEL_DIR, "wakeword.rknn")
            if not os.path.exists(rknn_model_path):
                logging.info(f"RKNN 模型不存在: {rknn_model_path}")
                return False

            # 根据可用库创建 RKNN 实例
            if RKNNLite_AVAILABLE:
                self.rknn = RKNNLite()
                ret = self.rknn.load_rknn(rknn_model_path)
                if ret != 0:
                    logging.error(f"加载 RKNN 模型失败: {ret}")
                    return False
                # 初始化 NPU 运行时
                ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO)
                if ret != 0:
                    logging.error(f"初始化 NPU 运行时失败: {ret}")
                    return False
            else:
                # 使用 rknn-toolkit2 的 RKNN 类
                self.rknn = RKNN()
                ret = self.rknn.load_rknn(rknn_model_path)
                if ret != 0:
                    logging.error(f"加载 RKNN 模型失败: {ret}")
                    return False
                # 初始化 NPU 运行时
                ret = self.rknn.init_runtime(target='rk3588')
                if ret != 0:
                    logging.error(f"初始化 NPU 运行时失败: {ret}")
                    return False

            self.npu_available = True
            self.use_npu = True
            logging.info("NPU 模式初始化成功")
            return True
        except Exception as e:
            logging.error(f"NPU 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _init_cpu(self) -> bool:
        """初始化 CPU 模式（FunASR）"""
        if not FUNASR_AVAILABLE:
            logging.error("FunASR 不可用")
            return False

        try:
            logging.info("Initializing FunASR model for wake word detection...")
            # 自动检测 GPU/CUDA
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logging.info(f"Using device: {device}")
            except:
                device = "cpu"

            # 使用 FunASR 的实时语音识别模型
            self.model = AutoModel(
                model="paraformer-zh-streaming",
                model_revision="v2.0.4",
                vad_model="fsmn-vad",
                vad_model_revision="v2.0.4",
                punc_model="ct-punc",
                punc_model_revision="v2.0.4",
                device=device,
            )
            self.use_npu = False
            logging.info("CPU 模式初始化成功 (FunASR)")
            return True
        except Exception as e:
            logging.error(f"CPU 模式初始化失败: {e}")
            return False

    async def initialize(self, prefer_npu: bool = True):
        """
        异步初始化唤醒词检测器

        Args:
            prefer_npu: 是否优先使用 NPU（如果可用）

        Returns:
            bool: 初始化是否成功
        """
        try:
            # 检查 NPU 可用性
            npu_can_use = self._check_npu_available() if prefer_npu and RKNN_AVAILABLE else False

            if npu_can_use:
                logging.info("检测到 NPU，尝试初始化 NPU 模式...")
                if self._init_npu():
                    self.is_initialized = True
                    logging.info("唤醒词检测器初始化成功 (NPU 模式)")
                    return True
                else:
                    logging.warning("NPU 初始化失败，回退到 CPU 模式...")
            else:
                logging.info("NPU 不可用或未启用，使用 CPU 模式")

            # 回退到 CPU 模式
            if self._init_cpu():
                self.is_initialized = True
                logging.info("唤醒词检测器初始化成功 (CPU 模式)")
                return True
            else:
                logging.error("唤醒词检测器初始化失败")
                return False

        except Exception as e:
            logging.error(f"初始化唤醒词检测器时发生错误: {e}")
            return False
    
    def _calculate_volume(self, pcm_data: bytes) -> float:
        """计算音频音量（PCM16 样本的 RMS 值）"""
        if len(pcm_data) < 2:
            return 0.0
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        # 计算 RMS（均方根）音量
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        return float(rms)
    
    def add_audio(self, pcm_data: bytes) -> bool:
        """
        添加音频数据到缓冲区并检测唤醒词
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            bool: 是否检测到唤醒词
        """
        if not self.is_initialized or not FUNASR_AVAILABLE:
            return False
        
        # VAD 音量检测
        volume = self._calculate_volume(pcm_data)
        if volume < self.vad_threshold:
            # 音量太低，丢弃数据（但不清空已有缓冲区）
            logging.debug(f"[VAD] 音量 {volume:.1f} < 阈值 {self.vad_threshold}，忽略静音")
            return False
        
        # 检测到人声
        if not self.has_speech:
            logging.info(f"[VAD] 检测到人声，音量: {volume:.1f}")
            self.has_speech = True
            
        self.audio_buffer.extend(pcm_data)
        
        # 保持缓冲区大小（最多2秒音频）
        max_bytes = int(self.sample_rate * 2 * self.buffer_duration_ms / 1000)
        if len(self.audio_buffer) > max_bytes:
            self.audio_buffer = self.audio_buffer[-max_bytes:]
        
        return False
    
    async def detect(self, pcm_data: bytes) -> bool:
        """
        异步检测唤醒词
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            bool: 是否检测到唤醒词
        """
        if not self.is_initialized or not FUNASR_AVAILABLE:
            return False
        
        # VAD 音量检测（二次检查，确保安全）
        volume = self._calculate_volume(pcm_data)
        if volume < self.vad_threshold:
            logging.debug(f"[VAD] 检测时音量 {volume:.1f} < 阈值 {self.vad_threshold}，跳过")
            return False
            
        try:
            # 将字节转换为 numpy 数组
            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
            
            # 调用 FunASR 进行识别
            result = self.model.generate(
                input=audio_array,
                sample_rate=self.sample_rate,
                language="zh",
                use_itn=True,
            )
            
            if result and len(result) > 0:
                text = result[0].get("text", "").strip()
                if text:
                    logging.debug(f"FunASR recognized: {text}")
                    return self._check_wake_word(text)
                    
        except Exception as e:
            logging.error(f"Wake word detection error: {e}")
            
        return False
    
    def _check_wake_word(self, text: str) -> bool:
        """检查文本是否包含唤醒词"""
        text = text.replace(" ", "").replace("。", "").replace("，", "")
        
        for wake_word in WAKE_WORDS:
            if wake_word in text:
                logging.info(f"Wake word detected: {wake_word} in text: {text}")
                if self.on_wake_word_detected:
                    self.on_wake_word_detected()
                return True
                
        return False
    
    def reset(self):
        """重置检测器状态"""
        self.audio_buffer = bytearray()
        self.has_speech = False
        
    def close(self):
        """关闭检测器"""
        if self.model:
            # FunASR 模型没有显式关闭方法
            pass
        self.is_initialized = False


# 全局检测器实例
_wake_word_detector: Optional[WakeWordDetector] = None

async def get_wake_word_detector() -> Optional[WakeWordDetector]:
    """获取或创建全局唤醒词检测器"""
    global _wake_word_detector
    
    if _wake_word_detector is None:
        _wake_word_detector = WakeWordDetector()
        await _wake_word_detector.initialize()
        
    return _wake_word_detector if _wake_word_detector.is_initialized else None

def reset_wake_word_detector():
    """重置全局检测器"""
    global _wake_word_detector
    if _wake_word_detector:
        _wake_word_detector.reset()
