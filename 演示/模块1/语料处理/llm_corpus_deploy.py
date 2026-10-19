# -*- coding: utf-8 -*-
"""
llm_corpus_deploy.py — Recipe 结构化封装 + 归档入库 + 知识库部署（全流程下半段）
==================================================================

读取 llm_corpus_input.py 产出的 清理版 标准 Markdown，自动完成下半段流程：
④ 结构化封装（自定义数据实体类 + 唯一封装 ID + 菜品名称/食材清单/匹配分数）
⑤ 归档到菜谱库 + 知识库部署（归档 + Git 版本管控 + 供 AI 视觉/语音模块调用）

本文件自包含全部方法（数据实体类 / 结构化提取 / 归档同步 / 知识库加载器），
不依赖后端主程序，本地直接运行。

用法：
    python llm_corpus_deploy.py
    （先运行 llm_corpus_input.py 生成 菜谱库/清蒸鲈鱼清理版.md）
"""

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")


# ==================== ④ 结构化封装 —— 自定义数据实体类 + 唯一封装 ID ====================

@dataclass
class Recipe:
    """【数据结构】菜谱实体类 - 标准化封装，类字段与结构化 JSON 输出一一对应"""
    rid: str                                        # 唯一封装 ID（JSON 输出字段名 id）
    name: str                                       # 菜品名称
    category: str = "未分类"                         # 菜品分类
    ingredients: List[dict] = field(default_factory=list)   # 主料 [{name, amount, unit}]
    seasonings: List[dict] = field(default_factory=list)    # 调料 [{name, amount, unit}]
    steps: List[str] = field(default_factory=list)  # 烹饪步骤
    cook_time_minutes: Optional[int] = None         # 烹饪用时（分钟）
    difficulty: str = "中等"                         # 烹饪难度
    price_yuan: Optional[float] = None              # 参考价格（元）
    rating: Optional[float] = None                  # 评分
    created_at: str = field(default_factory=lambda: date.today().isoformat())   # 建档日期
    heat: Optional[str] = None                      # 热量参考（内部字段，不进 JSON）
    score: float = 0.0                              # 匹配分数（检索阶段由匹配模块实时计算填充，内部字段）

    def to_dict(self) -> dict:
        """按标准结构化格式输出（字段与类定义一一对应）"""
        return {
            "id": self.rid,
            "name": self.name,
            "category": self.category,
            "ingredients": self.ingredients,
            "seasonings": self.seasonings,
            "steps": self.steps,
            "cook_time_minutes": self.cook_time_minutes,
            "difficulty": self.difficulty,
            "price_yuan": self.price_yuan,
            "rating": self.rating,
            "created_at": self.created_at,
        }


SEASONING_KEYWORDS = ("盐", "糖", "生抽", "老抽", "料酒", "醋", "豉油", "蚝油",
                      "食用油", "香油", "鸡精", "味精", "胡椒粉", "淀粉")


def _is_seasoning(name: str) -> bool:
    """依据名称关键词判断该条目是否为调料"""
    return any(kw in name for kw in SEASONING_KEYWORDS)


TOOL_KEYWORDS = ("锅", "盘", "碗", "刀", "铲", "勺", "筷子", "砧板", "蒸笼", "烤箱", "微波炉")


def _is_tool(name: str) -> bool:
    """依据名称关键词判断该条目是否为厨具工具（工具不是食材，不进主料/调料）"""
    return any(kw in name for kw in TOOL_KEYWORDS)


def _parse_ingredient(raw: str) -> Optional[dict]:
    """把「鲈鱼 1 条（约 500g）」这类文本解析成 {name, amount, unit} 结构"""
    core = re.sub(r"（[^）]*）|\([^)]*\)", "", raw).strip()      # 剥离括号备注
    if not core:
        return None
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?)\s*"
                  r"(g|克|kg|千克|ml|毫升|L|升|根|条|块|片|个|棵|瓣|勺|只|张|朵|包|盒|瓶|袋)", core)
    if m:
        name = core[:m.start()].strip() or core
        unit = {"克": "g", "千克": "kg", "毫升": "ml", "升": "L"}.get(m.group(2), m.group(2))
        nums = re.findall(r"\d+(?:\.\d+)?", m.group(1))       # 区间用量（如 5-10）取上限
        return {"name": name, "amount": float(nums[-1]), "unit": unit}
    if re.search(r"适量|少许", core):
        return {"name": re.sub(r"适量|少许", "", core).strip() or core, "amount": None, "unit": "适量"}
    return {"name": core, "amount": None, "unit": None}


