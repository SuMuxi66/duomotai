"""
全局倒计时管理器
支持任何场景创建倒计时，所有倒计时统一管理和广播
优化性能：后端只管理倒计时状态，前端负责UI更新
"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class Timer:
    """倒计时对象"""
    id: str
    description: str
    total_seconds: int
    end_time: float  # Unix timestamp (seconds)
    created_at: float
    category: str  # "cooking", "general", "reminder" 等
    session_id: str = "default"  # 所属会话（多会话隔离，防止跨会话串台）
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典，用于JSON序列化"""
        return {
            "id": self.id,
            "description": self.description,
            "total_seconds": self.total_seconds,
            "end_time": self.end_time,
            "created_at": self.created_at,
            "category": self.category,
            "session_id": self.session_id,
            "metadata": self.metadata,
            "remaining_seconds": max(0, int(self.end_time - time.time())),
        }


class TimerManager:
    """
    全局倒计时管理器（单例模式）
    
    特点：
    1. 支持任何场景创建倒计时
    2. 所有倒计时统一存储和管理
    3. 后端只维护状态，前端负责UI渲染
    4. 支持倒计时完成回调
    5. 支持按类别筛选倒计时
    """
    
    _instance: Optional['TimerManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._timers: Dict[str, Timer] = {}  # timer_id -> Timer
        self._callbacks: Dict[str, List[Callable]] = {}  # timer_id -> [callbacks]
        self._broadcast_callbacks: List[Callable] = []  # 全局广播回调
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()  # 将锁移到 __init__ 中初始化
        self._initialized = True
        
        # 启动清理任务
        self._start_cleanup_task()
    
    def _start_cleanup_task(self):
        """启动后台清理任务，定期检查并移除已完成的倒计时"""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            except RuntimeError:
                # 模块级别初始化时无事件循环（Python 3.13+），在 startup 时再启动
                self._cleanup_task = None
    
    async def _cleanup_loop(self):
        """后台清理循环，每5秒检查一次"""
        while True:
            try:
                await asyncio.sleep(5)
                await self._check_and_cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TimerManager] 清理循环异常: {e}")
    
    async def _check_and_cleanup_expired(self):
        """检查并清理已过期的倒计时"""
        current_time = time.time()
        expired_timers = []
        
        for timer_id, timer in self._timers.items():
            if timer.end_time <= current_time:
                expired_timers.append(timer_id)
        
        for timer_id in expired_timers:
            await self._trigger_timer_complete(timer_id)
    
    async def _trigger_timer_complete(self, timer_id: str):
        """触发倒计时完成事件"""
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        
        logger.info(f"[TimerManager] 倒计时完成: {timer_id} - {timer.description}")
        
        # 执行注册的回调
        callbacks = self._callbacks.pop(timer_id, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(timer)
                else:
                    callback(timer)
            except Exception as e:
                logger.error(f"[TimerManager] 回调执行失败: {e}")
        
        # 广播更新
        await self._broadcast_update("timer_completed", timer.to_dict())
    
    async def _broadcast_update(self, event_type: str, data: Dict[str, Any]):
        """广播倒计时更新事件"""
        for callback in self._broadcast_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, data)
                else:
                    callback(event_type, data)
            except Exception as e:
                logger.error(f"[TimerManager] 广播回调失败: {e}")
    
    def create_timer(
        self,
        description: str,
        seconds: int,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        on_complete: Optional[Callable] = None,
        session_id: str = "default",
    ) -> str:
        """
        创建一个新的倒计时

        Args:
            description: 倒计时描述
            seconds: 倒计时秒数
            category: 倒计时类别 (cooking, general, reminder, etc.)
            metadata: 额外的元数据
            on_complete: 完成时的回调函数
            session_id: 所属会话 ID（按会话隔离，避免跨会话串台）

        Returns:
            timer_id: 倒计时唯一标识
        """
        timer_id = f"timer_{uuid.uuid4().hex[:8]}_{int(time.time() * 1000) % 10000}"
        current_time = time.time()

        timer = Timer(
            id=timer_id,
            description=description,
            total_seconds=seconds,
            end_time=current_time + seconds,
            created_at=current_time,
            category=category,
            session_id=session_id or "default",
            metadata=metadata or {},
        )
        
        self._timers[timer_id] = timer
        
        # 注册完成回调
        if on_complete:
            if timer_id not in self._callbacks:
                self._callbacks[timer_id] = []
            self._callbacks[timer_id].append(on_complete)
        
        # 异步等待倒计时完成
        asyncio.create_task(self._wait_and_complete(timer_id, seconds))
        
        logger.info(f"[TimerManager] 创建倒计时: {timer_id} - {description} ({seconds}s)")
        
        # 广播新倒计时创建
        asyncio.create_task(self._broadcast_update("timer_created", timer.to_dict()))
        
        return timer_id
    
    async def _wait_and_complete(self, timer_id: str, seconds: int):
        """等待倒计时完成"""
        try:
            await asyncio.sleep(seconds)
            await self._trigger_timer_complete(timer_id)
        except asyncio.CancelledError:
            logger.info(f"[TimerManager] 倒计时被取消: {timer_id}")
        except Exception as e:
            logger.error(f"[TimerManager] 倒计时等待异常: {e}")
    
    def cancel_timer(self, timer_id: str) -> bool:
        """取消指定倒计时"""
        if timer_id not in self._timers:
            return False
        
        timer = self._timers.pop(timer_id)
        self._callbacks.pop(timer_id, None)
        
        logger.info(f"[TimerManager] 取消倒计时: {timer_id}")
        
        # 广播取消事件
        asyncio.create_task(self._broadcast_update("timer_cancelled", timer.to_dict()))
        
        return True
    
    def cancel_all_timers(
        self,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """
        取消所有倒计时，或按类别/会话筛选取消

        Args:
            category: 只取消该类别的倒计时（None 表示不限类别）
            session_id: 只取消该会话的倒计时（None 表示不限会话；
                传会话 ID 时不会影响其他会话的定时器）

        Returns:
            取消的倒计时数量
        """
        timer_ids_to_cancel = []

        for timer_id, timer in self._timers.items():
            if category is not None and timer.category != category:
                continue
            if session_id is not None and getattr(timer, "session_id", "default") != session_id:
                continue
            timer_ids_to_cancel.append(timer_id)

        for timer_id in timer_ids_to_cancel:
            self.cancel_timer(timer_id)

        return len(timer_ids_to_cancel)

    def cancel_session_category_timers(self, session_id: str, category: Optional[str] = None) -> int:
        """取消某个会话（可选指定类别）的全部倒计时；不影响其他会话"""
        return self.cancel_all_timers(category=category, session_id=session_id)
    
    def get_timer(self, timer_id: str) -> Optional[Timer]:
        """获取指定倒计时信息"""
        return self._timers.get(timer_id)
    
    def get_all_timers(
        self,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Timer]:
        """
        获取所有倒计时，或按类别/会话筛选

        Args:
            category: 只返回该类别的倒计时（None 表示不限类别）
            session_id: 只返回该会话的倒计时（None 表示不限会话）

        Returns:
            倒计时列表（按创建时间排序）
        """
        timers = list(self._timers.values())
        if category:
            timers = [t for t in timers if t.category == category]
        if session_id is not None:
            timers = [t for t in timers if getattr(t, "session_id", "default") == session_id]
        return sorted(timers, key=lambda t: t.created_at)
    
    def get_active_timer_count(self) -> int:
        """获取当前活跃的倒计时数量"""
        return len(self._timers)
    
    def register_broadcast_callback(self, callback: Callable):
        """注册全局广播回调，用于接收所有倒计时事件"""
        self._broadcast_callbacks.append(callback)
    
    def unregister_broadcast_callback(self, callback: Callable):
        """注销全局广播回调"""
        if callback in self._broadcast_callbacks:
            self._broadcast_callbacks.remove(callback)
    
    def format_remaining_time(self, timer_id: str) -> str:
        """格式化倒计时剩余时间"""
        timer = self._timers.get(timer_id)
        if not timer:
            return "00:00"
        
        remaining = max(0, int(timer.end_time - time.time()))
        mins = remaining // 60
        secs = remaining % 60
        
        if mins >= 60:
            hours = mins // 60
            remain_mins = mins % 60
            return f"{hours}:{remain_mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"


# 全局单例实例
timer_manager = TimerManager()


# 便捷函数
def create_countdown(
    description: str,
    seconds: int,
    category: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
    on_complete: Optional[Callable] = None,
    session_id: str = "default",
) -> str:
    """便捷函数：创建倒计时"""
    return timer_manager.create_timer(
        description=description,
        seconds=seconds,
        category=category,
        metadata=metadata,
        on_complete=on_complete,
        session_id=session_id,
    )


def get_all_countdowns(
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """便捷函数：获取所有倒计时（可按类别/会话筛选）"""
    timers = timer_manager.get_all_timers(category, session_id=session_id)
    return [t.to_dict() for t in timers]


def cancel_countdown(timer_id: str) -> bool:
    """便捷函数：取消倒计时"""
    return timer_manager.cancel_timer(timer_id)


def cancel_all_countdowns(
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """便捷函数：取消所有倒计时（可按类别/会话筛选）"""
    return timer_manager.cancel_all_timers(category=category, session_id=session_id)
