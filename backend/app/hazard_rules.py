"""危险类型规则配置：等级 / 冷却 / 处置动作。

用 Python 常量 + dict 表达，避免引入 yaml 依赖，便于 hazard_rules 解析与单测。
被 safety_aggregator / device_controller / config / vision_prompts 复用。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ====================== 危险等级 ======================
HAZARD_LEVEL_INFO = "info"
HAZARD_LEVEL_WARN = "warn"
HAZARD_LEVEL_HIGH = "high"
HAZARD_LEVEL_CRITICAL = "critical"

HAZARD_LEVELS = (HAZARD_LEVEL_INFO, HAZARD_LEVEL_WARN, HAZARD_LEVEL_HIGH, HAZARD_LEVEL_CRITICAL)
# 等级 -> 数值，便于比较
_LEVEL_RANK: Dict[str, int] = {
    HAZARD_LEVEL_INFO: 0,
    HAZARD_LEVEL_WARN: 1,
    HAZARD_LEVEL_HIGH: 2,
    HAZARD_LEVEL_CRITICAL: 3,
}

# ====================== 处置动作 ======================
ACTION_ALERT_ONLY = "alert_only"
ACTION_VOICE_REMIND = "voice_remind"
ACTION_OPEN_FAN = "open_fan"
ACTION_EMERGENCY_SHUTDOWN = "emergency_shutdown"

HAZARD_ACTIONS = (
    ACTION_ALERT_ONLY,
    ACTION_VOICE_REMIND,
    ACTION_OPEN_FAN,
    ACTION_EMERGENCY_SHUTDOWN,
)

# ====================== 危险类型 -> 规则 ======================
# 每类 {level, cooldown_sec, action}
HAZARD_RULES: Dict[str, Dict[str, Any]] = {
    "smoke": {
        "level": HAZARD_LEVEL_HIGH,
        "cooldown_sec": 30,
        "action": ACTION_OPEN_FAN,
        "label": "烟雾/火情",
    },
    "overflow": {
        "level": HAZARD_LEVEL_WARN,
        "cooldown_sec": 20,
        "action": ACTION_ALERT_ONLY,
        "label": "沸腾溢出",
    },
    "oil_fire": {
        "level": HAZARD_LEVEL_CRITICAL,
        "cooldown_sec": 15,
        "action": ACTION_EMERGENCY_SHUTDOWN,
        "label": "油锅起火",
    },
    "gas_left_on": {
        "level": HAZARD_LEVEL_CRITICAL,
        "cooldown_sec": 60,
        "action": ACTION_EMERGENCY_SHUTDOWN,
        "label": "燃气忘关",
    },
    "child_nearby": {
        "level": HAZARD_LEVEL_WARN,
        "cooldown_sec": 30,
        "action": ACTION_VOICE_REMIND,
        "label": "儿童靠近",
    },
    "knife_danger": {
        "level": HAZARD_LEVEL_HIGH,
        "cooldown_sec": 20,
        "action": ACTION_VOICE_REMIND,
        "label": "刀具危险",
    },
}

# ====================== 中文关键词 -> hazard_type 别名 ======================
# 供 VLM 文本后处理做关键词归一化（与 vision_prompts 词表保持一致）
HAZARD_ALIASES: Dict[str, str] = {
    # smoke
    "烟雾": "smoke", "火情": "smoke", "火焰": "smoke", "冒烟": "smoke",
    "黑烟": "smoke", "白烟": "smoke", "烟": "smoke",
    # overflow
    "泡沫溢出": "overflow", "白沫溢出": "overflow", "泡沫飞溅": "overflow",
    "白沫飞溅": "overflow", "溢锅": "overflow", "扑锅": "overflow", "沸锅": "overflow",
    "锅盖跳动": "overflow", "锅盖晃动": "overflow", "锅盖抖动": "overflow",
    "沸腾溢出": "overflow",
    # oil_fire
    "油锅起火": "oil_fire", "锅起火": "oil_fire", "油火": "oil_fire",
    "起火": "oil_fire", "着火": "oil_fire", "火焰窜起": "oil_fire",
    # gas_left_on
    "燃气忘关": "gas_left_on", "燃气未关": "gas_left_on", "火没关": "gas_left_on",
    "灶火未关": "gas_left_on", "火开着": "gas_left_on", "灶台未关": "gas_left_on",
    # child_nearby
    "儿童靠近": "child_nearby", "小孩靠近": "child_nearby", "孩子靠近": "child_nearby",
    "儿童接近": "child_nearby", "小孩接近": "child_nearby",
    # knife_danger
    "刀具危险": "knife_danger", "菜刀": "knife_danger", "刀具": "knife_danger",
}


def get_rule(hazard_type: str) -> Dict[str, Any]:
    """获取某类危险规则；未知类型回退为 info/alert_only/30s，避免 KeyError。"""
    return HAZARD_RULES.get(
        hazard_type,
        {"level": HAZARD_LEVEL_INFO, "cooldown_sec": 30, "action": ACTION_ALERT_ONLY, "label": hazard_type},
    )


def get_level(hazard_type: str) -> str:
    return str(get_rule(hazard_type).get("level", HAZARD_LEVEL_INFO))


def get_cooldown(hazard_type: str) -> int:
    return int(get_rule(hazard_type).get("cooldown_sec", 30))


def get_action(hazard_type: str) -> str:
    return str(get_rule(hazard_type).get("action", ACTION_ALERT_ONLY))


def level_rank(level: str) -> int:
    """等级转数值，便于比较大小。未知等级视为 0。"""
    return _LEVEL_RANK.get(level, 0)


def is_critical(hazard_type: str) -> bool:
    return get_level(hazard_type) == HAZARD_LEVEL_CRITICAL


def resolve_hazard_type(text: str) -> Optional[str]:
    """从中文文本归一化出 hazard_type；命中多个时按 dict 顺序取首个。

    用于把 VLM 输出的中文危险词映射到 hazard_rules 的 key。
    """
    if not text:
        return None
    for keyword, hazard_type in HAZARD_ALIASES.items():
        if keyword in text:
            return hazard_type
    return None


def resolve_all_hazard_types(text: str) -> list:
    """从中文文本归一化出全部 hazard_type（去重，保序）。"""
    if not text:
        return []
    found: list = []
    seen: set = set()
    for keyword, hazard_type in HAZARD_ALIASES.items():
        if keyword in text and hazard_type not in seen:
            seen.add(hazard_type)
            found.append(hazard_type)
    return found


__all__ = [
    "HAZARD_LEVEL_INFO", "HAZARD_LEVEL_WARN", "HAZARD_LEVEL_HIGH", "HAZARD_LEVEL_CRITICAL",
    "HAZARD_LEVELS", "ACTION_ALERT_ONLY", "ACTION_VOICE_REMIND", "ACTION_OPEN_FAN",
    "ACTION_EMERGENCY_SHUTDOWN", "HAZARD_ACTIONS", "HAZARD_RULES", "HAZARD_ALIASES",
    "get_rule", "get_level", "get_cooldown", "get_action", "level_rank", "is_critical",
    "resolve_hazard_type", "resolve_all_hazard_types",
]
