# -*- coding: utf-8 -*-
"""
llm_corpus_cleaner.py — 大模型语料清洗通道（社交平台口语化菜谱 → 标准菜谱库 md）
=================================================================================

背景：
  项目语料有两类来源：格式规整的文档资料，以及社交平台（抖音/小红书）的口语化
  自由文本。后者夹带点赞关注引流话术、话题标签、emoji、闲聊碎语，且食材用量、
  烹饪时间火候常常缺失——固定规则难以覆盖，需要语义级的理解与清洗。

本模块：
  调用大模型（DashScope qwen，与后端主程序同一套配置）执行语义级清洗：
  ① 清洗标准规则化：去噪规则、补全规则、标准格式模板三段注入 System Prompt
  ② 从口语化语料中剥离无关内容，只保留：菜名 / 食材 / 用量 / 步骤 / 分类 / 附加内容
  ③ 对原文没写清的部分（用量、时间、火候）按家常惯例合理补全
  ④ 按 HowToCook 标准格式输出 md，直接归档进菜谱库，被 RecipeService 检索加载

用法：
  cd backend
  python -m app.llm_corpus_cleaner "小红书文案原文……"
  python -m app.llm_corpus_cleaner --file 文案.txt --out "菜谱库/可乐鸡翅.md"
  python -m app.llm_corpus_cleaner --show-rules        # 查看注入给大模型的清洗规则
依赖：httpx（后端已有）、python-dotenv（后端已有）；DASHSCOPE_API_KEY 从 backend/.env 读取
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ▶【讲解① · 核心设计】大模型清洗的关键不是"会调接口"，而是把清洗标准定义成规则
#   注入 System Prompt，让模型按项目标准执行语义级清洗：
#   CLEANING_RULES（去噪：引流话术/话题标签/emoji/闲聊一律剥离）
#   + COMPLETION_RULES（补全用量火候，已写明的绝不改动，拿不准用"适量"兜底）
#   + STANDARD_FORMAT（HowToCook 标准格式模板），由 build_system_prompt 组装。
# ==================== 注入给大模型的"项目清洗规则" ====================
# 与 corpus.py 正则清洗逻辑一一对齐，让大模型理解"我们的代码是怎么清洗的"，
# 并把同一套规则泛化到正则处理不了的自由文本上。

CLEANING_RULES = """\
【项目语料清洗规则】
1. 去除一切与"这道菜怎么做"无关的内容：
   - 平台引流话术（关注、点赞、收藏、评论区见、私信、链接、@某人）
   - 话题标签（#xxx#、#话题）、emoji 及表情文字、分割线、无关配图说明
   - 博主闲聊、故事铺垫、情感抒发、与烹饪步骤无关的经历叙述
   - 广告、竞品推荐、无关菜品内容
2. 只保留核心有效字段：菜名、食材清单（含用量）、烹饪步骤、菜品分类、附加内容
3. 剔除原文中的错别字、语气词（哈哈、绝绝子、yyds、家人们）与无意义重复
"""

COMPLETION_RULES = """\
【补全规则】（原文没写清的部分按家常惯例补全，已写明的绝不改动）
1. 食材没写用量 → 补成常见家用量（如"盐 适量"、"生抽 10-15ml"、"五花肉 500g"）
2. 步骤没写时间/火候 → 按该菜式的常规做法补全（如"腌制 10 分钟"、"中火翻炒 2 分钟"、"大火收汁"）
3. 补全必须保守合理、符合常识；拿不准的不编造精确数字，用"适量/少许"兜底
"""

STANDARD_FORMAT = """\
【输出格式】严格输出以下 HowToCook 标准 Markdown（与项目菜谱库现有格式完全一致），不要输出任何解释、前言或代码块标记：

# {菜名}的做法

预估烹饪难度：★★☆

## 必备原料和工具

- 食材/工具1
- 食材/工具2

## 计算

每份：

- 食材1 用量（如：生抽 10-15ml）
- 食材2 用量

## 操作

- 步骤1（一句话一个动作，含火候与时间）
- 步骤2

## 附加内容