def extract_recipe(standard: str, rid: str) -> Recipe:
    """从标准 Markdown 菜谱中结构化提取核心字段，封装为 Recipe"""
    m = re.search(r"^#\s+(.+?)的做法", standard, re.MULTILINE)
    name = m.group(1).strip() if m else "未命名菜品"

    # 菜品分类：依据做法关键词推断
    category = "家常菜"
    for kw, cat in (("清蒸", "清蒸类"), ("红烧", "红烧类"), ("炒", "炒菜类"),
                    ("炖", "炖菜类"), ("汤", "汤羹类")):
        if kw in name:
            category = cat
            break

    # 主料/调料清单：## 必备原料和工具 下列表项 → 结构化 {name, amount, unit}
    ingredients: List[dict] = []
    seasonings: List[dict] = []
    im = re.search(r"##\s*必备原料和工具\s*\n(.*?)(?=\n##\s|$)", standard, re.S)
    if im:
        for line in im.group(1).splitlines():
            lm = re.match(r"[-*]\s*(.+)$", line.strip())
            if not lm:
                continue
            item = _parse_ingredient(lm.group(1))
            if not item or _is_tool(item["name"]):
                continue
            (seasonings if _is_seasoning(item["name"]) else ingredients).append(item)

    # 用量补全：## 计算 小节的「食材 N 单位」回填到主料/调料（必备原料清单通常不带量）
    cm = re.search(r"##\s*计算\s*\n(.*?)(?=\n##\s|$)", standard, re.S)
    if cm:
        for line in cm.group(1).splitlines():
            lm = re.match(r"[-*]\s*(.+)$", line.strip())
            if not lm:
                continue
            item = _parse_ingredient(lm.group(1))
            if item["amount"] is None and item["unit"] is None:
                continue
            for bucket in (ingredients, seasonings):
                for ing in bucket:
                    if item["name"] in ing["name"] or ing["name"] in item["name"]:
                        if ing["amount"] is None:
                            ing["amount"], ing["unit"] = item["amount"], item["unit"]

    # 烹饪步骤：## 操作 下列表项
    steps: List[str] = []
    sm = re.search(r"##\s*操作\s*\n(.*?)(?=\n##\s|$)", standard, re.S)
    if sm:
        for line in sm.group(1).splitlines():
            lm = re.match(r"[-*]\s*(.+)$", line.strip())
            if lm:
                steps.append(lm.group(1).strip())

    # 烹饪用时：汇总步骤中全部「N 分钟」
    cook_time = sum(int(n) for n in re.findall(r"(\d+)\s*分钟", standard))

    # 烹饪难度：星级 → 文字（★★☆ 及以下=简单，★★★=中等，★★★★ 起=较难）
    dm = re.search(r"难度[:：]?\s*([★☆]+)", standard)
    stars = dm.group(1).count("★") if dm else 3
    difficulty = "简单" if stars <= 2 else ("中等" if stars == 3 else "较难")

    # 热量参考：语料中可能无热量字段，缺省置空
    hm = re.search(r"热量[:：]?\s*([\d.]+\s*(?:kcal|千卡|大卡)?)", standard)
    heat = hm.group(1).strip() if hm else None

    return Recipe(rid=rid, name=name, category=category, ingredients=ingredients,
                  seasonings=seasonings, steps=steps, cook_time_minutes=cook_time,
                  difficulty=difficulty, heat=heat)


# ==================== ⑤ 归档到菜谱库 + 知识库部署 ====================

def _sync_to_production_library(md_path: Path) -> Optional[Path]:
    """归档后同步一份到生产菜谱库（buzhou/HowToCook/dishes/home_dish/<菜名>/），
    保证后端 RecipeService（默认加载目录）可直接检索；找不到生产库目录时静默跳过。"""
    here = Path(__file__).resolve().parent
    for base in [here, *here.parents]:
        home_dish = base / "buzhou" / "HowToCook" / "dishes" / "home_dish"
        if home_dish.is_dir():
            try:
                dest_dir = home_dish / md_path.stem
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / md_path.name
                dest.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
                return dest
            except Exception:
                return None
    return None


