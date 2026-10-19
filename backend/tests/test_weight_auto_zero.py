"""秤零点自动跟踪（自动校准 v2：块均值慢漂移累积）。

慢漂移（持续微小爬行）自动归零；静止负载块均值恒定永不吞；
到达瞬态（样本跳变/块间爬升）一律拦下。1s 采样间隔下每块 21 个样本。
"""
import time

import pytest

from app.weight import WeightScaleReader

BLOCK = 21  # 1s 间隔下每块样本数（块在 now-block_ts≥20s 的那个样本上闭合）


def _feed(r, samples):
    """samples: [(ts, value)]，value 为减基准后的相对读数"""
    for ts, v in samples:
        r._maybe_auto_zero(v, now=ts)


def _ramp(start_ts, n, step):
    return [(start_ts + i, step * i) for i in range(n)]


def _flat(start_ts, n, value):
    return [(start_ts + i, value) for i in range(n)]


def test_auto_zero_trims_slow_drift():
    """空秤 +0.4g/块 的慢爬升：5 块后累积 1.68g 触发 Trim，EMA 重置。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _ramp(1000, 5 * BLOCK, 0.02))
    # 块均值 0.2/0.62/1.04/1.46（第 4 块闭合即评估）：drift = 1.26
    assert r._soft_zero_offset == pytest.approx(1.26, abs=0.02)
    assert r._ema_weight is None  # 平滑值失效，下个读数直通重建


def test_auto_zero_trims_negative_drift():
    """负向慢漂移同样归零（实测 -2g 级场景）。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _ramp(1000, 5 * BLOCK, -0.02))
    assert r._soft_zero_offset == pytest.approx(-1.46, abs=0.02)  # 负分支：块均值 ≤-0.8 即按当前水平 Trim


def test_sitting_load_never_trimmed():
    """秤上静止的 3g 负载（倒的盐）：块均值恒定 drift=0，永远不被吞。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _flat(1000, 130, 3.0))
    assert r._soft_zero_offset == 0.0


def test_negative_residual_trimmed():
    """带物重启/带物去皮后拿走物品的持续负读数（真实负载不可能为负）：
    渐进归零——2026-09-21 起负向受 AUTO_ZERO_MAX_TRIM_G 单次上限约束
    （无上限时一次 Trim 吞掉整个载荷残留，与带物首连去皮缺陷组合成
    "放 300g 显示 1g"事故；大幅残留分多次渐进归零，期间显示负数提示异常）。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _flat(1000, 5 * BLOCK, -38.0))
    assert r._soft_zero_offset == pytest.approx(-15.0, abs=0.05)  # 首次 Trim 封顶 -15g


def test_jump_placement_resets_and_never_trims():
    """放置瞬态（样本跳变 ≥1.8g）清历史进等待期；负载静止后 drift=0 不 Trim。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _flat(1000, 100, 0.0))  # 空秤阶段
    _feed(r, _flat(1100, 120, 3.0))  # 跳变放上 3g 后静止
    assert r._soft_zero_offset == 0.0


def test_gradual_ramp_placement_blocks_trim():
    """缓慢放置（无样本级跳变，但块均值爬升 1.5g/块）：交互守卫拦下，不 Trim。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _flat(1000, 63, 0.0))
    _feed(r, _flat(1063, 63, 1.5))
    _feed(r, _flat(1126, 63, 3.0))
    assert r._soft_zero_offset == 0.0


def test_tare_suspension():
    """手动去皮采样窗口进行中挂起自动校准。"""
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    r.request_tare()  # 开启 2s 采样窗口
    _feed(r, _ramp(time.time(), 5 * BLOCK, 0.02))
    assert r._soft_zero_offset == 0.0


def test_flag_disabled(monkeypatch):
    """WEIGHT_AUTO_ZERO_ENABLED=false 时完全不生效。"""
    from app import config
    monkeypatch.setattr(config.settings, "weight_auto_zero_enabled", False)
    r = WeightScaleReader()
    r._soft_zero_offset = 0.0
    _feed(r, _ramp(1000, 5 * BLOCK, 0.02))
    assert r._soft_zero_offset == 0.0
