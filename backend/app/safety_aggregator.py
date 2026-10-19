"""多帧时序聚合器：滑动窗口 + 投票，降低单帧误报。

设计目标：
- 弥补 VLM 单帧判定易抖动 / 误报的短板。
- per hazard_type 维护独立滑动窗口（deque），互不污染。
- 仅负责"是否达到触发阈值"（feed -> bool）；冷却由调用方（glasses_cam 现有
  30s 冷却）协同处理，聚合器不持有时间态。

用法：
    agg = TemporalAggregator(window=5, threshold=3)
    should_alert = agg.feed("smoke", detected=True)
    if should_alert and not in_cooldown:
        alert(...)
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, Optional


class TemporalAggregator:
    """滑动窗口多帧投票聚合器。

    Args:
        window: 滑动窗口大小（帧数），>=1。
        threshold: 触发阈值（窗口内命中帧数 >= threshold 才返回 True），>=1。
            若 threshold > window，自动夹到 window（避免永不触发）。
    """

    def __init__(self, window: int = 5, threshold: int = 3):
        self.window = max(1, int(window))
        self.threshold = max(1, int(threshold))
        if self.threshold > self.window:
            # 阈值不能超过窗口，否则永远不可达
            self.threshold = self.window
        self._buffers: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=self.window))

    def feed(self, hazard_type: str, detected: bool) -> bool:
        """写入一帧判定结果，返回当前是否达到触发阈值。

        Args:
            hazard_type: 危险类型 key（见 hazard_rules.HAZARD_RULES）。
            detected: 该帧 VLM 是否检出该类危险。

        Returns:
            True 表示窗口内命中帧数 >= threshold，应触发警报；
            False 表示未达阈值，不应触发（降误报）。
        """
        buf = self._buffers[hazard_type]
        buf.append(1 if detected else 0)
        return sum(buf) >= self.threshold

    def count(self, hazard_type: str) -> int:
        """当前窗口内某类危险的命中帧数。"""
        return sum(self._buffers.get(hazard_type, deque()))

    def window_size(self, hazard_type: str) -> int:
        """当前某类已写入的帧数（<= window）。"""
        return len(self._buffers.get(hazard_type, deque()))

    def reset(self, hazard_type: Optional[str] = None) -> None:
        """清除某类缓冲；不传则清空全部。用于场景切换/误报复位。"""
        if hazard_type is None:
            self._buffers.clear()
        else:
            self._buffers.pop(hazard_type, None)

    def snapshot(self) -> Dict[str, int]:
        """返回 {hazard_type: 命中帧数} 快照，便于调试与单测。"""
        return {ht: sum(buf) for ht, buf in self._buffers.items()}

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"TemporalAggregator(window={self.window}, threshold={self.threshold}, snap={self.snapshot()})"


# ====================== 默认单例 ======================
# 供 glasses_cam 等模块按需复用；测试中请直接 new TemporalAggregator(...) 以隔离。
_default_aggregator: Optional[TemporalAggregator] = None


def get_default_aggregator(window: int = 5, threshold: int = 3) -> TemporalAggregator:
    """惰性获取进程级默认聚合器单例（按首次传入 window/threshold 初始化）。"""
    global _default_aggregator
    if _default_aggregator is None:
        _default_aggregator = TemporalAggregator(window=window, threshold=threshold)
    return _default_aggregator


def reset_default_aggregator() -> None:
    """重置默认单例（测试间隔离用）。"""
    global _default_aggregator
    _default_aggregator = None


__all__ = [
    "TemporalAggregator",
    "get_default_aggregator",
    "reset_default_aggregator",
]