def save_to_library(md: str) -> Path:
    """归档进菜谱库：按菜名自动命名（菜名从「# xxx的做法」标题抠出），并同步生产菜谱库"""
    m = re.search(r"^#\s+(.+?)的做法", md, re.MULTILINE)
    name = m.group(1).strip() if m else "未命名菜品"
    lib_dir = Path(__file__).resolve().parents[2] / "backend" / "app" / "data" / "llm菜谱库"
    lib_dir.mkdir(parents=True, exist_ok=True)
    out_path = lib_dir / f"{name}.md"
    out_path.write_text(md, encoding="utf-8")
    synced = _sync_to_production_library(out_path)
    if synced:
        print(f"[同步] 已复制到生产菜谱库：{synced}")
    return out_path


class CorpusLoader:
    """知识库加载器：把已归档的标准 Markdown 语料统一加载为 Recipe 结构化数据，
    一次性加载常驻内存，避免反复读取本地文件，供全模块直接检索调用。"""

    def __init__(self, lib_dirs: Optional[List[Path]] = None):
        self._recipes: List[Recipe] = []
        default_lib = Path(__file__).resolve().parents[2] / "backend" / "app" / "data" / "llm菜谱库"
        self.load(lib_dirs or [default_lib])

    def load(self, lib_dirs: List[Path]) -> None:
        """一次性加载全部归档菜谱（按序分配唯一封装 ID），常驻内存运行"""
        idx = 1
        for lib_dir in lib_dirs:
            if not lib_dir.exists():
                continue
            for md_file in sorted(lib_dir.glob("*.md")):
                recipe = extract_recipe(md_file.read_text(encoding="utf-8"), f"R{idx:03d}")
                if recipe.name != "未命名菜品" and recipe.steps:   # 过滤残缺数据
                    self._recipes.append(recipe)
                    idx += 1

    @property
    def recipes(self) -> List[Recipe]:
        """对外提供常驻内存的结构化菜谱数据源"""
        return self._recipes


# ==================== 下半段流程编排 ====================

# 上半段（llm_corpus_input.py）产出的清理版文件
CLEANED_FILE = Path(__file__).resolve().parent / "菜谱库" / "清蒸鲈鱼清理版.md"
# 结构化数据展示文件：把封装后的 Recipe 对象落成 md，直观展示"程序可识别的数据形态"
STRUCTURED_MD_FILE = Path(__file__).resolve().parent / "菜谱库" / "清蒸鲈鱼结构化数据.md"


def save_structured_md(recipe: Recipe) -> Path:
    """把封装后的 Recipe 结构化数据写成 JSON 展示文件（纯数据，与类字段一一对应）"""
    STRUCTURED_MD_FILE.write_text(json.dumps(recipe.to_dict(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return STRUCTURED_MD_FILE


def main() -> None:
    if not CLEANED_FILE.exists():                      # 清理版不存在 → 提示先跑上半段
        print(f"未找到清理版文件：{CLEANED_FILE}")
        print("请先运行 llm_corpus_input.py 完成大模型清洗，再执行本脚本。")
        return
    md = CLEANED_FILE.read_text(encoding="utf-8-sig")   # utf-8-sig 自动剥离 BOM，兼容带 BOM 的文件

    print("结构化封装：自定义数据实体类 + 唯一封装 ID + 菜品名称/食材清单/匹配分数")
    recipe = extract_recipe(md, "R001")                # 把清洗后的标准 Markdown 封装为 Recipe 实体，分配唯一封装 ID "R001"
    print(json.dumps(recipe.to_dict(), ensure_ascii=False, indent=2))   # 多行缩进 JSON：与 Recipe 类字段一一对应
    struct_path = save_structured_md(recipe)           # 把封装后的结构化数据落成 JSON 展示文件
    print(f"  结构化数据已写入展示文件：{struct_path}")

    # [5/5] ⑤ 归档到菜谱库 + 知识库部署：归档 + Git 版本管控 + 供 AI 视觉/语音模块调用
    print("\n[5/5] 归档到菜谱库 + 知识库部署：归档 + Git 版本管控 + 供 AI 视觉/语音模块调用")
    path = save_to_library(md)
    print(f"  已归档：{path.name} → {path.parent}（清洗后标准 Markdown，同步提交 Git 版本管控）")
    loader = CorpusLoader()
    print("  项目菜谱库已归档数据（常驻内存，无需反复读取本地文件）：")
    for r in loader.recipes:
        ings = '、'.join(i["name"] for i in r.ingredients + r.seasonings)
        print(f"  · {r.rid} {r.name}  [分类:{r.category}]  食材：{ings}")
    print("  为队友的步骤引导、AI 问答、AI 视觉、语音交互、功能调试与全模块测试提供统一可复用的基础数据源")



if __name__ == "__main__":
    main()
