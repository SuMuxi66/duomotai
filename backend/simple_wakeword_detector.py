#!/usr/bin/env python3
"""
简化版唤醒词检测器
使用轻量级方法，适合 RK3588 CPU
"""
import numpy as np
import logging
import os
from typing import Callable, Optional

# 尝试导入 sherpa-onnx
try:
    import sherpa_onnx
    SHERPA_AVAILABLE = True
except ImportError:
    SHERPA_AVAILABLE = False

# 尝试导入 FunASR
try:
    from funasr import AutoModel
    FUNASR_AVAILABLE = True
except ImportError:
    FUNASR_AVAILABLE = False

# 唤醒词
WAKE_WORDS = ["小云小云", "小云", "晓云", "你好小云"]
VAD_THRESHOLD = 30


class SimpleWakeWordDetector:
    """简化版唤醒词检测器 - 使用 FunASR + 轻量级关键词匹配"""
    
    def __init__(self):
        self.model = None
        self.is_initialized = False
        self.audio_buffer = bytearray()
        self.sample_rate = 16000
        self.on_wake_word_detected = None
        
        # 检测状态
        self.last_detect_time = 0
        self.cooldown_ms = 2000  # 2秒冷却时间
        
    def _calculate_volume(self, pcm_data: bytes) -> float:
        """计算音量"""
        if len(pcm_data) < 2:
            return 0.0
        audio = np.frombuffer(pcm_data, dtype=np.int16)
        return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    
    def _contains_wakeword(self, text: str) -> bool:
        """检查文本是否包含唤醒词"""
        text = text.lower().replace(" ", "")
        for ww in WAKE_WORDS:
            if ww in text:
                return True
        return False
    
    async def initialize(self):
        """初始化 - 优先使用 FunASR"""
        if not FUNASR_AVAILABLE:
            logging.error("FunASR 不可用")
            return False
        
        try:
            logging.info("初始化简化版唤醒词检测器...")
            
            # 使用轻量级配置
            self.model = AutoModel(
                model="paraformer-zh-streaming",
                model_revision="v2.0.4",
                # 禁用 VAD 和标点，减少计算
                vad_model=None,
                punc_model=None,
                device="cpu",
                # 使用量化模型 (如果支持)
                disable_pbar=True,
            )
            
            self.is_initialized = True
            logging.info("简化版唤醒词检测器初始化成功")
            return True
            
        except Exception as e:
            logging.error(f"初始化失败: {e}")
            return False
    
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
        
        # VAD 检查
        volume = self._calculate_volume(pcm_data)
        if volume < VAD_THRESHOLD:
            return False
        
        try:
            # 保存为临时 wav 文件
            import tempfile
            import wave
            
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                wav_path = f.name
            
            # 写入 wav
            with wave.open(wav_path, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
                wav.writeframes(pcm_data)
            
            # 识别
            result = self.model.generate(input=wav_path, batch_size_s=300)
            
            # 清理临时文件
            os.unlink(wav_path)
            
            if result and len(result) > 0:
                text = result[0].get('text', '')
                
                # 检查唤醒词
                if self._contains_wakeword(text):
                    logging.info(f"检测到唤醒词: {text}")
                    if self.on_wake_word_detected:
                        self.on_wake_word_detected(text)
                    return True
            
            return False
            
        except Exception as e:
            logging.error(f"检测错误: {e}")
            return False
    
    def add_audio(self, pcm_data: bytes) -> bool:
        """同步添加音频接口"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.detect(pcm_data))
        except:
            return False
    
    def get_audio_buffer(self) -> bytes:
        return bytes(self.audio_buffer)
    
    def reset_buffer(self):
        self.audio_buffer = bytearray()