- （可选，原文有才写）
"""


# ==================== 大模型调用（OpenAI 兼容模式，与后端 provider 体系一致） ====================

# base_url 与密钥键名由 provider 注册表派生（与 main.py / runtime 同一数据源）；
# 语料清洗可在演示目录独立运行，导入失败时退化到本地副本。
try:
    from .providers.registry import PROVIDER_REGISTRY as _PROVIDER_REGISTRY

    PROVIDER_BASE_URL: dict[str, str] = {
        name: spec.base_url for name, spec in _PROVIDER_REGISTRY.items()
    }
    PROVIDER_KEY_ENV: dict[str, str] = {
        name: spec.key_env for name, spec in _PROVIDER_REGISTRY.items()
    }
except ImportError:  # pragma: no cover - 独立脚本场景
    PROVIDER_BASE_URL = {
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "modelscope": "https://api-inference.modelscope.cn/v1",
    }
    PROVIDER_KEY_ENV = {
        "dashscope": "DASHSCOPE_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
    }

# 清洗任务的默认模型是"文本对话模型"语义（区别于各 provider 的 VLM 模型），保留本地定义
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "dashscope": "qwen3.8-27b",
    "siliconflow": "deepseek-v4-flash",
    "modelscope": "Qwen/Qwen3.8-27B",
}
DEFAULT_PROVIDER = "dashscope"


def _load_env_files() -> None:
    """加载 .env 配置；本文件在 backend/app/ 或演示目录均可独立运行：
    向上逐级搜索 backend/.env(.local) 与同目录 .env(.local)，只采纳非空值（跳过空占位符）"""
    import os
    from dotenv import dotenv_values
    here = Path(__file__).resolve().parent
    candidates = [here / ".env", here / ".env.local"]
    for base in [here, *here.parents]:
        candidates += [base / "backend" / ".env", base / "backend" / ".env.local"]
    candidates += [Path.cwd() / ".env", Path.cwd() / ".env.local"]
    for p in candidates:
        if not p.exists():
            continue
        for k, v in dotenv_values(p).items():
            if v and not os.getenv(k):      # 空占位符不采纳，真值先到先得
                os.environ[k] = v


def _get_api_key(provider: str) -> str:
    """优先复用后端 settings，其次读对应 provider 的环境变量"""
    try:
        from .config import settings
        key = getattr(settings, "dashscope_api_key", None)
        if provider == "dashscope" and key:
            return key
    except Exception:
        pass
    import os
    return os.getenv(PROVIDER_KEY_ENV[provider], "")


def build_system_prompt() -> str:
    """把项目清洗规则 + 补全规则 + 标准格式组装为 System Prompt"""
    return CLEANING_RULES + "\n" + COMPLETION_RULES + "\n" + STANDARD_FORMAT


# ▶【讲解② · 调用链】OpenAI 兼容接口调用 DashScope，key 与模型名复用后端 .env 配置，
#   和主程序同一套（qwen3.5-plus），零新增成本。两个实测细节：
#   temperature=0.2 —— 清洗任务要求稳定可复现；enable_thinking=False —— qwen3 系默认
#   开思考模式，非流式请求会被拖到超时，清洗任务无需思考（实测踩过的坑）。
def clean_with_llm(
    raw_text: str,
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> str:
    """调用大模型把口语化语料清洗为标准菜谱 md（OpenAI 兼容 chat/completions）"""
    import httpx

    _load_env_files()
    provider = provider if provider in PROVIDER_BASE_URL else DEFAULT_PROVIDER
    if model is None:
        # dashscope 优先用后端配置的 DASHSCOPE_LLM_MODEL，保持与主程序一致
        import os
        model = (os.getenv("DASHSCOPE_LLM_MODEL", "").strip()
                 if provider == "dashscope" else "") or PROVIDER_DEFAULT_MODEL[provider]
    api_key = (api_key or _get_api_key(provider) or "").strip()  # 去掉 Windows CRLF 残留
    if not api_key:
        raise RuntimeError(
            f"未配置 {PROVIDER_KEY_ENV[provider]}：请在 backend/.env 中添加，"
            f"或设置系统环境变量后重试（也可用 --api-key 临时传入）"
        )

    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": f"请把下面这段社交平台语料清洗为标准菜谱：\n\n{raw_text}"},
        ],
        "temperature": 0.2,   # 清洗任务要稳定，低随机性
    }
    if provider == "dashscope":
        # qwen3 系默认开思考模式，非流式请求会被拖到超时，清洗任务无需思考
        body["enable_thinking"] = False

    resp = httpx.post(
        f"{PROVIDER_BASE_URL[provider]}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return strip_fences(content)


def strip_fences(md: str) -> str:
    """剥掉大模型偶尔包上的 ```markdown 围栏与首尾空白"""
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", md, re.DOTALL)
    return (m.group(1) if m else md).strip()


# ▶【讲解③ · 质量门禁】大模型的输出不直接信任：strip_fences 剥掉偶发的 markdown 围栏，
#   validate_standard_md 做结构校验（"xx的做法"标题 / 食材小节 / 操作小节 / 步骤≥2 条），
#   不合格的输出被拦下不入库——保证产出的每份菜谱都能被 RecipeService 直接解析加载。
# ==================== 结果校验（轻量，保证能被 RecipeService 加载） ====================

def validate_standard_md(md: str) -> list[str]:
    """校验输出是否符合标准格式，返回问题列表（空列表 = 通过）"""
    problems = []
    if not re.search(r"^#\s+.+?的做法\s*$", md, re.MULTILINE):
        problems.append("缺少一级标题「# xxx的做法」")
    if "## 必备原料和工具" not in md:
        problems.append("缺少「## 必备原料和工具」小节")
    if "## 操作" not in md:
        problems.append("缺少「## 操作」小节")
    steps = re.findall(r"^## 操作\s*$\n((?:\s*-.+\n?)+)", md, re.MULTILINE)
    if steps and len(re.findall(r"^- ", steps[0], re.MULTILINE)) < 2:
        problems.append("「## 操作」下步骤少于 2 条，疑似清洗过度")
    return problems


# ▶【讲解④ · 归档入库】校验通过后按菜名自动归档为标准 md 进菜谱库，
#   后续菜名/食材双维度检索直接命中——口语语料就此进入结构化知识库。
def _sync_to_production_library(md_path: Path) -> Path | None:
    """归档后同步一份到生产菜谱库（buzhou/HowToCook/dishes/home_dish/<菜名>/），
    保证 RecipeService（默认加载目录）可直接检索；找不到生产库目录时静默跳过。"""
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


def save_to_library(md: str, out_path: Path | None = None, name_hint: str | None = None) -> Path:
    """归档进菜谱库：未指定路径时按菜名自动命名，并同步一份到生产菜谱库"""
    auto = out_path is None
    if auto:
        m = re.search(r"^#\s+(.+?)的做法", md, re.MULTILINE)
        name = (name_hint or (m.group(1).strip() if m else "未命名菜品"))
        lib_dir = Path(__file__).parent / "data" / "llm菜谱库"
        lib_dir.mkdir(parents=True, exist_ok=True)
        out_path = lib_dir / f"{name}.md"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    if auto:
        synced = _sync_to_production_library(out_path)
        if synced:
            print(f"[同步] 已复制到生产菜谱库：{synced}")
    return out_path


# ==================== CLI ====================

def main() -> None:
    parser = argparse.ArgumentParser(description="大模型语料清洗：社交平台口语化菜谱 → 标准菜谱库 md")
    parser.add_argument("text", nargs="?", help="原始语料文本（也可用 --file 或 stdin 传入）")
    parser.add_argument("--file", help="从文件读取原始语料")
    parser.add_argument("--out", help="清洗结果保存路径（默认按菜名存入 app/data/llm菜谱库/）")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=list(PROVIDER_BASE_URL),
                        help=f"大模型服务商（默认 {DEFAULT_PROVIDER}）")
    parser.add_argument("--model", default=None, help="模型型号（默认用该 provider 的推荐模型）")
    parser.add_argument("--api-key", default=None, help="临时传入 API Key（优先于环境变量）")
    parser.add_argument("--show-rules", action="store_true", help="只打印注入给大模型的清洗规则，不调用")
    parser.add_argument("--no-save", action="store_true", help="只打印清洗结果，不落盘")
    args = parser.parse_args()

    if args.show_rules:
        print(build_system_prompt())
        return

    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        raw = args.text
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        parser.error("请提供语料：位置参数、--file 或 stdin 三选一")

    model = args.model or PROVIDER_DEFAULT_MODEL[args.provider]
    print(f"[语料清洗] 原始文本 {len(raw)} 字符，{args.provider}/{model}，清洗中……")
    md = clean_with_llm(raw, provider=args.provider, model=model, api_key=args.api_key)

    problems = validate_standard_md(md)
    if problems:
        print("[校验] 输出存在格式问题（已打印，不影响保存）：")
        for p in problems:
            print(f"  - {p}")
    else:
        print("[校验] 标准格式校验通过（可被 RecipeService 直接加载）")

    print("\n========== 清洗结果 ==========\n")
    print(md)
    print("\n==============================")
    print(f"[语料清洗] {len(raw)} 字符口语文本 → {len(md)} 字符标准菜谱")

    if not args.no_save:
        path = save_to_library(md, Path(args.out) if args.out else None)
        print(f"[归档] 已保存：{path}")


if __name__ == "__main__":
    main()
