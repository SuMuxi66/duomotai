"""W= 主路径显示值 EMA 平滑：压板卡原生噪声（实测峰峰 ~1.2g），
同时保证 raw 即时值可用于下料达标检测（EMA 滞后会过冲）。"""
from app.weight import WeightScaleReader


def test_ema_first_value_passthrough_and_smoothing():
    r = WeightScaleReader()
    out = [r._apply_ema(v) for v in (5.2, 4.8, 5.6, 5.0)]
    assert out[0] == 5.2  # 首值直通
    # 平滑后波动幅度明显小于原始序列的峰峰 0.8g
    assert max(out) - min(out) < 0.8


def test_ema_resets_to_none_on_tare():
    r = WeightScaleReader()
    r._apply_ema(5.2)
    assert r._ema_weight is not None
    r.request_tare()  # 开启 2s 采样窗口（与真实去皮流程一致）
    r._maybe_capture_tare(6.0)
    r._maybe_capture_tare(6.0)
    r._maybe_capture_tare(6.0)
    r._maybe_capture_tare(6.0)
    r._maybe_capture_tare(6.0)
    r._maybe_capture_tare(6.0)  # 第 6 个样本触发去皮完成
    assert r._soft_zero_offset == 6.0
    assert r._ema_weight is None  # 基准变化后平滑值重置


def test_weight_data_carries_raw_for_dispense():
    r = WeightScaleReader()
    r._apply_ema(5.0)
    r.update_weight_data(r._ema_weight, "g", raw=5.5)
    data = r.get_weight_data()
    assert data["weight"] == 5.0
    assert data["raw"] == 5.5


def test_update_without_raw_falls_back_to_weight():
    r = WeightScaleReader()
    r.update_weight_data(3.2, "g")
    assert r.get_weight_data()["raw"] == 3.2


def test_display_weight_is_rounded_to_int_raw_keeps_decimal():
    r = WeightScaleReader()
    r.update_weight_data(3.7, "g", raw=3.9)
    data = r.get_weight_data()
    assert data["weight"] == 4  # 显示取整（0.x 残余噪声不下屏）
    assert data["raw"] == 3.9  # 即时值保留 1 位小数供下料检测


def test_dead_zone_still_applies_to_ema():
    r = WeightScaleReader()
    r.update_weight_data(0.6, "g", raw=0.6)
    assert r.get_weight_data()["weight"] == 0.0


def test_ema_snaps_on_real_step_change():
    """突变直通：物品拿走（raw 从 120g 掉到 ~0）时 EMA 立即跟随，
    不再按 α=0.2 衰减十几秒留下旧读数残影。"""
    r = WeightScaleReader()
    for v in (119.5, 120.4, 120.1):  # 物品压秤：稳态平滑
        r._apply_ema(v)
    assert 119.5 < r._ema_weight < 120.5
    # 拿走物品：raw 一步跌回零点附近，偏差远超阈值 → 直通
    out = r._apply_ema(0.3)
    assert out == 0.3
    # 下一拍继续正常平滑（从新基准出发）
    assert abs(r._apply_ema(0.5) - 0.34) < 0.1


def test_ema_resync_threshold_ignores_noise():
    """噪声级别的波动（<5g）不触发直通，仍走平滑。"""
    r = WeightScaleReader()
    r._apply_ema(5.0)
    out = r._apply_ema(4.0)  # 偏差 1.0g，属噪声范围
    assert 4.0 < out < 5.0  # 平滑值只向 raw 移动 20%


def test_median3_rejects_single_spike():
    """中值预滤波：单发毛刺（振动/继电器/电机干扰）不进 EMA。"""
    r = WeightScaleReader()
    assert r._median3(5.0) == 5.0
    assert r._median3(5.1) == 5.1  # 窗口未满取上中位
    assert r._median3(12.0) == 5.1  # 毛刺被中值剔除
    assert r._median3(5.2) == 5.2  # 窗口滑出后恢复正常跟随


def test_display_hysteresis_holds_on_boundary_flutter():
    """显示迟滞：平滑值在 3.5 取整边界往复时显示整数不动，
    偏离当前显示 ≥0.7g 才更新——噪声引起的 1g 内抖动不再上屏。"""
    r = WeightScaleReader()
    r.update_weight_data(3.2, "g")
    assert r.get_weight_data()["weight"] == 3
    r.update_weight_data(3.6, "g")  # |3.6-3|=0.6 < 0.7：保持 3
    assert r.get_weight_data()["weight"] == 3
    r.update_weight_data(3.4, "g")
    assert r.get_weight_data()["weight"] == 3
    r.update_weight_data(3.8, "g")  # |3.8-3|=0.8 ≥ 0.7：更新为 4
    assert r.get_weight_data()["weight"] == 4


# ---------------------------------------------------------------------------
# parse_weight 解析正则回归保护
# 新板卡固件同一行输出 "Weight=<克数>g  RAW=<ADC>"，旧 W= 模式会误命中 RAW= 中的
# "W=" 子串返回 ADC 计数（~8227673），导致秤读数差 205 倍。这些用例锁定修复。
# ---------------------------------------------------------------------------

def test_parse_new_firmware_weight_plus_raw_line():
    """新板卡行 Weight=3.6g  RAW=8227673 应解析出 3.6g，不是 8227673。"""
    r = WeightScaleReader()
    weight, unit = r.parse_weight("Weight=3.6g  RAW=8227673")
    assert weight == 3.6
    assert unit == 'g'


def test_parse_new_firmware_negative_weight():
    """负克数 Weight=-0.4g  RAW=8226500 应解析出 -0.4g。"""
    r = WeightScaleReader()
    weight, unit = r.parse_weight("Weight=-0.4g  RAW=8226500")
    assert weight == -0.4
    assert unit == 'g'


def test_parse_legacy_w_format_still_supported():
    """旧板卡 W=15.2 直读克重格式仍兼容。"""
    r = WeightScaleReader()
    weight, unit = r.parse_weight("W=15.2")
    assert weight == 15.2
    assert unit == 'g'


def test_parse_raw_alone_not_misdetected():
    """仅 RAW=8227673（无 Weight=）不应被误报为克数。"""
    r = WeightScaleReader()
    weight, unit = r.parse_weight("RAW=8227673")
    assert weight is None
    assert unit is None


def test_parse_raw_substring_does_not_leak_into_w_pattern():
    """W= 模式加负向回顾：Weight=3.6g  RAW=8227673 行不能让 W= 命中 RAW 值。"""
    r = WeightScaleReader()
    # 即便移除首条 Weight= 模式（模拟顺序回退），W= 也不应抓到 8227673
    patterns = [p for p in r.WEIGHT_PATTERNS if not p[0].startswith('Weight=')]
    import re
    for pattern, unit in patterns:
        m = re.search(pattern, "Weight=3.6g  RAW=8227673", re.IGNORECASE)
        if m:
            # 允许命中 Weight 行里真实克数 3.6（来自 weight= 兜底也不会，因为没 weight=），
            # 但绝不能是 8227673
            assert float(m.group(1)) != 8227673
