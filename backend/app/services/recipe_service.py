# -*- coding: utf-8 -*-
"""
菜谱服务
读取和解析 HowToCook 菜谱文件，提供搜索和获取接口
"""

import sys
import os
import re
import difflib
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel, Field

# 配置路径以支持从 backend 目录导入
# 项目根目录是 backend 的父目录
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 菜谱目录路径
DISHES_DIR = _project_root / "buzhou" / "HowToCook" / "dishes"


class RecipeStep(BaseModel):
    """菜谱步骤"""
    step_number: int = Field(..., description="步骤序号")
    title: Optional[str] = Field(None, description="步骤标题")
    content: str = Field(..., description="步骤内容")


class Recipe(BaseModel):
    """菜谱详情"""
    name: str = Field(..., description="菜谱名称")
    difficulty: Optional[str] = Field(None, description="烹饪难度")
    ingredients: List[str] = Field(default_factory=list, description="食材列表")
    prep_items: List[str] = Field(default_factory=list, description="做饭前需要准备的原料和工具")
    servings_note: Optional[str] = Field(None, description="份量说明")
    per_serving_lines: List[str] = Field(default_factory=list, description="每份用量说明")
    steps: List[RecipeStep] = Field(default_factory=list, description="烹饪步骤")
    tips: Optional[str] = Field(None, description="小贴士")
    category: Optional[str] = Field(None, description="菜谱分类")


class RecipeSearchResult(BaseModel):
    """菜谱搜索结果"""
    name: str = Field(..., description="菜谱名称")
    category: Optional[str] = Field(None, description="菜谱分类")
    matched_ingredients: List[str] = Field(default_factory=list, description="匹配的食材")
    difficulty: Optional[str] = Field(None, description="烹饪难度")


