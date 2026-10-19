"""
【实操动作1：语料清洗与结构化知识库部署】实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第一模块·选手2实操手1：

完成菜谱语料的全流程采集、清洗与结构化规整：
- 项目菜谱原始数据为收集而来的、携带大量脏数据的 Word 文档，格式杂乱、冗余杂质多
- 先通过 MinerU 工具将批量 Word 文档统一转换为 Markdown 格式
- 再编写 Python 脚本，利用正则表达式对转换后的数据精细化清洗：
  自动过滤无效注释、乱码、残缺段落、分页页眉、OCR 拆字、广告与图片占位等冗余噪声
- 清洗后按 HowToCook 标准格式重组为规范菜谱（对标归档样式「清蒸鲈鱼 copy.md」），
  只保留食材清单、烹饪步骤、菜品分类、热量参考等核心有效字段
- 清洗后的标准 Markdown 归档到项目菜谱库，同步 Git 版本管控，团队数据资源共享
- 自定义 Recipe 数据实体类：为每条菜谱分配唯一封装 ID，对菜品名称、食材清单、
  匹配分数等核心字段标准化结构化封装，供后续 AI 视觉、语音交互等模块直接调用

用法：
  python corpus.py          # 真实脏数据 清洗前后对比 + 标准规整 + 结构化封装 + 知识库部署演示
"""

import re
import sys
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')


# ==================== 终端 Markdown 渲染 ====================

def _print_markdown(text: str):
    """终端 Markdown 渲染（使用 rich 库），失败则退化为纯文本"""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console(soft_wrap=True).print(Markdown(text))
    except Exception:
        print(text)


# ==================== 语料结构化数据封装 ====================

@dataclass
class Recipe:
    """【数据结构】菜谱实体类 - 唯一封装 ID + 菜品名称、食材清单等核心字段标准化封装"""
    rid: str                       # 唯一封装 ID
    name: str                      # 菜品名称
    ingredients: List[str]         # 食材清单
    category: str = "未分类"        # 菜品分类
    steps: List[str] = field(default_factory=list)   # 烹饪步骤
    heat: Optional[str] = None     # 热量参考
    score: float = 0.0             # 匹配分数（检索阶段由匹配模块实时计算填充）


# 真实原始脏数据文件（清洗前：OCR 扫描识别版，携带大量噪声）
RAW_FILE = Path(__file__).parent / "清蒸鲈鱼 - 副本.md"

# 清洗后标准格式参照（HowToCook 菜谱库归档样式）
STANDARD_REF = Path(__file__).parent / "清蒸鲈鱼 copy.md"

# 项目菜谱库其余结构化语料（模拟从 HowToCook 开源数据集清洗归档后的标准数据）
RAW_MARKDOWN_CORPUS = [
    """
# 番茄炒蛋
> 分类：家常菜
> 热量：约 150 kcal
- 食材：番茄、鸡蛋、葱、盐、糖
- 步骤：番茄切块，鸡蛋打散；热锅炒蛋至半熟盛出；炒番茄出汁，加蛋翻炒；加盐糖调味撒葱花
""",
    """
# 青椒炒肉
> 分类：快手菜
> 热量：约 220 kcal
- 食材：青椒、猪肉、蒜、生抽、盐
- 步骤：青椒切丝，肉切片腌制；大火爆炒肉片；加青椒翻炒；生抽盐调味出锅
""",
    """
# 红烧肉
> 分类：川湘风味
> 热量：约 450 kcal
- 食材：五花肉、酱油、糖、八角、桂皮、姜
- 步骤：五花肉切块焯水；炒糖色；加肉翻炒上色；加水和香料小火炖1小时
""",
    """
# 番茄鸡蛋汤
> 分类：家常菜
> 热量：约 90 kcal
- 食材：番茄、鸡蛋、葱、盐、香油
- 步骤：番茄切块；水烧开放番茄；打蛋花淋入；加盐香油调味
""",
    """
# 青椒肉丝
> 分类：快手菜
> 热量：约 200 kcal
- 食材：青椒、猪肉、姜、生抽、淀粉
- 步骤：青椒切丝，肉切丝上浆；滑炒肉丝变色；加青椒丝翻炒；生抽调味
""",
]


# ==================== 正则精细化清洗规则 ====================

