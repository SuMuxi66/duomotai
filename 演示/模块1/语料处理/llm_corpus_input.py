# -*- coding: utf-8 -*-
"""
llm_corpus_input.py — 大模型语料清洗（文件直读版 · 全流程上半段）
==================================================================

直接把待清洗的菜谱文件送给大模型清洗，自动完成上半段流程：
读取文档 → 大模型语义清洗 → 质量校验 → 保存清理版

清洗结果保存为同目录 菜谱库/清蒸鲈鱼清理版.md，
随后运行 llm_corpus_deploy.py 完成下半段（Recipe 结构化封装 + 归档 + 知识库部署）。

用法：
    python llm_corpus_input.py

依赖：httpx、python-dotenv；DASHSCOPE_API_KEY 自动从 backend/.env 读取
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")


# ==================== ① 注入给大模型的"项目清洗规则" ====================
# 清洗标准定义成规则注入 System Prompt，让模型按项目标准执行语义级清洗：
# CLEANING_RULES（去噪）+ COMPLETION_RULES（补全）+ STANDARD_FORMAT（标准格式模板）

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


# ==================== ② 大模型调用（OpenAI 兼容模式） ====================

PROVIDER_BASE_URL: dict = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "modelscope": "https://api-inference.modelscope.cn/v1",
}
PROVIDER_KEY_ENV: dict = {
    "dashscope": "DASHSCOPE_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "modelscope": "MODELSCOPE_API_KEY",
}
PROVIDER_DEFAULT_MODEL: dict = {
    "dashscope": "qwen3.5-plus",
    "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
    "modelscope": "Qwen/Qwen3.5-27B",
}
DEFAULT_PROVIDER = "modelscope"   # dashscope 账户欠费（Arrearage），演示期切到 ModelScope（key 在 模块1/.env）


def _load_env_files() -> None:
    """加载 .env 配置：从本目录向上逐级搜索 backend/.env(.local) 与同目录 .env(.local)，
    只采纳非空值（跳过空占位符），真值先到先得——本文件独立运行也能拿到 key"""
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
            if v and not os.getenv(k):
                os.environ[k] = v


def build_system_prompt() -> str:
    """把项目清洗规则 + 补全规则 + 标准格式组装为 System Prompt"""
    return CLEANING_RULES + "\n" + COMPLETION_RULES + "\n" + STANDARD_FORMAT


def clean_with_llm(raw_text: str, provider: str = DEFAULT_PROVIDER,
                   model: Optional[str] = None, timeout: float = 120.0) -> str:
    """调用大模型把口语化语料清洗为标准菜谱 md（OpenAI 兼容 chat/completions）"""
    import httpx

    _load_env_files()
    provider = provider if provider in PROVIDER_BASE_URL else DEFAULT_PROVIDER
    if model is None:
        # dashscope 优先读 DASHSCOPE_LLM_MODEL 环境变量，与后端主程序模型保持一致
        model = (os.getenv("DASHSCOPE_LLM_MODEL", "").strip()
                 if provider == "dashscope" else "") or PROVIDER_DEFAULT_MODEL[provider]
    api_key = os.getenv(PROVIDER_KEY_ENV[provider], "").strip()
    if not api_key:
        raise RuntimeError(
            f"未配置 {PROVIDER_KEY_ENV[provider]}：请在 backend/.env 中添加后重试"
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
    if resp.status_code >= 400:   # 报错时带上响应体，一眼看出 400 的具体原因（如模型名错/key 失效/参数超限）
        raise RuntimeError(
            f"大模型接口返回 {resp.status_code}：{resp.text[:300]}\n"
            f"[诊断] provider={provider} model={model} key前缀={api_key[:6]}****"
        )
    content = resp.json()["choices"][0]["message"]["content"]
    return strip_fences(content)


# ==================== ③ 质量门禁（大模型的输出不直接信任） ====================

def strip_fences(md: str) -> str:
    """剥掉大模型偶尔包上的 ```markdown 围栏与首尾空白"""
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", md, re.DOTALL)
    return (m.group(1) if m else md).strip()


def validate_standard_md(md: str) -> list:
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


# ==================== ④ 文件直读 + 清洗编排（全流程上半段） ====================

# 直接传送的待清洗文件：演示/模块1/菜谱库/清蒸鲈鱼 - 副本.md
RAW_INPUT_FILE = Path(__file__).resolve().parent / "菜谱库" / "清蒸鲈鱼 - 副本.md"
# 清洗结果输出文件：演示/模块1/菜谱库/清蒸鲈鱼清理版.md（供 llm_corpus_deploy.py 下半段使用）
CLEANED_OUTPUT_FILE = Path(__file__).resolve().parent / "菜谱库" / "清蒸鲈鱼清理版.md"


def read_from_file() -> str:
    """步骤⓪ 读取文档：把待清洗文件整篇读出，直接传送给大模型"""
    if RAW_INPUT_FILE.exists():                        # 文件存在才读，避免直接读取时抛 FileNotFoundError
        text = RAW_INPUT_FILE.read_text(encoding="utf-8").strip()  # 按 UTF-8 整篇读出，去掉首尾空白
        if text:                                       # 内容非空才算有效
            return text                                # 读取成功 → 返回整篇原文
    raise FileNotFoundError(
        f"待清洗文件不存在或为空：{RAW_INPUT_FILE}\n"
        f"请先把社交平台脏语料保存到该文件后重试"
    )


def main() -> None:
    # 步骤⓪ 读取文档：直接读取待清洗文件，整篇传送给大模型
    raw = read_from_file()
    print(f"[⓪ 读取文档] {RAW_INPUT_FILE.name}，共 {len(raw)} 字符")

    model = PROVIDER_DEFAULT_MODEL[DEFAULT_PROVIDER]
    print(f"[① 语料清洗] {DEFAULT_PROVIDER}/{model} 清洗中……")
    md = clean_with_llm(raw)

    problems = validate_standard_md(md)
    if problems:
        print(f"[② 质量校验] ⚠️ {problems}")

    # 步骤③ 保存清理版：清洗结果存为 清蒸鲈鱼清理版.md，交给 llm_corpus_deploy.py 完成下半段
    CLEANED_OUTPUT_FILE.write_text(md, encoding="utf-8")
    print(f"\n✅ {RAW_INPUT_FILE.name} 清洗完成")
    print(f"清洗后的数据已保存至：{CLEANED_OUTPUT_FILE}")


if __name__ == "__main__":
    main()
