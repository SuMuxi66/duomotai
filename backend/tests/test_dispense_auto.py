# -*- coding: utf-8 -*-
"""
下料克数提取与 `_maybe_auto_dispense` 自动下料缺口回归测试：
- 缺口①：search() 只取第一个克数匹配，"500g 肉 + 5g 盐"混合句首个超限即放弃，后续调料克数被漏
         → 改为遍历全部匹配，调料词相邻优先。
- 缺口②：无调料词关联，"取一块 50g 姜"/"面团 100 克"这类食材量触发下料 → 食材词相邻排除、
         液体调料（水/油/蛋液）相邻排除。
参考 tests/test_dispense.py 的 monkeypatch 风格（不触真实硬件）。
"""
import asyncio

from app import cooking_core
from app.session import SessionState
import app.ws_handlers.cooking as cooking_module


# ---------- extract_dispense_grams：纯函数（无硬件） ----------

def test_extract_first_over_limit_but_later_salt():
    """缺口①：首匹配 300g 超限，后续 5g 盐必须被提取（旧 search() 直接放弃整段）。"""
    assert cooking_core.extract_dispense_grams("在大碗中放入中筋面粉 300g 和盐 5g，混合均匀") == 5.0


def test_extract_first_over_limit_but_later_pepper():
    """缺口①：200g 牛肉在前，2g 黑胡椒在后。"""
    assert (
        cooking_core.extract_dispense_grams(
            "将 200g 牛肉末放入碗中，加入 1 汤匙生抽、半汤匙白糖和 2g 黑胡椒，搅拌均匀"
        )
        == 2.0
    )


def test_extract_mixed_grams_picks_seasoning_not_flour():
    """缺口②：'100g 面粉 + 5g 盐'混合句，必须取 5g 盐而不是第一个合法克数 100g 面粉。"""
    assert cooking_core.extract_dispense_grams("找个干净的盆，加入 100g 面粉、200g 淀粉、180g 水、5g 盐") == 5.0


def test_extract_food_amount_not_dispensed():
    """食材量（姜/面团/葱）相邻克数不触发。"""
    assert cooking_core.extract_dispense_grams("取一块 50g 姜（鸡蛋大小）") is None
    assert cooking_core.extract_dispense_grams("面团分成每份约 100 克，搓圆，擀成薄片") is None
    assert cooking_core.extract_dispense_grams("取葱白切丝 25g，姜去皮后切丝") is None


def test_extract_liquid_and_oil_not_dispensed():
    """液体调料（植物油/水）相邻克数不触发。"""
    assert cooking_core.extract_dispense_grams("在铁锅中倒入 15g 植物油，用中小火慢熬") is None
    assert cooking_core.extract_dispense_grams("将 50g 清水、40g 番茄酱、20g 白糖、10g 白醋放入小碗中") in (40.0, 20.0)


def test_extract_salt_adjacent_first():
    """同一句多克数：调料词（盐）相邻的克数优先。"""
    assert cooking_core.extract_dispense_grams("加入 2g 盐和 2g 糖调味") == 2.0
    assert cooking_core.extract_dispense_grams("加入 20g 盐，25g 料酒，然后给鲤鱼搓个澡") == 20.0


def test_extract_range_limits():
    """0.5~100g 范围外不触发。"""
    assert cooking_core.extract_dispense_grams("加入 0.2g 盐") is None
    assert cooking_core.extract_dispense_grams("加入 500 克盐，腌一整只羊") is None
    assert cooking_core.extract_dispense_grams("加入 150g 白砂糖") is None


def test_extract_empty_and_no_match():
    assert cooking_core.extract_dispense_grams("") is None
    assert cooking_core.extract_dispense_grams("盐适量，味精少许，翻炒均匀") is None


# ---------- _maybe_auto_dispense：状态守卫 + start_dispense 交互（monkeypatch） ----------

class _FakeState:
    def __init__(self, cooking_state="COOKING"):
        self.cooking_state = cooking_state


def _make_state(cooking_state="COOKING"):
    state = SessionState()
    state.cooking_state = cooking_state
    return state


def test_maybe_auto_dispense_ignored_outside_cooking(monkeypatch):
    """非 COOKING 态（如 STEP_WAITING）不启动下料。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state("STEP_WAITING")
    text = asyncio.run(cooking_module._maybe_auto_dispense(state, "下一步：加入盐 3g"))
    assert called["n"] == 0
    assert "自动下料" not in text


def test_maybe_auto_dispense_starts_with_seasoning(monkeypatch):
    """COOKING 态含调料克数文本：启动下料并追加提示。"""
    captured = {}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True, "target_g": target_g, "target": target_g}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    text = asyncio.run(cooking_module._maybe_auto_dispense(state, "第二步：加入盐 5 克"))
    assert captured.get("target_g") == 5.0
    assert "已启动自动下料 5 克" in text


def test_maybe_auto_dispense_food_amount_no_start(monkeypatch):
    """食材量文本（50g 姜）不启动下料也不追加提示。"""
    called = {"n": 0}

    async def fake_start_dispense(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    text = asyncio.run(cooking_module._maybe_auto_dispense(state, "第二步：取一块 50g 姜（鸡蛋大小）"))
    assert called["n"] == 0
    assert "自动下料" not in text


def test_maybe_auto_dispense_start_failure_keeps_text(monkeypatch):
    """启动失败（串口未就绪）：不追加提示，原文本原样返回。"""
    async def fake_start_dispense(*a, **k):
        return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    original = "第三步：加入 3g 淀粉勾芡"
    text = asyncio.run(cooking_module._maybe_auto_dispense(state, original))
    assert text == original  # 未触发时原样返回


def test_maybe_auto_dispense_flour_then_salt_mixed(monkeypatch):
    """缺口①端到端：'100g 面粉 + 5g 盐'混合句，COOKING 态下启动 5g 而非 100g。"""
    captured = {}

    async def fake_start_dispense(target_g):
        captured["target_g"] = target_g
        return {"success": True}

    monkeypatch.setattr(cooking_module.dispense_module, "start_dispense", fake_start_dispense)
    state = _make_state()
    asyncio.run(
        cooking_module._maybe_auto_dispense(
            state, "找个干净的盆，加入 100g 面粉、200g 淀粉、180g 水、5g 盐"
        )
    )
    assert captured.get("target_g") == 5.0