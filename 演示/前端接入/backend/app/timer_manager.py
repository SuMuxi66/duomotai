# -*- coding: utf-8 -*-
"""
★ 演示文件 - 倒计时异步引擎（全语音 · 异步不互扰）

模块对应：全局倒计时管理器（异步线程等待，不占交互主流程）
完整实现：backend/app/timer_manager.py

保留「创建即异步等待 → 到期触发回调 → 可取消」核心机制，
去掉日志/广播等运行时依赖，用于演示/讲解，不可直接执行。
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Timer:
    """倒计时对象"""
    id: str
    description: str
    total_seconds: int
    end_time: float        # Unix 时间戳（秒）
    created_at: float
    category: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "total_seconds": self.total_seconds,
            "end_time": self.end_time,
            "category": self.category,
            "remaining_seconds": max(0, int(self.end_time - time.time())),
        }


class TimerManager:
    """
    全局倒计时管理器（单例）

    核心设计 —— 异步线程互不干扰：
    1. create_timer 立即返回，不阻塞对话主流程
    2. 后台开一个独立协程 asyncio.sleep 等待，等待期间不占任何资源
    3. 到期才触发 on_complete 回调（异步执行）
    4. 随时可 cancel
    """

    _instance: Optional["TimerManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._timers: Dict[str, Timer] = {}
        self._callbacks: Dict[str, List[Callable]] = {}

    def create_timer(
        self,
        description: str,
        seconds: int,
        category: str = "general",
        metadata: Optional[dict] = None,
        on_complete: Optional[Callable] = None,
    ) -> str:
        """创建倒计时：同步返回 timer_id，异步后台等待"""
        timer_id = f"timer_{uuid.uuid4().hex[:8]}"
        now = time.time()
        timer = Timer(
            id=timer_id,
            description=description,
            total_seconds=seconds,
            end_time=now + seconds,
            created_at=now,
            category=category,
            metadata=metadata or {},
        )
        self._timers[timer_id] = timer
        if on_complete:
            self._callbacks.setdefault(timer_id, []).append(on_complete)

        # ★ 独立异步任务等待：倒计时期间对话主流程完全不受影响
        asyncio.create_task(self._wait_and_complete(timer_id, seconds))
        return timer_id

    async def _wait_and_complete(self, timer_id: str, seconds: int):
        """后台等待协程（核心）：sleep 完触发回调，可被 cancel"""
        try:
            await asyncio.sleep(seconds)
            await self._trigger_timer_complete(timer_id)
        except asyncio.CancelledError:
            print(f"[TimerManager] 倒计时 {timer_id} 已取消")

    async def _trigger_timer_complete(self, timer_id: str):
        """到期：执行注册的回调（回调本身就是异步任务，不阻塞谁）"""
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        callbacks = self._callbacks.pop(timer_id, [])
        for cb in callbacks:
            result = cb(timer)
            if asyncio.iscoroutine(result):
                await result

    def cancel_timer(self, timer_id: str) -> bool:
        """取消倒计时"""
        if timer_id not in self._timers:
            return False
        self._timers.pop(timer_id)
        self._callbacks.pop(timer_id, None)
        return True

    def get_all_timers(self) -> List[Timer]:
        return list(self._timers.values())


timer_manager = TimerManager()


def create_countdown(
    description: str,
    seconds: int,
    category: str = "general",
    on_complete: Optional[Callable] = None,
) -> str:
    """便捷函数：创建倒计时"""
    return timer_manager.create_timer(
        description=description, seconds=seconds,
        category=category, on_complete=on_complete,
    )


def get_all_countdowns() -> List[dict]:
    return [t.to_dict() for t in timer_manager.get_all_timers()]


# ============================
# 演示用法
# ============================
if __name__ == "__main__":
    async def demo():
        # 语音说"5分钟后提醒我" → 后端创建倒计时，立即返回（交互不阻塞）
        timer_id = create_countdown(
            description="关火", seconds=3,
            category="cooking",
            on_complete=lambda timer: print(f"⏰ TTS播报: {timer.description} 时间到了！"),
        )
        print(f"[{time.strftime('%H:%M:%S')}] 已创建倒计时 {timer_id}，立即继续处理交互")
        await asyncio.sleep(1.5)
        print(f"[{time.strftime('%H:%M:%S')}] 用户此刻正在问别的问题（交互进行中，倒计时并行等待，互不干扰）")
        await asyncio.sleep(3)

    asyncio.run(demo())