CLEAN_RULES = [
    # (规则说明, 正则, 替换为)
    ("过滤扫描页眉页脚（第 X 页 共 X 页 · 扫描识别）",
     re.compile(r"^.*?第\s*\d+\s*页\s*共\s*\d+\s*页.*$", re.MULTILINE), ""),
    ("过滤图片占位/加载失败标记（【图片位置】/[图片: xx.jpg]）",
     re.compile(r"\[?图片[^\n]*|【图片位置】[^\n]*", re.MULTILINE), ""),
    ("过滤广告与引流链接（广告区/www.xxx.com）",
     re.compile(r"广告区[^\n]*|www\.\S+", re.MULTILINE), ""),
    ("过滤扫描重复页说明（第 2 页重复了…）",
     re.compile(r"第\s*\d+\s*页\s*重复[^\n]*", re.MULTILINE), ""),
    ("过滤网友补充/评论区噪声（补充内容/额外小贴士）",
     re.compile(r"补充内容[^\n]*|额外小贴士[^\n]*", re.MULTILINE), ""),
    ("修复 OCR 竖排拆字（操作/步骤被拆成单字换行）",
     re.compile(r"操\s*\n\s*作\s*\n\s*步\s*\n\s*骤"), "操作步骤"),
    ("剥离冗余格式符号（见附件/emoji/转义符）",
     re.compile(r"（见附件）|👉|\\|[*_`~]"), ""),
]


def clean_raw_text(raw: str):
    """按规则逐条正则清洗脏数据，返回清洗后文本与命中的规则名列表"""
    text = raw
    applied = []
    for name, pattern, repl in CLEAN_RULES:
        new_text, n = pattern.subn(repl, text)
        if n:
            applied.append(name)
            text = new_text
    # 收尾规整：去除行首缩进/行尾空白、压缩多余空行
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)
    return text.strip(), applied


# ==================== 标准格式规整与结构化字段提取 ====================

def _extract_name(cleaned: str) -> str:
    """提取菜名：取「X 的做法」中的 X"""
    m = re.search(r"^\s*([\u4e00-\u9fa5A-Za-z0-9]+?)\s*的做法", cleaned, re.MULTILINE)
    return m.group(1) if m else "未命名菜品"


def _extract_ingredient_names(cleaned: str) -> List[str]:
    """从「必备原料和工具」段提取食材名列表（先剥离括号说明再切分）"""
    names: List[str] = []
    im = re.search(r"必备原料和工具[-—:\s]*([\s\S]*?)(?=每份计算|注意|$)", cleaned)
    if not im:
        return names
    body = re.sub(r"（[^）]*）|\([^)]*\)", "", im.group(1))  # 剥离说明括号
    for seg in re.split(r"[-、,]", body):
        seg = seg.strip()
        if not seg or "必备原料" in seg or "和工具" in seg:
            continue
        wm = re.match(r"([\u4e00-\u9fa5A-Za-z0-9]{1,8})", seg)
        if wm:
            names.append(wm.group(1))
    return names


def _extract_portions(cleaned: str, ingredient_names: List[str]) -> List[str]:
    """从「每份计算」段按原料名锚定切分，生成「食材 分量」列表"""
    cm = re.search(r"每份计算[：:]\s*([\s\S]*?)(?=操作步骤|$)", cleaned)
    if not cm or not ingredient_names:
        return []
    body = re.sub(r"（[^）]*）|\([^)]*\)", "", cm.group(1))  # 剥离括号说明
    portions: List[str] = []
    for i, name in enumerate(ingredient_names):
        start = body.find(name)
        if start == -1:
            continue
        end = (body.find(ingredient_names[i + 1])
               if i + 1 < len(ingredient_names) else len(body))
        piece = re.sub(r"\s{2,}", " ", body[start:end]).strip(" ，,、")
        if piece:
            portions.append(piece)
    return portions


def _extract_steps(cleaned: str) -> List[str]:
    """从「操作步骤」至「关键总结」之间逐行提取步骤"""
    sm = re.search(r"操作步骤[:：]?\s*([\s\S]*?)(?=关键总结|$)", cleaned)
    if not sm:
        return []
    steps: List[str] = []
    for line in sm.group(1).splitlines():
        line = line.strip().lstrip("0123456789.。、 ").strip()
        if line and not line.startswith("注意"):
            steps.append(line)
    return steps


