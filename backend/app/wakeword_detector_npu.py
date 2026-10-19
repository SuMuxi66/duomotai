"""
RK3588 NPU 加速的唤醒词检测模块
使用 RKNN 模型进行 NPU 推理
"""

import numpy as np
import logging
import os
import io
import wave
from typing import Callable, Optional

# RKNN 支持
RKNN_AVAILABLE = False
try:
    from rknnlite.api import RKNNLite
    RKNN_AVAILABLE = True
    logging.info("RKNN Lite 库已加载")
except ImportError:
    logging.warning("RKNN Lite 未安装，NPU 加速不可用")

# 模型路径
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model-rknn")
os.makedirs(MODEL_DIR, exist_ok=True)

# NPU 核心配置
NPU_CORE_AUTO = 0  # 自动选择核心
NPU_CORE_0 = 1     # 使用核心 0
NPU_CORE_1 = 2     # 使用核心 1
NPU_CORE_2 = 4     # 使用核心 2
NPU_CORE_ALL = 7   # 使用所有核心


class WakeWordDetectorNPU:
    """基于 RK3588 NPU 的唤醒词检测器"""
    
    def __init__(self):
        self.rknn: Optional[RKNNLite] = None
        self.is_initialized = False
        self.npu_available = False
        self.audio_buffer = bytearray()
        self.buffer_duration_ms = 2000  # 2秒音频缓冲区
        self.sample_rate = 16000
        self.on_wake_word_detected: Optional[Callable[[], None]] = None
        
        # 特征提取参数 (MFCC)
        self.n_mfcc = 13
        self.n_fft = 512
        self.hop_length = 256
        
    def _check_npu_available(self) -> bool:
        """检查 NPU 是否可用"""
        if not RKNN_AVAILABLE:
            return False
        
        try:
            # 尝试创建 RKNN 实例
            rknn = RKNNLite()
            # 尝试加载一个测试模型来验证 NPU 是否工作
            # 这里只是测试 API 是否可用
            del rknn
            return True
        except Exception as e:
            logging.warning(f"NPU 检查失败: {e}")
            return False
    
    async def initialize(self, model_path: Optional[str] = None) -> bool:
        """
        初始化 NPU 唤醒词检测器
        
        Args:
            model_path: RKNN 模型路径，如果为 None 则使用默认路径
            
        Returns:
            bool: 初始化是否成功
        """
        if not RKNN_AVAILABLE:
            logging.error("RKNN Lite 不可用，无法初始化 NPU 检测器")
            return False
        
        # 检查 NPU 是否可用
        if not self._check_npu_available():
            logging.error("NPU 不可用，请检查固件和驱动")
            return False
        
        try:
            logging.info("初始化 RK3588 NPU 唤醒词检测器...")
            
            # 创建 RKNN Lite 实例
            self.rknn = RKNNLite()
            
            # 如果有模型路径，加载模型
            if model_path and os.path.exists(model_path):
                logging.info(f"加载 RKNN 模型: {model_path}")
                ret = self.rknn.load_rknn(model_path)
                if ret != 0:
                    logging.error(f"加载 RKNN 模型失败: {ret}")
                    return False
                
                # 初始化 NPU 运行时
                ret = self.rknn.init_runtime(core_mask=NPU_CORE_AUTO)
                if ret != 0:
                    logging.error(f"初始化 NPU 运行时失败: {ret}")
                    return False
                
                self.npu_available = True
                logging.info("NPU 唤醒词检测器初始化成功")
            else:
                logging.warning(f"RKNN 模型不存在: {model_path}")
                logging.info("等待模型加载...")
                # 即使没有模型，也标记为初始化成功
                # 后续可以动态加载
            
            self.is_initialized = True
            return True
            
        except Exception as e:
            logging.error(f"初始化 NPU 检测器失败: {e}")
            return False
    
    def _extract_features(self, pcm_data: bytes) -> np.ndarray:
        """
        从 PCM 音频数据提取特征
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            np.ndarray: 音频特征
        """
        # 转换为 numpy 数组
        audio = np.frombuffer(pcm_data, dtype=np.int16)
        
        # 归一化
        audio = audio.astype(np.float32) / 32768.0
        
        # 简单的特征提取（能量和过零率）
        # 实际应用中应该使用 MFCC 或更复杂的特征
        frame_size = 512
        hop_size = 256
        
        features = []
        for i in range(0, len(audio) - frame_size, hop_size):
            frame = audio[i:i+frame_size]
            
            # 能量
            energy = np.sum(frame ** 2)
            
            # 过零率
            zcr = np.sum(np.abs(np.diff(np.sign(frame)))) / (2 * len(frame))
            
            features.append([energy, zcr])
        
        return np.array(features, dtype=np.float32)
    
    def _preprocess_audio(self, pcm_data: bytes) -> np.ndarray:
        """
        预处理音频数据为模型输入
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            np.ndarray: 模型输入数据
        """
        # 提取特征
        features = self._extract_features(pcm_data)
        
        # 填充或截断到固定长度
        target_length = 100  # 假设模型需要 100 帧
        if len(features) < target_length:
            # 填充
            padding = np.zeros((target_length - len(features), features.shape[1]), dtype=np.float32)
            features = np.vstack([features, padding])
        else:
            # 截断
            features = features[:target_length]
        
        # 添加 batch 维度
        return np.expand_dims(features, axis=0)
    
    async def detect(self, pcm_data: bytes) -> bool:
        """
        检测唤醒词
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            bool: 是否检测到唤醒词
        """
        if not self.is_initialized:
            return False
        
        if not self.npu_available or self.rknn is None:
            # NPU 不可用，回退到其他检测方式
            return False
        
        try:
            # 预处理音频
            input_data = self._preprocess_audio(pcm_data)
            
            # NPU 推理
            outputs = self.rknn.inference(inputs=[input_data])
            
            # 解析输出
            # 假设输出是一个概率值，表示唤醒词的概率
            if outputs and len(outputs) > 0:
                prob = outputs[0][0][0]  # 根据实际模型输出调整
                
                # 阈值判断
                if prob > 0.8:  # 80% 阈值
                    logging.info(f"NPU 检测到唤醒词，置信度: {prob:.2f}")
                    if self.on_wake_word_detected:
                        self.on_wake_word_detected()
                    return True
            
            return False
            
        except Exception as e:
            logging.error(f"NPU 推理错误: {e}")
            return False
    
    def add_audio(self, pcm_data: bytes) -> bool:
        """
        添加音频数据到缓冲区
        
        Args:
            pcm_data: PCM16 音频数据
            
        Returns:
            bool: 缓冲区是否已累积足够数据，可调用 detect() 进行检测
        """
        # 添加到缓冲区
        self.audio_buffer.extend(pcm_data)
        
        # 保持缓冲区大小（最多 2 秒音频）
        max_bytes = int(self.sample_rate * 2 * 2)  # 16kHz, 16bit, 2秒
        if len(self.audio_buffer) > max_bytes:
            self.audio_buffer = self.audio_buffer[-max_bytes:]
        
        # 每累积约 0.8 秒音频返回 True，提示调用方可调用 detect()
        return len(self.audio_buffer) >= int(self.sample_rate * 2 * 0.8)
    
    def get_audio_buffer(self) -> bytes:
        """获取当前音频缓冲区"""
        return bytes(self.audio_buffer)
    
    def reset_buffer(self):
        """重置音频缓冲区"""
        self.audio_buffer = bytearray()
    
    def release(self):
        """释放 NPU 资源"""
        if self.rknn is not None:
            try:
                self.rknn.release()
            except:
                pass
            self.rknn = None
        self.is_initialized = False
        self.npu_available = False
        logging.info("NPU 资源已释放")


# 创建全局实例
def get_npu_detector() -> WakeWordDetectorNPU:
    """获取 NPU 检测器实例"""
    return WakeWordDetectorNPU()
