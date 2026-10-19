"""
营养计算模块

菜谱食材 + 份量 → 卡路里 / 蛋白质 / 脂肪 / 碳水，并输出可拼入 LLM 的健康话术 prompt。

依赖：仅使用标准库（json / re / pathlib / typing）。
数据源：data/food_composition.json（中国食物成分表参考子集）。

典型用法：
    from backend.app.nutrition import calc_nutrition, NUTRITION_PROMPT
    result = calc_nutrition([{"name": "鸡蛋", "amount_g": 60}])
    # result["calories"] / result["protein_g"] / ...

接入 cooking_core 的换算流程（演示场景）：
    1. cooking_core.calculate_servings_with_llm 拿到调整后的食材清单后，
       把每个 {"name","calculated"} 解析成 {"name","amount_g"}（单位换算可由
       LLM 一并输出或在本模块外做，本模块只负责按克计算）。
    2. 调 calc_nutrition(...) 得到每份营养总和 + per_ingredient 明细。
    3. 用 build_nutrition_prompt(...) 拼出 NUTRITION_PROMPT 实参，
       再喂给 LLM 生成健康话术（与 SERVINGS_CALCULATION_PROMPT 复用同一通道）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# 项目根目录：本文件位于 backend/app/nutrition.py，往上两级即项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSITION_PATH = _PROJECT_ROOT / "data" / "food_composition.json"

# 食材名中需要剥离的常见量词 / 修饰词 / 单位，便于模糊匹配
# 例："猪肉100g" -> "猪肉"；"瘦猪肉" -> "瘦猪肉"（保留修饰，因为别名里有"瘦猪肉"）；
#     "土豆1个" -> "土豆"
_INGREDIENT_NOISE_UNITS = (
    "kg", "g", "ml", "l", "克", "千克", "公斤", "斤", "两", "毫升", "升",
    "个", "只", "根", "瓣", "片", "块", "条", "颗", "粒", "头", "尾",
    "把", "捆", "扎", "包", "袋", "盒", "罐", "瓶", "杯", "碗", "勺", "匙",
    "汤匙", "茶匙", "大勺", "小勺",
)
# 数字 + 单位的正则，用于剥离量词
_AMOUNT_WITH_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:" + "|".join(re.escape(u) for u in _INGREDIENT_NOISE_UNITS) + r")"
)

# 修饰前缀（去重用，不直接剥离以免破坏"瘦猪肉"这类有效别名）
_MODIFIER_PREFIXES = ("鲜", "干", "生", "熟", "大", "小", "老", "嫩", "新")


def load_composition(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """
    加载食物成分表 JSON。

    Args:
        path: JSON 文件路径，None 时使用 DEFAULT_COMPOSITION_PATH。

    Returns:
        以小写规范化 name 为 key 的字典：
            {"大米": {"name":..., "aliases":[...], "kcal_per_100g":..., ...}, ...}
        同时为每条记录补充 "_alias_set"（小写化的名称+别名集合），加速匹配。
    """
    file_path = Path(path) if path else DEFAULT_COMPOSITION_PATH
    with open(file_path, "r", encoding="utf-8") as fp:
        raw_list: list[dict[str, Any]] = json.load(fp)

    composition: dict[str, dict[str, Any]] = {}
    for item in raw_list:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        # 构建小写化查找集合（中文 lower 等于原值，但兼容英文别名）
        alias_set = {name.lower()} | {a.lower() for a in aliases}
        entry = {
            "name": name,
            "aliases": aliases,
            "kcal_per_100g": float(item.get("kcal_per_100g", 0) or 0),
            "protein_g": float(item.get("protein_g", 0) or 0),
            "fat_g": float(item.get("fat_g", 0) or 0),
            "carbs_g": float(item.get("carbs_g", 0) or 0),
            "_alias_set": alias_set,
        }
        composition[name] = entry
    return composition


def _strip_amount_and_unit(text: str) -> str:
    """剥离 '数字+单位' 片段，如 '猪肉100g' -> '猪肉'，'土豆1个' -> '土豆'。"""
    cleaned = _AMOUNT_WITH_UNIT_RE.sub("", text)
    return cleaned.strip()


def _strip_punct_and_spaces(text: str) -> str:
    """去掉首尾标点和空白。"""
    return re.sub(r"^[\s,，、;；:：*+\-]+|[\s,，、;；:：*+\-]+$", "", text).strip()


def match_ingredient(name: str, composition: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """
    模糊匹配食材名到成分表条目。

    匹配优先级：
        1. 精确匹配 name（不区分大小写）
        2. 别名精确匹配
        3. 去掉 '数字+单位' 后再精确 / 别名匹配（"猪肉100g" -> "猪肉"）
        4. 包含匹配：成分表条目 name 或别名出现在输入里，或输入出现在它们里
           （例："瘦猪肉" 包含 "猪肉"；"西红柿块" 包含 "西红柿"）

    Args:
        name: 用户输入的食材名（可能带量词/单位/修饰）
        composition: load_composition 返回的字典

    Returns:
        匹配到的条目（含 name/aliases/kcal_per_100g/...），未匹配返回 None。
    """
    if not name or not composition:
        return None

    raw = str(name).strip()
    if not raw:
        return None

    key = raw.lower()

    # 1) 精确匹配 name
    for entry in composition.values():
        if key in entry["_alias_set"]:
            return entry

    # 3) 去单位后再匹配（"猪肉100g" / "土豆1个"）
    stripped = _strip_amount_and_unit(raw)
    stripped = _strip_punct_and_spaces(stripped)
    if stripped and stripped != raw:
        skey = stripped.lower()
        for entry in composition.values():
            if skey in entry["_alias_set"]:
                return entry

    # 4) 包含匹配：优先匹配最长的别名（更具体）
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in composition.values():
        for alias in entry["_alias_set"]:
            if not alias:
                continue
            if alias in key or key in alias:
                # 用别名长度作为评分，越长越具体
                candidates.append((len(alias), entry))
                break
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return None


def calc_nutrition(
    ingredients: Iterable[dict[str, Any]],
    composition: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    根据食材清单计算营养总量。

    Args:
        ingredients: 食材列表，每项形如 {"name": str, "amount_g": float}。
                     amount_g 缺失时按 0 处理；支持负值会被裁剪到 0。
        composition: load_composition 返回的字典；None 时自动加载默认成分表。

    Returns:
        {
            "calories": float,           # 总卡路里（kcal）
            "protein_g": float,          # 总蛋白质（g）
            "fat_g": float,              # 总脂肪（g）
            "carbs_g": float,            # 总碳水（g）
            "per_ingredient": [          # 每种食材明细
                {"name": str, "amount_g": float, "calories": float,
                 "protein_g": float, "fat_g": float, "carbs_g": float,
                 "matched": bool, "matched_name": str | None}
            ],
            "matched_count": int,        # 命中成分表的食材数
            "total_count": int,          # 食材总数
        }
    未匹配的食材 matched=False，营养值全部为 0，不抛异常。
    """
    if composition is None:
        composition = load_composition()

    total_cal = 0.0
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    per_ingredient: list[dict[str, Any]] = []
    matched_count = 0
    total_count = 0

    for item in ingredients or []:
        if not isinstance(item, dict):
            continue
        ing_name = str(item.get("name", "")).strip()
        if not ing_name:
            continue
        total_count += 1
        amount_g = float(item.get("amount_g") or 0)
        if amount_g < 0:
            amount_g = 0.0

        entry = match_ingredient(ing_name, composition)
        if entry is None:
            per_ingredient.append({
                "name": ing_name,
                "amount_g": amount_g,
                "calories": 0.0,
                "protein_g": 0.0,
                "fat_g": 0.0,
                "carbs_g": 0.0,
                "matched": False,
                "matched_name": None,
            })
            continue

        matched_count += 1
        ratio = amount_g / 100.0
        cal = entry["kcal_per_100g"] * ratio
        protein = entry["protein_g"] * ratio
        fat = entry["fat_g"] * ratio
        carbs = entry["carbs_g"] * ratio

        total_cal += cal
        total_protein += protein
        total_fat += fat
        total_carbs += carbs

        per_ingredient.append({
            "name": ing_name,
            "amount_g": amount_g,
            "calories": round(cal, 2),
            "protein_g": round(protein, 2),
            "fat_g": round(fat, 2),
            "carbs_g": round(carbs, 2),
            "matched": True,
            "matched_name": entry["name"],
        })

    return {
        "calories": round(total_cal, 2),
        "protein_g": round(total_protein, 2),
        "fat_g": round(total_fat, 2),
        "carbs_g": round(total_carbs, 2),
        "per_ingredient": per_ingredient,
        "matched_count": matched_count,
        "total_count": total_count,
    }


