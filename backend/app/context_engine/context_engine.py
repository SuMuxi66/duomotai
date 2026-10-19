"""
上下文引擎主类

提供统一的上下文管理接口，整合历史管理和压缩功能
"""
from typing import List, Dict, Any, Optional, Literal
import asyncio
import time

from ..config import settings
from ..logger import backend_log
from . import vector_store
from .history_manager import HistoryManager, ChatTurn, CompressedHistory
from .compressor import ContextCompressor, CompressionConfig


SceneType = Literal["general", "cooking", "vision"]


MAX_CACHED_SESSIONS = 20

# ===== 向量召回参数 =====
VECTOR_TOP_K = 3                    # 最多召回的早期相关轮数
VECTOR_MIN_SIMILARITY = 0.5         # 余弦相似度阈值（bge-m3 中文相关句通常 >0.6）
VECTOR_ASSISTANT_EXCERPT = 160      # 召回轮的助手回复截断长度（控 token）


class ContextEngine:
    """
    上下文引擎
    
    统一的对话历史管理入口，支持：
    1. 记录用户和助手消息
    2. 自动触发压缩
    3. 构建LLM可用的上下文
    4. 场景感知的上下文策略
    """
    
    def __init__(self, config: Optional[CompressionConfig] = None):
        """
        初始化上下文引擎
        
        Args:
            config: 压缩配置，默认使用推荐配置
        """
        self.config = config or self._default_config()
        self.compressor = ContextCompressor(self.config)
        self._history_cache: Dict[str, HistoryManager] = {}  # session_id -> HistoryManager
        self.vector_degraded = False   # embedding 失败后置位，回落启发式压缩（成功后复位）
        # 测试注入点：async (texts) -> list[np.ndarray] | None；None = 用 SiliconFlow 真实接口
        self._embed_override = None
    
    @staticmethod
    def _default_config() -> CompressionConfig:
        """默认配置"""
        return CompressionConfig(
            full_layer_turns=4,
            summary_layer_turns=6,
            max_tokens=2000,
            enable_deep_summary=True,
            deep_summary_threshold=10
        )
    
    def _get_history_manager(self, session_id: str) -> HistoryManager:
        """获取或创建历史管理器"""
        if session_id not in self._history_cache:
            # 淘汰最旧的缓存
            if len(self._history_cache) >= MAX_CACHED_SESSIONS:
                oldest_key = next(iter(self._history_cache))
                del self._history_cache[oldest_key]
            self._history_cache[session_id] = HistoryManager(max_raw_turns=100)
        return self._history_cache[session_id]
    
    def record_user_message(
        self, 
        session_id: str, 
        content: str, 
        scene_type: SceneType = "general",
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        记录用户消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            scene_type: 场景类型
            metadata: 额外元数据（如场景状态、传感器数据等）
        """
        manager = self._get_history_manager(session_id)
        
        # 合并场景信息到metadata
        final_metadata = {"scene_type": scene_type, "timestamp": time.time()}
        if metadata:
            final_metadata.update(metadata)
        
        manager.add_user_message(content, final_metadata)
    
    def record_assistant_message(
        self, 
        session_id: str, 
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        记录助手消息，完成一轮对话
        
        Args:
            session_id: 会话ID
            content: 消息内容
            metadata: 额外元数据
        """
        manager = self._get_history_manager(session_id)
        manager.add_assistant_message(content, metadata)
    
    def build_history(
        self, 
        session_id: str,
        max_tokens: Optional[int] = None,
        scene_type: SceneType = "general",
        extra_context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, str]]:
        """
        构建LLM可用的历史消息列表
        
        Args:
            session_id: 会话ID
            max_tokens: 最大token数（覆盖默认配置）
            scene_type: 场景类型
            extra_context: 额外上下文（如当前菜谱、步骤等）
        
        Returns:
            LLM消息列表，每个消息为 {"role": str, "content": str}
        """
        manager = self._get_history_manager(session_id)
        turns = manager.get_all_turns()
        
        if not turns:
            return []
        
        # 创建临时压缩器以使用指定的max_tokens，避免修改共享配置
        if max_tokens:
            temp_config = CompressionConfig(
                full_layer_turns=self.config.full_layer_turns,
                summary_layer_turns=self.config.summary_layer_turns,
                max_tokens=max_tokens,
                enable_deep_summary=self.config.enable_deep_summary,
                deep_summary_threshold=self.config.deep_summary_threshold,
            )
            temp_compressor = ContextCompressor(temp_config)
        else:
            temp_compressor = self.compressor

        # 判断是否需要压缩
        if temp_compressor.need_compression(turns):
            # 执行压缩
            compressed = temp_compressor.compress(turns)
            messages = compressed.to_messages()
        else:
            # 不需要压缩，直接转换所有轮次
            messages = []
            for turn in turns:
                messages.extend(turn.to_messages())
        
        # 添加场景特定的系统上下文（如果需要）
        messages = self._with_scene_context(messages, scene_type, extra_context)

        return messages

    def _with_scene_context(
        self,
        messages: List[Dict[str, str]],
        scene_type: SceneType,
        extra_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """做饭场景把菜谱/步骤/份数作为 system 消息插到最前"""
        if scene_type == "cooking" and extra_context:
            cooking_context = self._build_cooking_context(extra_context)
            if cooking_context:
                messages.insert(0, {"role": "system", "content": cooking_context})
        return messages

    # ===== 向量召回（方案1：超窗历史语义检索，替代启发式摘要） =====

    @staticmethod
    def _turn_text(turn: ChatTurn) -> str:
        """轮次的向量化输入文本"""
        return f"用户：{turn.user_message}\n助手：{(turn.assistant_message or '')[:300]}"

    async def _embed_batch(self, texts: List[str]) -> Optional[list]:
        """批量 embedding；_embed_override 为测试注入点。失败返回 None。"""
        if self._embed_override is not None:
            return await self._embed_override(texts)
        api_key = settings.siliconflow_api_key
        if not settings.context_vector_enabled or not api_key:
            return None
        return await vector_store.embed_texts(
            texts, api_key, settings.embedding_model, settings.embedding_base_url
        )

    async def _ensure_turn_embeddings(self, session_id: str, turns: List[ChatTurn]) -> bool:
        """为缺向量的轮次批量补 embedding；失败置 degraded 并返回 False。"""
        missing = [t for t in turns if t.embedding is None]
        if not missing:
            return True
        vecs = await self._embed_batch([self._turn_text(t) for t in missing])
        if vecs is None:
            self.vector_degraded = True
            return False
        self.vector_degraded = False
        for turn, vec in zip(missing, vecs):
            turn.embedding = vec
        return True

    async def warm_vector_index(self, session_id: str) -> None:
        """后台预热：为会话全部轮次补向量（重连恢复后调用，一次批量 API）"""
        if not settings.context_vector_enabled or self.vector_degraded:
            return
        try:
            turns = self._get_history_manager(session_id).get_all_turns()
            await self._ensure_turn_embeddings(session_id, turns)
        except Exception as exc:
            backend_log(f"[向量记忆] 索引预热失败: {exc}")

    async def _retrieve_relevant(
        self, session_id: str, turns: List[ChatTurn], full_layer_turns: int
    ) -> Optional[List[tuple]]:
        """语义检索早期相关轮；None = 检索失败（走回落），[] = 成功但无相关轮。"""
        full_start = max(0, len(turns) - full_layer_turns)
        older = turns[:full_start]
        if not older:
            return None

        # 查询 = 当前待回复的用户消息（还没有成轮），兜底用最近一轮
        pending = self._get_history_manager(session_id).get_pending_user_message()
        query_text = f"用户：{pending}" if pending else self._turn_text(turns[-1])

        # 查询向量与缺失的轮次向量合并成一次批量调用
        texts = [query_text] if pending else []
        missing = [t for t in turns if t.embedding is None]
        texts.extend(self._turn_text(t) for t in missing)
        vecs = await self._embed_batch(texts)
        if vecs is None:
            self.vector_degraded = True
            return None
        self.vector_degraded = False
        it = iter(vecs)
        qvec = next(it) if pending else None
        for turn in missing:
            turn.embedding = next(it)
        if qvec is None:
            qvec = turns[-1].embedding
        if qvec is None:
            return None

        candidates = [(i, t.embedding) for i, t in enumerate(older) if t.embedding is not None]
        hits = vector_store.cosine_topk(qvec, candidates, VECTOR_TOP_K, VECTOR_MIN_SIMILARITY)
        return [(older[i], sim) for i, sim in hits]

    @staticmethod
    def _retrieval_block(retrieved: List[tuple]) -> Dict[str, str]:
        """召回轮格式化为一条 system 消息（时间戳 + 截断摘要，控 token）"""
        lines = ["[早期相关对话回忆]（按语义相关性检索自更早历史；结论可能已过时，以最近对话为准）"]
        for turn, sim in retrieved:
            ts = time.strftime("%m-%d %H:%M", time.localtime(turn.timestamp))
            lines.append(f"- [{ts} 相似度{sim:.2f}] 用户：{turn.user_message[:100]}")
            excerpt = (turn.assistant_message or "")[:VECTOR_ASSISTANT_EXCERPT]
            lines.append(f"  助手：{excerpt}")
        return {"role": "system", "content": "\n".join(lines)}

    async def build_history_async(
        self,
        session_id: str,
        max_tokens: Optional[int] = None,
        scene_type: SceneType = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """build_history 的向量召回变体。

        - 会话很短（无需压缩）→ 与同步版完全一致，零额外开销
        - 超窗 → 最近 full_layer_turns 轮原文 + 语义检索 top-k 早期相关轮
        - embedding 失败/未启用 → 自动回落启发式压缩（原行为）
        """
        manager = self._get_history_manager(session_id)
        turns = manager.get_all_turns()
        if not turns:
            return []

        if max_tokens:
            temp_config = CompressionConfig(
                full_layer_turns=self.config.full_layer_turns,
                summary_layer_turns=self.config.summary_layer_turns,
                max_tokens=max_tokens,
                enable_deep_summary=self.config.enable_deep_summary,
                deep_summary_threshold=self.config.deep_summary_threshold,
            )
            temp_compressor = ContextCompressor(temp_config)
        else:
            temp_compressor = self.compressor

        # 无需压缩：全量轮次直出（与同步版一致，零向量开销）
        if not temp_compressor.need_compression(turns):
            messages: List[Dict[str, str]] = []
            for turn in turns:
                messages.extend(turn.to_messages())
            return self._with_scene_context(messages, scene_type, extra_context)

        # 向量召回分支
        if settings.context_vector_enabled and not self.vector_degraded:
            try:
                retrieved = await self._retrieve_relevant(
                    session_id, turns, self.config.full_layer_turns
                )
            except Exception as exc:
                backend_log(f"[向量记忆] 检索异常，回落启发式: {exc}")
                retrieved = None

            if retrieved is not None:
                recent = turns[-self.config.full_layer_turns:]
                messages = []
                if retrieved:
                    messages.append(self._retrieval_block(retrieved))
                for turn in recent:
                    messages.extend(turn.to_messages())
                return self._with_scene_context(messages, scene_type, extra_context)

        # 回落：启发式压缩（原行为）
        compressed = temp_compressor.compress(turns)
        messages = compressed.to_messages()
        return self._with_scene_context(messages, scene_type, extra_context)
    
    def _build_cooking_context(self, extra_context: Dict[str, Any]) -> str:
        """构建做饭场景的系统上下文"""
        parts = ["[当前做饭场景信息]"]
        
        recipe = extra_context.get("recipe", {})
        if recipe:
            parts.append(f"菜谱: {recipe.get('name', '未选择')}")
        
        step = extra_context.get("step", 0)
        total_steps = extra_context.get("total_steps", 0)
        if step > 0:
            parts.append(f"当前步骤: {step}/{total_steps}")
        
        servings = extra_context.get("servings")
        if servings:
            parts.append(f"份数: {servings}")
        
        return "\n".join(parts)
    
    def get_stats(self, session_id: str) -> Dict[str, Any]:
        """
        获取会话统计信息
        
        Returns:
            包含总轮数、是否压缩、预估token数等信息的字典
        """
        manager = self._get_history_manager(session_id)
        turns = manager.get_all_turns()
        
        total_tokens = sum(
            self.compressor.estimate_turn_tokens(t) 
            for t in turns
        )
        
        return {
            "total_turns": len(turns),
            "total_tokens": total_tokens,
            "need_compression": self.compressor.need_compression(turns),
            "full_layer_turns": self.config.full_layer_turns,
            "summary_layer_turns": self.config.summary_layer_turns,
        }
    
    def clear_history(self, session_id: str) -> None:
        """清空指定会话的历史"""
        if session_id in self._history_cache:
            self._history_cache[session_id].clear()
    
    def reset_session(self, session_id: str) -> None:
        """重置会话（清空历史并移除缓存）"""
        if session_id in self._history_cache:
            del self._history_cache[session_id]
    
    def get_recent_turns(self, session_id: str, n: int) -> List[ChatTurn]:
        """获取最近n轮对话"""
        manager = self._get_history_manager(session_id)
        return manager.get_recent_turns(n)