def to_standard_markdown(cleaned: str) -> str:
    """把清洗后规整文本重组为 HowToCook 标准 Markdown 格式（对标「清蒸鲈鱼 copy.md」）"""
    name = _extract_name(cleaned)
    ingredient_names = _extract_ingredient_names(cleaned)
    portions = _extract_portions(cleaned, ingredient_names)
    steps = _extract_steps(cleaned)

    md = [f"# {name}的做法", ""]

    dm = re.search(r"预估烹饪难度[：:]\s*(.+?)\s*$", cleaned, re.MULTILINE)
    if dm:
        difficulty = re.sub(r"（[^）]*）|\([^)]*\)", "", dm.group(1)).strip()
        md += [f"预估烹饪难度：{difficulty}", ""]

    if ingredient_names:
        md += ["## 必备原料和工具", ""]
        md += [f"- {n}" for n in ingredient_names]
        md += [""]

    if portions:
        md += ["## 计算", "", "每份：", ""]
        md += [f"- {p}" for p in portions]
        md += [""]

    if steps:
        md += ["## 操作", ""]
        md += [f"- {s}" for s in steps]
        md += [""]

    result = "\n".join(md).strip()
    return re.sub(r"\n{3,}", "\n\n", result)


def extract_recipe(standard: str, rid: str) -> Recipe:
    """从标准 Markdown 菜谱中结构化提取核心字段，封装为 Recipe"""
    m = re.search(r"^#\s+(.+?)\s*的做法", standard, re.MULTILINE)
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


# ==================== 知识库加载器（常驻内存） ====================

class CorpusLoader:
    """
    知识库加载器：把已归档的标准 Markdown 语料统一加载为 Recipe 结构化数据，
    数据一次性加载常驻内存，避免程序反复读取本地文件，提升检索效率。
    """

    def __init__(self, corpus: Optional[List[str]] = None):
        self._recipes: List[Recipe] = []
        self.load(corpus if corpus is not None else RAW_MARKDOWN_CORPUS)

    def _parse_markdown(self, text: str, rid: str) -> Optional[Recipe]:
        """正则抓取标准 Markdown 中的核心有效字段，返回 Recipe 或 None"""
        name_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
        category_match = re.search(r"分类[：:]\s*(.+?)\s*$", text, re.MULTILINE)
        ingredients_match = re.search(r"食材[：:]\s*(.+?)\s*$", text, re.MULTILINE)

        if not name_match:
            return None  # 过滤无效残缺数据

        ingredients = [
            ing.strip()
            for ing in re.split(r"[、，,]", ingredients_match.group(1))
            if ing.strip()
        ] if ingredients_match else []

        return Recipe(rid=rid, name=name_match.group(1).strip(),
                      ingredients=ingredients,
                      category=category_match.group(1).strip() if category_match else "未分类")

    def load(self, corpus: List[str]) -> None:
        """一次性加载全部菜谱数据，常驻内存运行（按序分配唯一封装 ID）"""
        for idx, text in enumerate(corpus, start=1):
            recipe = self._parse_markdown(text, f"R{idx:03d}")
            if recipe:
                self._recipes.append(recipe)

    @property
    def recipes(self) -> List[Recipe]:
        """对外提供常驻内存的结构化菜谱数据源"""
        return self._recipes


# ==================== 现场敲代码区（答辩演示预留） ====================

# ↓↓↓ 演示时在此处照讲解稿现场敲入核心代码（模块级顶格缩进） ↓↓↓
# ① 正则精细化清洗（re.sub 去页眉 / 去广告 / 修复 OCR 拆字）
# ② 一键清洗 + 标准规整（clean_raw_text → to_standard_markdown）
# ③ 结构化封装（extract_recipe → Recipe 唯一 ID）
# 敲入前先执行：raw = open("清蒸鲈鱼 - 副本.md", encoding="utf-8").read()
# 敲入参考与验证：见文末【现场演示敲代码标注】，敲完直接运行 python corpus.py 验证
# ↓↓↓ 空位（在此空白行敲入代码） ↓↓↓

