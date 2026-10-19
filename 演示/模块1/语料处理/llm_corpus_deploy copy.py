
import re
import sys
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


# ==================== ① 数据封装：脏语料 → 结构化数据实体 ====================

@dataclass
class Recipe:                                      # 自定义菜谱数据实体类（标准封装形式）
    rid: str                                       # 唯一封装 ID（每条数据唯一标识）
    name: str                                      # 菜品名称
    ingredients: List[str]                         # 食材清单
    category: str = "未分类"                        # 菜品分类
    steps: List[str] = field(default_factory=list)  # 烹饪步骤
    heat: Optional[str] = None                     # 热量参考
    score: float = 0.0                             # 匹配分数（检索阶段由匹配模块实时计算填充）


def extract_recipe(standard: str, rid: str) -> Recipe:
    """结构化提取函数：从标准 Markdown 菜谱中抠出核心字段，封装为 Recipe 实体"""
    m = re.search(r"^#\s+(.+?)的做法", standard, re.MULTILINE)   # 从一级标题抠出菜名
    name = m.group(1).strip() if m else "未命名菜品"

    # 菜品分类：依据做法关键词推断
    category = "家常菜"
    for kw, cat in (("清蒸", "清蒸类"), ("红烧", "红烧类"), ("炒", "炒菜类"),
                    ("炖", "炖菜类"), ("汤", "汤羹类")):
        if kw in name:
            category = cat
            break

    # 食材清单：## 必备原料和工具 下列表项，剥离括号备注
    ingredients: List[str] = []
    im = re.search(r"##\s*必备原料和工具\s*\n(.*?)(?=\n##\s|$)", standard, re.S)
    if im:
        for line in im.group(1).splitlines():
            lm = re.match(r"[-*]\s*(.+)$", line.strip())
            if lm:
                ing = re.sub(r"（[^）]*）|\([^)]*\)", "", lm.group(1)).strip()
                if ing:
                    ingredients.append(ing)

    # 烹饪步骤：## 操作 下列表项
    steps: List[str] = []
    sm = re.search(r"##\s*操作\s*\n(.*?)(?=\n##\s|$)", standard, re.S)
    if sm:
        for line in sm.group(1).splitlines():
            lm = re.match(r"[-*]\s*(.+)$", line.strip())
            if lm:
                steps.append(lm.group(1).strip())

    # 热量参考：语料中可能无热量字段，缺省置空
    hm = re.search(r"热量[:：]?\s*([\d.]+\s*(?:kcal|千卡|大卡)?)", standard)
    heat = hm.group(1).strip() if hm else None

    return Recipe(rid=rid, name=name, ingredients=ingredients,
                  category=category, steps=steps, heat=heat)


# ==================== 主流程：数据封装 + 知识库加载 ====================

def deploy_recipe_knowledge(cleaned_file: str, rid: str):
    """部署菜谱知识库：通用接口，传入任意清理版文档 → 封装 Recipe 实体 → 知识库加载常驻内存"""
    file_path = Path(__file__).resolve().parent / "菜谱库" / cleaned_file
    if not file_path.exists():                          # 清理版不存在 → 提示先跑上半段
        raise FileNotFoundError(f"未找到清理版文件：{file_path}，请先运行 llm_corpus_input.py")

   # md = file_path.read_text(encoding="utf-8-sig")       # ① 读入清洗后的标准 Markdown 文档
    recipe = extract_recipe(md, rid)                     # ② 数据封装：文档 → 食谱 对象（唯一封装 ID）
    loader = CorpusLoader()                              # ③ 知识库加载：菜谱库归档数据 → 常驻运行内存
    recipes = loader.recipes                             # ④ 取出常驻内存的全部结构化菜谱数据
    if recipe.name == "未命名菜品" or not recipe.steps:   # ⑤ 封装结果自检：残缺数据不进知识库
        raise ValueError(f"{cleaned_file} 封装失败：菜名或步骤缺失")
    return recipe, recipes                               # ⑥ 返回：单条实体 + 常驻内存的全部数据


# ==================== ② 知识库加载：结构化数据 → 常驻运行内存 ====================

class CorpusLoader:                                # 知识库加载器
    """把已归档的标准 Markdown 语料统一加载为 Recipe 结构化数据，
    一次性加载常驻内存，避免反复读取本地文件，供全模块直接检索调用。"""

    def __init__(self, lib_dirs: Optional[List[Path]] = None):
        self._recipes: List[Recipe] = []           # 内存中的结构化数据仓库
        default_lib = Path(__file__).resolve().parents[2] / "backend" / "app" / "data" / "llm菜谱库"
        self.load(lib_dirs or [default_lib])       # 构造即加载，一步到位

    def load(self, lib_dirs: List[Path]) -> None:
        idx = 1
        for lib_dir in lib_dirs:
            if not lib_dir.exists():
                continue
            for md_file in sorted(lib_dir.glob("*.md")):          # 遍历菜谱库全部归档 md
                recipe = extract_recipe(md_file.read_text(encoding="utf-8"), f"R{idx:03d}")
                                                               # 每份归档文档 → Recipe 实体（按序分配唯一 ID）
                if recipe.name != "未命名菜品" and recipe.steps:   # 过滤残缺数据，脏数据不进内存
                    self._recipes.append(recipe)               # 装入内存仓库
                    idx += 1

    @property
    def recipes(self) -> List[Recipe]:
        return self._recipes                        # 对外提供常驻内存的数据源


if __name__ == "__main__":
    deploy_recipe_knowledge("清蒸鲈鱼清理版.md", "R001")

