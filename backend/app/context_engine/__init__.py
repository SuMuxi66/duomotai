"""
上下文引擎 - 全局对话历史管理与智能压缩

提供统一的对话历史管理服务，支持：
1. 所有场景记录对话历史
2. 智能分层压缩（完整层、摘要层、深度层）
3. 场景感知的上下文构建
"""

from .history_manager import HistoryManager, ChatTurn, CompressedHistory
from .compressor import ContextCompressor, CompressionConfig
from .context_engine import ContextEngine

__all__ = [
    "HistoryManager",
    "ChatTurn", 
    "CompressedHistory",
    "ContextCompressor",
    "CompressionConfig",
    "ContextEngine",
]