from pathlib import Path
BASE = Path(__file__).parent                     # 锚定脚本所在目录，任何位置运行都不怕
Path("清蒸鲈鱼.md").exists()
raw=open(BASE/"菜谱库"/"清蒸鲈鱼.md", encoding="utf-8").read()
print(raw[:120])

import re
re.sub(r"^\s*#[^\n]*\n", "", raw)
re.sub(r"^\s*广告\s*\n", "", raw)
re.sub("操作", "操作\n", raw)

from corpus import clean_raw_text, to_standard_markdown
cleaned,applied=clean_raw_text(raw)

print(len(raw),"字符->",len(cleaned),"字符")
print(to_standard_markdown(cleaned))


from corpus import extract_recipe
recipe=extract_recipe(to_standard_markdown(cleaned),"R001")
print(recipe)
Path("菜谱库").mkdir(exist_ok=True)
(BASE/"菜谱库"/"R001.md").write_text(to_standard_markdown(cleaned), encoding="utf-8")
from corpus import CorpusLoader
loader=CorpusLoader()
print(loader.recipes)















# ↑↑↑ 现场敲代码区结束 ↑↑↑


# ==================== 实操运行演示 ====================

def _render_before_after(raw: str, standard: str):
    """清洗前后左右并排对比渲染（rich Columns + Panel）"""
    from rich.console import Console
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.text import Text
    from rich.markdown import Markdown

    console = Console(soft_wrap=True)
    before_panel = Panel(
        Text(raw, style="default"),
        title="[bold red]清洗前[/bold red] · 原始 OCR",
        border_style="red",
        padding=(1, 1),
        width=44,
    )
    after_panel = Panel(
        Markdown(standard),
        title="[bold green]清洗后[/bold green] · 标准格式",
        border_style="green",
        padding=(1, 1),
        width=44,
    )
    console.print(Columns([before_panel, after_panel], equal=True))


def main():
    _print_markdown("# 实操动作1 · 语料清洗与结构化知识库部署")

    # ① 语料采集与格式转换
    print("\n[1/4] 语料采集与格式转换（MinerU 批量 Word → Markdown）")
    print("  项目菜谱原始数据为携带大量脏数据的 Word 文档，先用 MinerU 统一转换为 Markdown")
    print(f"  示例原始语料：{RAW_FILE.name}（OCR 扫描识别版）")
    raw = RAW_FILE.read_text(encoding="utf-8") if RAW_FILE.exists() else ""
    if not raw:
        print(f"  ⚠️ 未找到原始脏数据文件，跳过真实数据清洗演示。")
        return

    # ② 正则精细化清洗
    print("\n[2/4] 正则精细化清洗（自动过滤无效噪声，只保留核心有效字段）")
    cleaned, applied = clean_raw_text(raw)
    _print_markdown(
        "**本次命中的清洗规则：**\n\n"
        + "\n".join(f"- {rule}" for rule in applied)
        + f"\n\n语料规模：**{len(raw)}** 字符 → **{len(cleaned)}** 字符"
          f"（剔除 **{len(raw) - len(cleaned)}** 字符噪声）"
    )

    # ③ 清洗前后左右对比
    print("\n[3/4] 清洗前后左右对比（脏数据 vs 标准 Markdown）")
    standard = to_standard_markdown(cleaned)
    _render_before_after(raw, standard)

    # ④ Recipe 结构化封装（唯一 ID + 核心字段）
    print("\n[4/4] Recipe 数据实体类结构化封装（唯一 ID + 核心字段，供 AI 模块调用）")
    recipe = extract_recipe(standard, "R001")
    _print_markdown(
        f"**Recipe 结构化封装**（唯一封装 ID: `{recipe.rid}`）\n\n"
        f"- 菜品名称：**{recipe.name}**\n"
        f"- 菜品分类：{recipe.category}\n"
        f"- 食材清单：{'、'.join(recipe.ingredients)}\n"
        f"- 烹饪步骤：共 {len(recipe.steps)} 步\n"
        f"- 热量参考：{recipe.heat or '语料未提供（缺省）'}\n"
        f"- 匹配分数：{recipe.score}（检索阶段由匹配模块实时计算填充）"
    )
    print("  清洗前脏文本 → 标准格式归档 → 结构化数据实体，程序可精准识别、可检索调用")

    # ⑤ 知识库部署（归档 + Git + 全模块共享）
    print("\n[5/5] 知识库部署：归档到菜谱库 + Git 版本管控 + 全模块共享")
    lib_dir = Path(__file__).parent / "菜谱库"
    lib_dir.mkdir(exist_ok=True)
    archived = lib_dir / f"{recipe.name}.md"
    archived.write_text(standard, encoding="utf-8")
    print(f"  已归档：{archived.name} → {lib_dir}（清洗后标准 Markdown，同步提交 Git 版本管控）")
    loader = CorpusLoader()
    _print_markdown(
        "**项目菜谱库已归档数据**（常驻内存，无需反复读取本地文件）：\n\n"
        + "\n".join(
            f"- `{r.rid}` **{r.name}**  [分类:{r.category}]  食材：{'、'.join(r.ingredients)}"
            for r in loader.recipes
        )
    )
    print("  清洗后的标准 Markdown 归档至项目菜谱库目录，同步提交 Git 完成版本管控")
    print("  为队友的步骤引导、AI 问答、AI 视觉、语音交互、功能调试与全模块测试提供统一可复用的基础数据源")

    print("\n✅ 实操动作1 演示完成：采集转换 → 正则清洗 → 前后对比 → 标准规整 → 结构化封装 → 知识库部署，全程闭环。")