def format_nutrition_summary(result: dict[str, Any]) -> str:
    """
    把 calc_nutrition 的结果格式化成简短文本，便于拼入 prompt 或直接给前端展示。

    例：
        总热量 285.4 kcal，蛋白质 18.6g，脂肪 12.3g，碳水 24.7g
        - 鸡蛋 60g: 86.4 kcal
        - 大米 100g: 346.0 kcal
    """
    if not result:
        return ""
    lines = [
        f"总热量 {result['calories']:.1f} kcal，"
        f"蛋白质 {result['protein_g']:.1f}g，"
        f"脂肪 {result['fat_g']:.1f}g，"
        f"碳水 {result['carbs_g']:.1f}g"
    ]
    for ing in result.get("per_ingredient", []):
        if ing.get("matched"):
            lines.append(f"- {ing['name']} {ing['amount_g']:.0f}g: {ing['calories']:.1f} kcal")
        else:
            lines.append(f"- {ing['name']} {ing['amount_g']:.0f}g: 未匹配成分表")
    return "\n".join(lines)


# 供 cooking_core 换算后拼入 LLM 的 prompt 片段。
# 占位符：
#   {servings}            目标份数
#   {nutrition_summary}   format_nutrition_summary 生成的文本
#   {calories}            总卡路里
#   {protein_g}           总蛋白质
#   {fat_g}               总脂肪
#   {carbs_g}             总碳水
# 使用方式（接入 cooking_core 时，不要修改本文件，由 cooking_core 侧拼装）：
#   from backend.app.nutrition import NUTRITION_PROMPT, calc_nutrition, format_nutrition_summary
#   nut = calc_nutrition(parsed_ingredients)
#   prompt = NUTRITION_PROMPT.format(
#       servings=servings,
#       nutrition_summary=format_nutrition_summary(nut),
#       calories=nut["calories"],
#       protein_g=nut["protein_g"],
#       fat_g=nut["fat_g"],
#       carbs_g=nut["carbs_g"],
#   )
#   # 然后把 prompt 追加到 SERVINGS_CALCULATION_PROMPT 的输出之后，让 LLM 生成健康话术。
NUTRITION_PROMPT = """【营养数据】
本次按 {servings} 人份计算，预计每份营养摄入：
{nutrition_summary}

总热量 {calories} kcal，蛋白质 {protein_g}g，脂肪 {fat_g}g，碳水 {carbs_g}g。

【任务】
请基于以上营养数据，用 2-3 句话给用户一段简短、口语化的健康饮食提示：
1. 评价本餐的营养结构（热量/蛋白/脂肪/碳水是否均衡）。
2. 针对过高的脂肪/热量给出一句可操作的改善建议（如减少用油、搭配蔬菜）。
3. 若蛋白质偏低，建议补充蛋白来源。
4. 控制在 80 字以内，语气亲切，避免堆砌数字；可用 1 个 emoji 点缀。

要求：仅输出话术文本，不要 JSON、不要 markdown 标题。"""


def build_nutrition_prompt(
    result: dict[str, Any],
    servings: int = 1,
) -> str:
    """
    便捷封装：根据 calc_nutrition 结果 + 份数，直接生成可喂给 LLM 的 prompt 字符串。
    """
    return NUTRITION_PROMPT.format(
        servings=servings,
        nutrition_summary=format_nutrition_summary(result),
        calories=result.get("calories", 0),
        protein_g=result.get("protein_g", 0),
        fat_g=result.get("fat_g", 0),
        carbs_g=result.get("carbs_g", 0),
    )


__all__ = [
    "DEFAULT_COMPOSITION_PATH",
    "NUTRITION_PROMPT",
    "load_composition",
    "match_ingredient",
    "calc_nutrition",
    "format_nutrition_summary",
    "build_nutrition_prompt",
]