class RecipeService:
    """菜谱服务类"""

    def __init__(self, dishes_dir: Path = DISHES_DIR):
        """
        初始化菜谱服务

        Args:
            dishes_dir: 菜谱目录路径
        """
        self.dishes_dir = dishes_dir
        self._recipes_cache: dict = {}
        self._recipes_loaded = False

    def _load_all_recipes(self) -> None:
        """加载所有菜谱文件"""
        if self._recipes_loaded:
            return

        self._recipes_cache = {}
        self._scan_directory(self.dishes_dir)
        self._recipes_loaded = True

    def _scan_directory(self, directory: Path) -> None:
        """递归扫描目录中的 markdown 文件"""
        if not directory.exists() or not directory.is_dir():
            return

        for item in directory.iterdir():
            if item.is_dir():
                # 递归扫描子目录
                self._scan_directory(item)
            elif item.suffix == ".md":
                # 解析菜谱文件
                recipe = self._parse_recipe_file(item)
                if recipe:
                    self._recipes_cache[recipe.name] = recipe

    def _parse_recipe_file(self, file_path: Path) -> Optional[Recipe]:
        """
        解析单个菜谱文件

        Args:
            file_path: 菜谱文件路径

        Returns:
            Recipe 对象，解析失败返回 None
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 提取菜谱名称（文件名前缀或目录名）
            name = self._extract_recipe_name(file_path)

            # 提取难度
            difficulty = self._extract_difficulty(content)

            # 提取食材列表
            ingredients = self._extract_ingredients(content)

            # 提取做饭准备信息
            prep_items = self._extract_preparation_items(content)
            servings_note = self._extract_servings_note(content)
            per_serving_lines = self._extract_per_serving_lines(content)

            # 提取步骤列表
            steps = self._extract_steps(content)

            # 提取小贴士
            tips = self._extract_tips(content)

            # 提取分类
            category = self._extract_category(file_path)

            return Recipe(
                name=name,
                difficulty=difficulty,
                ingredients=ingredients,
                prep_items=prep_items,
                servings_note=servings_note,
                per_serving_lines=per_serving_lines,
                steps=steps,
                tips=tips,
                category=category
            )
        except Exception as e:
            print(f"解析菜谱文件失败 {file_path}: {e}")
            return None

    def _extract_recipe_name(self, file_path: Path) -> str:
        """
        从文件路径提取菜谱名称

        Args:
            file_path: 文件路径

        Returns:
            菜谱名称
        """
        # 判断文件是在子目录中还是直接在分类目录下
        # 子目录菜谱: dishes/category/recipe_name/recipe_name.md
        # 直接文件: dishes/category/recipe_name.md
        parent_dir = file_path.parent

        if parent_dir == self.dishes_dir:
            # 文件直接在大 dishes 目录下（不应该存在这种情况）
            return file_path.stem
        elif parent_dir.parent == self.dishes_dir:
            # 文件直接在分类目录下（如 dishes/aquatic/咖喱炒蟹.md）
            # 使用文件名
            return file_path.stem
        else:
            # 文件在子目录中（如 dishes/meat_dish/回锅肉/回锅肉.md）
            # 使用子目录名
            return parent_dir.name

    def _extract_difficulty(self, content: str) -> Optional[str]:
        """从内容中提取难度等级"""
        match = re.search(r"预估烹饪难度[：:]\s*([★☆]+)", content)
        if match:
            return match.group(1)
        return None

    def _extract_ingredients(self, content: str) -> List[str]:
        """
        从内容中提取食材列表

        Args:
            content: 文件内容

        Returns:
            食材列表
        """
        ingredients = []

        # 匹配 ## 必备原料和工具 下的内容
        pattern = r"##\s*必备原料(?:和工具)?.*?(?=\n##|\n#|$)"
        match = re.search(pattern, content, re.DOTALL)

        if match:
            section = match.group(0)
            # 提取列表项（- 开头的行）
            items = re.findall(r"^\s*-\s+(.+?)$", section, re.MULTILINE)
            for item in items:
                # 清理 markdown 格式符号
                clean_item = self._clean_markdown(item)
                if clean_item:
                    ingredients.append(clean_item)

        return ingredients

    def _extract_section_lines(self, content: str, section_title: str) -> List[str]:
        lines: List[str] = []
        capturing = False

        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue

            if stripped.startswith("## "):
                heading = stripped[3:].strip()
                if heading == section_title:
                    capturing = True
                    continue
                if capturing:
                    break
                continue

            if not capturing:
                continue

            lines.append(stripped)

        return lines

    def _extract_preparation_items(self, content: str) -> List[str]:
        section_lines = self._extract_section_lines(content, "必备原料和工具")
        if not section_lines:
            return []

        section_text = "\n".join(section_lines)
        items: List[str] = []
        for match in re.findall(r"[*-]\s*([^*\n\r]+)", section_text):
            cleaned = self._clean_markdown(match.strip())
            if not cleaned:
                continue
            if cleaned in {"原料", "工具", "材料", "配料"}:
                continue
            if cleaned not in items:
                items.append(cleaned)

        if items:
            return items

        for line in section_lines:
            cleaned = self._clean_markdown(line)
            if cleaned and cleaned not in {"原料", "工具", "材料", "配料"} and cleaned not in items:
                items.append(cleaned)
        return items

    def _extract_servings_note(self, content: str) -> Optional[str]:
        section_lines = self._extract_section_lines(content, "计算")
        if not section_lines:
            return None

        note_lines: List[str] = []
        for line in section_lines:
            stripped = line.strip()
            if re.match(r"^(总量|每份|每份：)", stripped):
                break
            cleaned = self._clean_markdown(stripped)
            if cleaned:
                note_lines.append(cleaned)

        if not note_lines:
            return None
        return "\n".join(note_lines).strip()

    def _extract_per_serving_lines(self, content: str) -> List[str]:
        section_lines = self._extract_section_lines(content, "计算")
        if not section_lines:
            return []

        per_lines: List[str] = []
        capturing = False
        for line in section_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue
            if stripped.startswith("每份"):
                capturing = True
                continue
            if not capturing:
                continue
            if stripped.startswith("## "):
                break
            cleaned = self._clean_markdown(stripped)
            if cleaned:
                per_lines.append(cleaned)

        if per_lines:
            return per_lines

        total_lines: List[str] = []
        capturing_total = False
        for line in section_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue
            if stripped.startswith("总量"):
                capturing_total = True
                continue
            if stripped.startswith("每份"):
                break
            if not capturing_total:
                continue
            cleaned = self._clean_markdown(stripped)
            if cleaned:
                total_lines.append(cleaned)

        return total_lines

    def _extract_steps(self, content: str) -> List[RecipeStep]:
        """
        ???????????

        ???????????
        - ????? / ??????????
        - ?? bullet / ?????????
        - ?? bullet ????????
        - ?????? / ????? Issue ?????
        """
        steps: List[RecipeStep] = []
        operation_titles = {"".join(chr(c) for c in (0x64CD, 0x4F5C)), "".join(chr(c) for c in (0x505A, 0x6CD5))}
        tip_titles = {"".join(chr(c) for c in (0x5C0F, 0x8D16, 0x58EB)), "".join(chr(c) for c in (0x9644, 0x52A0, 0x5185, 0x5BB9))}
        footer_markers = {
            "Issue",
            "Pull request",
            "".join(chr(c) for c in (0x9075, 0x5FAA, 0x672C, 0x6307, 0x5357)),
            "参考资料",
            "参考文献",
            "参考链接",
        }

        current_heading = None
        capturing = False
        current_step: dict[str, Any] | None = None

        def flush_current() -> None:
            nonlocal current_step
            if current_step and current_step.get("content"):
                steps.append(RecipeStep(**current_step))
            current_step = None

        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue

            if stripped.startswith("## "):
                heading = stripped[3:].strip()
                if heading in operation_titles:
                    capturing = True
                    current_heading = heading
                    continue
                if heading in tip_titles:
                    break
                if capturing:
                    break
                current_heading = heading
                continue

            if not capturing:
                continue
            if any(marker in stripped for marker in footer_markers):
                break

            title_match = re.match(r"^###\s+(.+)", stripped)
            if title_match:
                flush_current()
                current_step = {
                    "step_number": len(steps) + 1,
                    "title": self._clean_markdown(title_match.group(1).strip()),
                    "content": "",
                }
                continue

            numbered_match = re.match(r"^(\d+)\.\s+(.+)", stripped)
            if numbered_match and not raw_line.startswith((" ", "\t")):
                flush_current()
                current_step = {
                    "step_number": len(steps) + 1,
                    "title": None,
                    "content": self._clean_markdown(numbered_match.group(2).strip()),
                }
                continue

            bullet_match = re.match(r"^[-*+]\s+(.+)", stripped)
            if bullet_match and not raw_line.startswith((" ", "\t")):
                flush_current()
                current_step = {
                    "step_number": len(steps) + 1,
                    "title": None,
                    "content": self._clean_markdown(bullet_match.group(1).strip()),
                }
                continue

            if current_step is None:
                current_step = {
                    "step_number": len(steps) + 1,
                    "title": None,
                    "content": "",
                }

            extra = self._clean_markdown(stripped)
            if not extra:
                continue
            if current_step["content"]:
                current_step["content"] += "\n" + extra
            else:
                current_step["content"] = extra

        flush_current()
        return steps

    def _extract_tips(self, content: str) -> Optional[str]:
        """
        ???????????????
        """
        tip_titles = {"".join(chr(c) for c in (0x5C0F, 0x8D16, 0x58EB)), "".join(chr(c) for c in (0x9644, 0x52A0, 0x5185, 0x5BB9))}
        footer_markers = {"Issue", "Pull request", "".join(chr(c) for c in (0x9075, 0x5FAA, 0x672C, 0x6307, 0x5357))}
        lines: list[str] = []
        capturing = False

        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue

            if stripped.startswith("## "):
                heading = stripped[3:].strip()
                if heading in tip_titles:
                    capturing = True
                    continue
                if capturing:
                    break
                continue

            if not capturing:
                continue
            if any(marker in stripped for marker in footer_markers):
                break

            cleaned = self._clean_markdown(stripped)
            if cleaned:
                lines.append(cleaned)

        if not lines:
            return None
        return "\n".join(lines).strip()

    def _extract_category(self, file_path: Path) -> Optional[str]:
        """
        从文件路径提取菜谱分类

        Args:
            file_path: 文件路径

        Returns:
            分类名称
        """
        try:
            relative = file_path.relative_to(self.dishes_dir)
            parts = relative.parts
            if len(parts) > 1:
                # 返回第一级目录作为分类
                return parts[0]
        except ValueError:
            pass
        return None

    def _clean_markdown(self, text: str) -> str:
        """
        清理 markdown 格式符号

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return ""

        # 移除图片语法 ![alt](url)
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        # 移除链接语法 [text](url)
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # 移除加粗 **text**
        text = re.sub(r"\*\*([^\*]+)\*\*", r"\1", text)
        # 移除斜体 *text*
        text = re.sub(r"\*(?!\*)([^\*]+)\*", r"\1", text)
        # 移除代码 `code`
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # 移除行内代码块
        text = re.sub(r"``[^`]*``", "", text)
        # 清理多余空白
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _fuzzy_match(self, text: str, keywords: List[str]) -> bool:
        """
        模糊匹配：检查关键词是否出现在文本中

        Args:
            text: 要匹配的文本
            keywords: 关键词列表

        Returns:
            是否匹配
        """
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True
        return False

    def search_by_ingredients(self, ingredients: List[str]) -> List[RecipeSearchResult]:
        """
        通过食材搜索菜谱

        Args:
            ingredients: 食材列表

        Returns:
            匹配的菜谱列表
        """
        self._load_all_recipes()

        results = []
        for name, recipe in self._recipes_cache.items():
            # 检查每个食材是否在菜谱中出现
            matched = []
            recipe_text = " ".join(recipe.ingredients)

            for ingredient in ingredients:
                if self._fuzzy_match(recipe_text, [ingredient]):
                    matched.append(ingredient)

            # 如果有匹配的食材，添加到结果
            if matched:
                results.append(RecipeSearchResult(
                    name=recipe.name,
                    category=recipe.category,
                    matched_ingredients=matched,
                    difficulty=recipe.difficulty
                ))

        # 按匹配食材数量排序
        results.sort(key=lambda x: len(x.matched_ingredients), reverse=True)
        return results

    # 菜名同义词：不同叫法指同一食材。AI 选菜时可能把"番茄炒蛋"改写成
    # "西红柿炒蛋"，逐字比对会搜不到，归一后再匹配
    NAME_SYNONYMS = (("番茄", "西红柿"),)

    @classmethod
    def _canonical_name(cls, name: str) -> str:
        name = (name or "").strip().lower()
        for src, dst in cls.NAME_SYNONYMS:
            name = name.replace(src, dst)
        return name

    def search_by_name(self, query: str, limit: int = 8) -> List[Recipe]:
        """
        通过菜名模糊搜索菜谱

        匹配顺序：
        1. 同义词归一后的双向子串匹配（番茄=西红柿）
        2. 模糊兜底：允许个别字的差异（如"西红柿炒蛋" vs "西红柿炒鸡蛋"）

        Args:
            query: 用户输入的菜名关键词
            limit: 最多返回数量

        Returns:
            匹配的菜谱列表
        """
        self._load_all_recipes()
        canon_query = self._canonical_name(query)
        if not canon_query:
            return []

        results: List[Recipe] = []
        for recipe in self._recipes_cache.values():
            canon_name = self._canonical_name(recipe.name)
            if canon_query in canon_name or canon_name in canon_query:
                results.append(recipe)

        if not results:
            canon_name_map = {
                self._canonical_name(r.name): r for r in self._recipes_cache.values()
            }
            for close in difflib.get_close_matches(
                canon_query, list(canon_name_map.keys()), n=limit, cutoff=0.8
            ):
                results.append(canon_name_map[close])

        results.sort(key=lambda recipe: (
            0 if self._canonical_name(recipe.name) == canon_query else 1,
            len(recipe.name),
        ))
        return results[:limit]

    def get_recipe(self, name: str) -> Optional[Recipe]:
        """
        获取菜谱详情

        Args:
            name: 菜谱名称

        Returns:
            菜谱详情，未找到返回 None
        """
        self._load_all_recipes()
        return self._recipes_cache.get(name)

    def get_all_recipes(self) -> List[Recipe]:
        """
        获取所有菜谱

        Returns:
            所有菜谱列表
        """
        self._load_all_recipes()
        return list(self._recipes_cache.values())

    def get_categories(self) -> List[str]:
        """
        获取所有菜谱分类

        Returns:
            分类列表
        """
        self._load_all_recipes()
        categories = set()
        for recipe in self._recipes_cache.values():
            if recipe.category:
                categories.add(recipe.category)
        return sorted(list(categories))

    def get_recipes_by_category(self, category: str) -> List[Recipe]:
        """
        获取指定分类的菜谱

        Args:
            category: 分类名称

        Returns:
            该分类下的菜谱列表
        """
        self._load_all_recipes()
        return [
            recipe for recipe in self._recipes_cache.values()
            if recipe.category == category
        ]


# 全局单例实例
_recipe_service: Optional[RecipeService] = None


def get_recipe_service() -> RecipeService:
    """
    获取菜谱服务单例

    Returns:
        RecipeService 实例
    """
    global _recipe_service
    if _recipe_service is None:
        _recipe_service = RecipeService()
    return _recipe_service
