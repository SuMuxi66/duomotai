"""
历史记录管理器

管理对话历史的存储、查询和基础操作
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
import time


@dataclass
class ChatTurn:
    """一轮对话（用户+助手）"""
    user_message: str
    assistant_message: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 轮次文本向量（context_engine 向量检索用；仅内存态，不随对话落盘）
    embedding: Optional[List[float]] = None
    
    def to_messages(self) -> List[Dict[str, str]]:
        """转换为消息列表格式"""
        return [
            {"role": "user", "content": self.user_message},
            {"role": "assistant", "content": self.assistant_message}
        ]


@dataclass
class TurnSummary:
    """对话轮摘要"""
    topic: str                            # 主题
    user_intent: str                      # 用户意图
    key_points: List[str]                 # 关键点
    assistant_conclusion: str             # 助手结论
    original_turn_count: int              # 原始轮数
    timestamp: float = field(default_factory=time.time)
    
    def to_system_message(self) -> Dict[str, str]:
        """转换为系统消息格式"""
        content = f"""[历史对话摘要 - 主题: {self.topic}]
用户意图: {self.user_intent}
关键信息:
"""
        for point in self.key_points:
            content += f"- {point}\n"
        content += f"""\n助手结论: {self.assistant_conclusion}
[该摘要包含 {self.original_turn_count} 轮对话的要点]"""
        
        return {"role": "system", "content": content}


@dataclass
class DeepSummary:
    """深度摘要（多轮合并）"""
    overview: str                         # 总体概述
    key_decisions: List[str]             # 关键决策点
    accumulated_knowledge: List[str]     # 积累的知识/事实
    original_turn_count: int
    timestamp: float = field(default_factory=time.time)
    
    def to_system_message(self) -> Dict[str, str]:
        """转换为系统消息格式"""
        content = f"""[早期对话深度摘要]
总体概述: {self.overview}

关键决策:
"""
        for decision in self.key_decisions:
            content += f"- {decision}\n"
        
        if self.accumulated_knowledge:
            content += "\n已确认的信息:\n"
            for fact in self.accumulated_knowledge:
                content += f"- {fact}\n"
        
        content += f"\n[该摘要包含 {self.original_turn_count} 轮早期对话的核心信息]"
        
        return {"role": "system", "content": content}


@dataclass
class CompressedHistory:
    """压缩后的历史结构"""
    full_turns: List[ChatTurn] = field(default_factory=list)           # 最近完整对话
    summary_layer: Optional[TurnSummary] = None                         # 中间层摘要
    deep_summary: Optional[DeepSummary] = None                          # 早期深度摘要
    total_original_turns: int = 0
    
    def to_messages(self) -> List[Dict[str, str]]:
        """转换为LLM可用的消息列表"""
        messages = []
        
        # 早期深度摘要（如果有）
        if self.deep_summary:
            messages.append(self.deep_summary.to_system_message())
        
        # 中间层摘要（如果有）
        if self.summary_layer:
            messages.append(self.summary_layer.to_system_message())
        
        # 最近完整对话
        for turn in self.full_turns:
            messages.extend(turn.to_messages())
        
        return messages
    
    def estimate_tokens(self) -> int:
        """预估token数（简化估算：1字≈1.5token）"""
        total_chars = 0
        
        if self.deep_summary:
            total_chars += len(self.deep_summary.overview)
            total_chars += sum(len(d) for d in self.deep_summary.key_decisions)
            total_chars += sum(len(k) for k in self.deep_summary.accumulated_knowledge)
        
        if self.summary_layer:
            total_chars += len(self.summary_layer.topic)
            total_chars += len(self.summary_layer.user_intent)
            total_chars += sum(len(p) for p in self.summary_layer.key_points)
            total_chars += len(self.summary_layer.assistant_conclusion)
        
        for turn in self.full_turns:
            total_chars += len(turn.user_message) + len(turn.assistant_message)
        
        return int(total_chars * 1.5)


class HistoryManager:
    """对话历史管理器"""
    
    def __init__(self, max_raw_turns: int = 100):
        """
        Args:
            max_raw_turns: 最大保留原始轮数，超过则触发压缩
        """
        self.max_raw_turns = max_raw_turns
        self._turns: List[ChatTurn] = []
        self._pending_user_message: Optional[str] = None  # 待配对的用户消息
    
    def add_user_message(self, content: str, metadata: Dict[str, Any] = None) -> None:
        """添加用户消息（等待助手回复配对）"""
        self._pending_user_message = content
        if metadata:
            # 暂存metadata，等配对时使用
            self._pending_metadata = metadata
        else:
            self._pending_metadata = {}
    
    def add_assistant_message(self, content: str, metadata: Dict[str, Any] = None) -> None:
        """添加助手消息，完成一轮配对"""
        if self._pending_user_message is None:
            raise ValueError("没有待配对的用户消息，请先调用 add_user_message")
        
        # 合并metadata
        final_metadata = self._pending_metadata.copy()
        if metadata:
            final_metadata.update(metadata)
        
        turn = ChatTurn(
            user_message=self._pending_user_message,
            assistant_message=content,
            timestamp=time.time(),
            metadata=final_metadata
        )
        
        self._turns.append(turn)
        self._pending_user_message = None
        self._pending_metadata = {}
        
        # 清理旧数据
        self._cleanup_old_turns()
    
    def _cleanup_old_turns(self) -> None:
        """清理过旧的轮次，只保留最近的一定数量"""
        if len(self._turns) > self.max_raw_turns:
            # 保留最近的，删除早期的
            self._turns = self._turns[-self.max_raw_turns:]
    
    def get_recent_turns(self, n: int) -> List[ChatTurn]:
        """获取最近 n 轮对话"""
        return self._turns[-n:] if n > 0 else []
    
    def get_all_turns(self) -> List[ChatTurn]:
        """获取所有对话轮"""
        return self._turns.copy()
    
    def get_turn_count(self) -> int:
        """获取总轮数"""
        return len(self._turns)
    
    def clear(self) -> None:
        """清空历史"""
        self._turns.clear()
        self._pending_user_message = None
        self._pending_metadata = {}
    
    def is_waiting_for_assistant(self) -> bool:
        """检查是否有待配对的用户消息"""
        return self._pending_user_message is not None

    def get_pending_user_message(self) -> Optional[str]:
        """获取待配对的用户消息内容（向量检索把它当查询词）"""
        return self._pending_user_message
    
    def cancel_pending(self) -> None:
        """取消待配对的用户消息"""
        self._pending_user_message = None
        self._pending_metadata = {}