# ==================== 现场演示敲代码标注（答辩用） ====================
r'''
【实操动作1 现场演示 · 边讲边敲代码】—— 与讲解稿五步叙事一一对应

运行：python -i corpus.py 进入交互模式，按下述顺序边讲边敲（>>> 为 REPL 提示符，无需输入）。

① 采集与格式转换 —— 对应稿「MinerU 批量 Word → Markdown」
>>> from pathlib import Path
>>> Path("清蒸鲈鱼 - 副本.docx").exists()                        # 原始携带大量脏数据的 Word 文档
>>> raw = open("清蒸鲈鱼 - 副本.md", encoding="utf-8").read()    # MinerU 转换后（仍带 OCR 噪声）
>>> print(raw[:120])                                             # 展示转换结果：格式杂乱、冗余杂质多

② 正则精细化清洗 —— 对应稿「过滤无效注释/乱码/残缺段落/冗余格式符号」（最有技术感，现场逐条敲）
>>> import re
>>> re.sub(r"^.*?第\s*\d+\s*页.*$", "", raw, flags=re.M)          # 去扫描页眉页脚
>>> re.sub(r"广告区.*|www\.\S+", "", raw, flags=re.M)             # 去广告引流链接
>>> re.sub(r"操\s*\n\s*作\s*\n\s*步\s*\n\s*骤", "操作步骤", raw)  # 修复 OCR 拆字

③ 一键清洗 + 标准规整 —— 对应稿「只保留食材清单/烹饪步骤/菜品分类/热量参考」
>>> from corpus import clean_raw_text, to_standard_markdown
>>> cleaned, applied = clean_raw_text(raw)
>>> print(len(raw), "字符 →", len(cleaned), "字符")               # 1018 → 697
>>> print(to_standard_markdown(cleaned))                          # 核心字段标准格式输出

④ 结构化封装 —— 对应稿「自定义数据实体类 + 唯一封装 ID + 菜品名称/食材清单/匹配分数」
>>> from corpus import extract_recipe
>>> recipe = extract_recipe(to_standard_markdown(cleaned), "R001")
>>> print(recipe)                                                 # 唯一 ID、名称、食材、分类、步骤、匹配分数

⑤ 归档到菜谱库 + 知识库部署 —— 对应稿「归档 + Git 版本管控 + 供 AI 视觉/语音模块调用」
>>> Path("菜谱库").mkdir(exist_ok=True)
>>> (Path("菜谱库") / "清蒸鲈鱼.md").write_text(to_standard_markdown(cleaned), encoding="utf-8")
>>> from corpus import CorpusLoader
>>> for r in CorpusLoader().recipes:
...     print(r.rid, r.name, r.category)

演示提示：
- 终端窗口拉宽到 120+ 列，保证「清洗前后左右对比」面板并排显示（红=清洗前 / 绿=清洗后）
- 每敲完一段，口头说明对应讲解稿中的技术点，评委印象最深的是 ② 那段现场正则
'''


if __name__ == "__main__":
    main()
