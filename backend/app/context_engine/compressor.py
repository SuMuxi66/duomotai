"""
上下文压缩器

实现智能分层压缩算法，将长对话历史压缩为可管理的上下文
"""
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import re

from .history_manager import ChatTurn, TurnSummary, DeepSummary, CompressedHistory


@dataclass
class CompressionConfig:
    """压缩配置"""
    full_layer_turns: int = 4           # 完整保留的最近轮数
    summary_layer_turns: int = 6         # 中间层保留轮数（压缩为摘要）
    max_tokens: int = 2000               # 最大token数限制
    enable_deep_summary: bool = True     # 是否启用深度压缩
    deep_summary_threshold: int = 10     # 触发深度压缩的轮数阈值
    
    # token估算系数（中文字符 ≈ 1.5 tokens）
    token_per_char: float = 1.5


class ContextCompressor:
    """上下文压缩器"""
    
    def __init__(self, config: Optional[CompressionConfig] = None):
        self.config = config or CompressionConfig()
    
    def estimate_tokens(self, text: str) -> int:
        """估算文本的token数"""
        return int(len(text) * self.config.token_per_char)
    
    def estimate_turn_tokens(self, turn: ChatTurn) -> int:
        """估算一轮对话的token数"""
        total_text = turn.user_message + turn.assistant_message
        return self.estimate_tokens(total_text)
    
    def need_compression(self, turns: List[ChatTurn]) -> bool:
        """判断是否需要压缩"""
        if len(turns) <= self.config.full_layer_turns:
            return False
        
        total_tokens = sum(self.estimate_turn_tokens(t) for t in turns)
        return total_tokens > self.config.max_tokens
    
    def compress(self, turns: List[ChatTurn]) -> CompressedHistory:
        """
        压缩对话历史
        
        策略：
        1. 最近 N 轮完整保留
        2. 中间 M 轮压缩为摘要
        3. 早期历史深度压缩（如果启用且轮数足够）
        """
        if not turns:
            return CompressedHistory()
        
        total_turns = len(turns)
        config = self.config
        
        # 如果轮数较少，不压缩
        if total_turns <= config.full_layer_turns:
            return CompressedHistory(
                full_turns=turns,
                total_original_turns=total_turns
            )
        
        # 计算各层边界
        full_end = total_turns
        full_start = max(0, full_end - config.full_layer_turns)
        
        summary_end = full_start
        summary_start = max(0, summary_end - config.summary_layer_turns)
        
        # 提取完整层
        full_layer = turns[full_start:full_end]
        
        # 提取并压缩中间层
        summary_layer = None
        if summary_end > summary_start:
            middle_turns = turns[summary_start:summary_end]
            summary_layer = self._create_summary(middle_turns)
        
        # 深度压缩早期历史
        deep_summary = None
        if config.enable_deep_summary and summary_start > 0:
            early_turns = turns[:summary_start]
            if len(early_turns) >= 2:  # 至少2轮才做深度压缩
                deep_summary = self._create_deep_summary(early_turns)
        
        return CompressedHistory(
            full_turns=full_layer,
            summary_layer=summary_layer,
            deep_summary=deep_summary,
            total_original_turns=total_turns
        )
    
    def _create_summary(self, turns: List[ChatTurn]) -> TurnSummary:
        """
        创建中间层摘要
        
        使用规则提取关键信息（不调用LLM，保持快速）
        """
        # 收集用户消息和助手回复
        user_messages = [t.user_message for t in turns]
        assistant_messages = [t.assistant_message for t in turns]
        
        # 推断主题（使用第一条用户消息的前20字）
        topic = user_messages[0][:20] + "..." if len(user_messages[0]) > 20 else user_messages[0]
        
        # 推断意图（简单规则：问句为询问，陈述句为分享）
        first_message = user_messages[0]
        if any(first_message.endswith(p) for p in ["?", "？", "吗", "呢", "吧"]):
            user_intent = "询问相关信息"
        elif any(kw in first_message for kw in ["怎么做", "如何做", "步骤"]):
            user_intent = "寻求操作指导"
        elif any(kw in first_message for kw in ["为什么", "什么原因"]):
            user_intent = "寻求原理解释"
        else:
            user_intent = "分享或讨论"
        
        # 提取关键点（每轮的关键句）
        key_points = []
        for i, (user_msg, assistant_msg) in enumerate(zip(user_messages, assistant_messages)):
            # 用户消息取前30字
            user_key = user_msg[:30] + "..." if len(user_msg) > 30 else user_msg
            key_points.append(f"轮{i+1}: 用户询问「{user_key}」")
            
            # 从助手回复提取结论句（含结论词或总结性的句子）
            conclusion = self._extract_conclusion(assistant_msg)
            if conclusion:
                key_points.append(f"  → 结论: {conclusion}")
        
        # 助手结论（取最后一条回复的前50字）
        last_assistant = assistant_messages[-1]
        assistant_conclusion = last_assistant[:50] + "..." if len(last_assistant) > 50 else last_assistant
        
        return TurnSummary(
            topic=topic,
            user_intent=user_intent,
            key_points=key_points[:6],  # 限制关键点数量
            assistant_conclusion=assistant_conclusion,
            original_turn_count=len(turns)
        )
    
    def _extract_conclusion(self, text: str) -> Optional[str]:
        """从文本中提取结论句"""
        # 找包含结论词的句子
        conclusion_keywords = ["结论", "总结", "因此", "所以", "总之", "综上", "建议"]
        
        sentences = re.split(r'[。！？\n]', text)
        for sent in sentences:
            sent = sent.strip()
            if any(kw in sent for kw in conclusion_keywords) and len(sent) > 5:
                return sent[:40] + "..." if len(sent) > 40 else sent
        
        # 如果没有结论句，返回第一句话
        first_sent = sentences[0].strip() if sentences else ""
        return first_sent[:40] + "..." if len(first_sent) > 40 else first_sent
    
    def _create_deep_summary(self, turns: List[ChatTurn]) -> DeepSummary:
        """
        创建深度摘要
        
        将大量早期历史压缩为结构化信息
        """
        user_messages = [t.user_message for t in turns]
        assistant_messages = [t.assistant_message for t in turns]
        
        # 总体概述（统计性描述）
        overview = f"对话涉及 {len(turns)} 轮交流，主要围绕"
        
        # 提取高频关键词作为主题提示
        all_text = " ".join(user_messages + assistant_messages)
        keywords = self._extract_keywords(all_text)
        if keywords:
            overview += "「" + "、".join(keywords[:3]) + "」等主题"
        else:
            overview += "多个话题"
        
        # 提取关键决策点
        key_decisions = []
        for i, turn in enumerate(turns):
            # 检测决策性对话（包含选择、决定、确定等词）
            user_lower = turn.user_message.lower()
            assistant_lower = turn.assistant_message.lower()
            
            decision_indicators = ["选择", "决定", "确定", "选定", "选了", "要", "用"]
            if any(ind in user_lower for ind in decision_indicators):
                decision = f"第{i+1}轮: 用户"
                # 提取决策内容
                for ind in decision_indicators:
                    if ind in turn.user_message:
                        idx = turn.user_message.find(ind)
                        context = turn.user_message[max(0, idx-5):idx+15]
                        decision += context
                        break
                key_decisions.append(decision)
        
        if not key_decisions:
            key_decisions = ["对话主要集中于信息收集和问答"]
        
        # 提取积累的知识/事实
        knowledge = []
        for turn in turns:
            # 从助手回复中提取事实性陈述（包含"是"、"有"、"需要"等）
            facts = self._extract_facts(turn.assistant_message)
            knowledge.extend(facts)
        
        # 去重并限制数量
        knowledge = list(dict.fromkeys(knowledge))[:5]
        
        return DeepSummary(
            overview=overview,
            key_decisions=key_decisions[:3],  # 限制决策点数量
            accumulated_knowledge=knowledge,
            original_turn_count=len(turns)
        )
    
    def _extract_keywords(self, text: str) -> List[str]:
        """简单关键词提取"""
        # 定义停用词
        stop_words = set([
            "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", 
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "那"
        ])
        
        # 提取2-4字的名词性短语（简化处理）
        words = []
        for word in re.findall(r'[\u4e00-\u9fa5]{2,4}', text):
            if word not in stop_words and len(word) >= 2:
                words.append(word)
        
        # 统计频率
        word_freq = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        
        # 返回高频词
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:10]]
    
    def _extract_facts(self, text: str) -> List[str]:
        """从文本中提取事实性陈述"""
        facts = []
        
        # 按句子分割
        sentences = re.split(r'[。！？\n]', text)
        
        # 事实性指示词
        fact_indicators = ["需要", "应该", "必须", "可以", "建议", "注意"]
        
        for sent in sentences:
            sent = sent.strip()
            if any(ind in sent for ind in fact_indicators):
                # 提取包含指示词的片段
                for ind in fact_indicators:
                    if ind in sent:
                        idx = sent.find(ind)
                        # 提取前后文
                        start = max(0, idx - 3)
                        end = min(len(sent), idx + 25)
                        fact = sent[start:end]
                        if len(fact) > 5:
                            facts.append(fact)
                        break
        
        return facts[:3]  # 限制数量
