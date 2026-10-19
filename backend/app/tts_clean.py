# -*- coding: utf-8 -*-
"""TTS 播报文本清洗——全项目唯一权威版本（ws/tts_handler、providers/dashscope_tts 共用）。

历史教训驱动，规则不允许假设 markdown 记号后有空格：
- 模型会输出紧凑 markdown："-目标温度：26°C"（横杠后无空格）、"##原因"（井号后无
  空格）——2026-09-09 实测温度播报把列表横杠读成"杠/横杠"；
- 换行会被下游 TTS provider（xiaomi/dashscope）折叠或删除，行首规则必须在清洗
  阶段（换行还在时）完成；
- 路径斜杠（"灶台/锅面"）会被读成"斜杠"；"°C" 各家 TTS 读音不一，统一成"摄氏度"；
- 数字区间连字符会被读成"零下"（"150-180度"→"150零下180度"），改读"到"。
- 误伤约束（2026-09-05 回归）：CJK 夹着的无空格横杠（"番茄-鸡蛋"）与小数
  （"1.5克"）不得被当作 markdown 处理。
"""
from __future__ import annotations

import re

# 行首列表记号后允许紧贴的字符（CJK/字母/引号/括号/星号）；数字除外——保护
# 行首负数与年份（"-26°C""-2024年"不当作列表剥掉横杠）。
_COMPACT_BULLET_AFTER = r"[\u4e00-\u9fa5A-Za-z（(【\[\"'*]"


def clean_text_for_tts(text: str) -> str:
    # 1) 代码块/行内代码/HTML 标签：整块移除
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    # 2) 裸 URL 会被逐字符念出来
    text = re.sub(r"https?://\S+", "", text)
    # 3) 标题井号：不限行首、不要求井号后有空格（紧凑写法"##原因"，换行拼接后出现在行中）
    text = re.sub(r"#{1,6}\s*", "", text)
    # 4) 图片先于链接处理（否则链接规则会吃掉图片的 [alt](url) 留下孤立的 !）
    text = re.sub(r"!\[.*?\]\(.+?\)", "[图片]", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    # 5) 加粗/斜体配对剥离
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # 6) 加粗跨 TTS 缓冲区/未成对时残留的星号兜底（否则被读成"星号"）
    text = re.sub(r"\*+", "", text)
    # 7) 列表横杠/星号/加号：
    #    a) 行首 + 空格（标准写法"- 温度"）
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    #    b) 行首 + 紧贴 CJK/字母/引号（紧凑写法"-目标温度"）
    text = re.sub(
        rf"^([\s]*)[-*+](?={_COMPACT_BULLET_AFTER})", r"\1", text, flags=re.MULTILINE
    )
    # 8) 行首序号："1. 做法"（要求序号点后有空格，保护小数"26.5克"）与紧凑"1、做法"
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+、\s*", "", text, flags=re.MULTILINE)
    # 9) 行内残留的紧凑横杠（2026-09-09 温度播报实测"：-目标温度：26°C-平均温度"）：
    #    a) 标点后直接删除——标点本身已是停顿（"当前实测温度：-目标温度"）。
    #       lookbehind 不含 CJK：CJK 夹横杠由 c) 处理（"番茄-鸡蛋"必须保留）
    text = re.sub(
        r"(?<=[：:，,。、；;）)】>》])[-*+](?=[\u4e00-\u9fa5A-Za-z])",
        "",
        text,
    )
    #    b) 数字/字母/右括号后→逗号（"26°C-平均温度"），有停顿才不像连读
    text = re.sub(r"(?<=[0-9A-Za-z）)])\s*[-*+]\s*(?=[\u4e00-\u9fa5])", "，", text)
    #    c) CJK 夹着且带空格（"视觉分析- 核心主体"）→逗号；不带空格（"番茄-鸡蛋"）
    #       保留不处理（2026-09-05 回归约束，词条内部连接横杠）
    text = re.sub(r"(?<=[\u4e00-\u9fa5])\s*[-*]\s+(?=[\u4e00-\u9fa5])", "，", text)
    #    d) CJK 后的紧凑序号（"处理建议1. 手动补救""建议2、小火"）→逗号；
    #       序号点后必须空格/或为顿号，保护小数"1.5克"
    text = re.sub(r"(?<=[\u4e00-\u9fa5）)])\d+\.\s+", "，", text)
    text = re.sub(r"(?<=[\u4e00-\u9fa5）)])\d+、", "，", text)
    # 10) 单位符号归一：°C/℃→摄氏度、°F→华氏度、孤立°→度
    text = re.sub(r"\s*[°℃]\s*[Cc]", "摄氏度", text)
    text = re.sub(r"\s*[°℃]\s*[Ff]", "华氏度", text)
    text = re.sub(r"[°℃]", "度", text)
    # 11) 引用与水平线
    text = re.sub(r"^[\s]*>\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*[-=]{3,}\s*$", "", text, flags=re.MULTILINE)
    # 12) CJK 间路径斜杠读"斜杠"（"灶台/锅面"）→顿号
    text = re.sub(r"(?<=[\u4e00-\u9fa5])\s*/\s*(?=[\u4e00-\u9fa5])", "、", text)
    # 13) 数字区间连字符读"零下"→"到"
    text = re.sub(r"(\d)\s*[-–—~～]\s*(\d)", r"\1到\2", text)
    # 14) 压缩连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
