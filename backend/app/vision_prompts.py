"""扩充危险检测词表与 VLM 判定规则（独立模块）。

说明：
- 与 backend/app/prompts/vision_prompts.py（既有视觉聊天 prompt）解耦，独立维护
  危险词表与统一危险判定 prompt，避免侵入既有视觉聊天流程。
- 词表与 hazard_rules.HAZARD_ALIASES 保持一致，便于后处理关键词归一化。
- 可被 glasses_cam / vision pipeline 用于：1) 构建 VLM prompt；2) 文本后处理
  把 VLM 输出的中文危险词映射回 hazard_type。
"""
from __future__ import annotations

from typing import Dict, List

from .hazard_rules import HAZARD_ALIASES, resolve_all_hazard_types

# ====================== 扩充危险检测词表（per hazard_type）======================
HAZARD_KEYWORDS: Dict[str, List[str]] = {
    "smoke": [
        "烟雾", "火情", "火焰", "冒烟", "起火", "着火",
        "黑烟", "白烟", "烟", "烟味", "冒火星",
    ],
    "overflow": [
        "泡沫溢出", "白沫溢出", "泡沫飞溅", "白沫飞溅", "泡沫涌出",
        "白沫涌出", "泡沫喷出", "白沫喷", "泡沫翻滚", "沸腾溢出",
        "沸腾飞溅", "溢锅", "扑锅", "沸锅",
        "锅盖跳动", "锅盖晃动", "锅盖抖动", "锅盖剧烈", "锅盖飞",
        "锅盖震", "锅盖弹", "锅盖跳", "锅盖摇", "锅盖晃", "锅盖颠", "锅盖振",
    ],
    "oil_fire": [
        "油锅起火", "锅起火", "油火", "油锅着火", "油锅冒火",
        "火焰窜起", "油花飞溅着火", "热油起火", "炸锅起火",
    ],
    "gas_left_on": [
        "燃气忘关", "燃气未关", "火没关", "灶火未关", "火开着",
        "灶台未关", "燃气灶开着", "无人看火", "空灶干烧",
    ],
    "child_nearby": [
        "儿童靠近", "小孩靠近", "孩子靠近", "儿童接近",
        "小孩接近", "幼儿靠近", "宝宝靠近灶台", "儿童在厨房",
    ],
    "knife_danger": [
        "刀具危险", "菜刀", "刀具", "锋利", "刀刃",
        "挥刀", "玩刀", "刀口对人",
    ],
}

# 扁平关键词集合（便于快速扫文本）
ALL_HAZARD_KEYWORDS: List[str] = [kw for kws in HAZARD_KEYWORDS.values() for kw in kws]


def keywords_for(hazard_type: str) -> List[str]:
    """获取某类危险的关键词列表；未知类型返回空。"""
    return list(HAZARD_KEYWORDS.get(hazard_type, []))


def match_keywords(text: str) -> Dict[str, List[str]]:
    """扫描文本，返回 {hazard_type: [命中的关键词]}。"""
    if not text:
        return {}
    matched: Dict[str, List[str]] = {}
    for hazard_type, kws in HAZARD_KEYWORDS.items():
        hits = [kw for kw in kws if kw in text]
        if hits:
            matched[hazard_type] = hits
    return matched


def detect_hazard_types(text: str) -> List[str]:
    """从文本中解析出全部 hazard_type（去重保序）。

    优先用 HAZARD_ALIASES 归一化，结果与 hazard_rules 对齐。
    """
    return resolve_all_hazard_types(text)


# ====================== 扩充 VLM 危险判定 prompt ======================
# 覆盖原有 3 类（烟雾/锅盖跳动/泡沫溢出）+ 新增 3 类（油锅起火/燃气忘关/儿童靠近/刀具危险）
EXPANDED_DANGER_PROMPT = """分析画面内容，判断是否存在以下厨房危险情况（多类可同时存在）：

1. 烟雾/火情(smoke)：是否有烟雾、火焰、冒烟、起火、着火、黑烟等火情迹象。
2. 沸腾溢出(overflow)：是否有大量白色泡沫溢出、飞溅、涌出、沸腾溢出，或锅盖剧烈跳动/晃动/抖动/即将飞出。
3. 油锅起火(oil_fire)：油锅是否起火、锅起火、火焰从油锅窜起、热油起火等（比一般烟雾更紧急）。
4. 燃气忘关(gas_left_on)：灶火是否在无人看管下持续开启、燃气未关、空锅干烧、火开着但人不在。
5. 儿童靠近(child_nearby)：是否有儿童/小孩/幼儿靠近灶台、刀具或高温区域（存在烫伤/碰撞风险）。
6. 刀具危险(knife_danger)：画面中是否有人挥舞刀具、玩刀、刀口对人、刀具无人看管放置在边缘。

判定要点：
- 仅依据画面可见证据，不臆测看不到的细节。
- 油锅起火与普通烟雾区分：油锅起火必有明火从油锅窜出。
- 燃气忘关需同时观察到「火开着」且「无人在灶前」。
- 儿童靠近需能识别到儿童/小孩身影。

请严格按照以下JSON格式返回结果（不要包含其他内容）：
{
  "has_danger": true或false,
  "danger_types": ["smoke" 或 "overflow" 或 "oil_fire" 或 "gas_left_on" 或 "child_nearby" 或 "knife_danger" 或组合],
  "description": "简要描述画面情况（中文）",
  "severity": "low" 或 "medium" 或 "high" 或 "critical"
}

如果没有危险，返回：
{"has_danger": false, "danger_types": [], "description": "正常烹饪中", "severity": "low"}
"""

# 旧版 3 类中文标签 -> 新 hazard_type 映射（向后兼容既有 vision_handler 关键词回退）
LEGACY_DANGER_TYPE_MAP = {
    "烟雾": "smoke",
    "锅盖跳动": "overflow",
    "泡沫溢出": "overflow",
}


__all__ = [
    "HAZARD_KEYWORDS",
    "ALL_HAZARD_KEYWORDS",
    "EXPANDED_DANGER_PROMPT",
    "LEGACY_DANGER_TYPE_MAP",
    "keywords_for",
    "match_keywords",
    "detect_hazard_types",
]
