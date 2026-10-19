"""
STT 文本修正器：清洗端侧/云端 ASR 吐出的原始识别文本，得到干净、可直接进入
业务关键词匹配（倒计时/油温/烟雾确认/做饭指令）的文本。

设计原则：
- 保守修正：只做确定性清洗（空白、标点、全角/半角、重复语气词），不做语义改写；
  避免破坏“好/开/开启/要/嗯”等单字确认词（烟雾/油烟口语确认拦截依赖它们）。
- 返回 str：空输入/纯噪声一律返回空字符串，调用方用 `or "（未识别到有效语音）"` 兜底
  （见 audio_handler/glasses_audio_handler）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# 首尾可剥离的标点：中文句末/顿读噪声 + 英文标点
_TRIM_PUNCT = "。！？!?，,、；;：:．.…～~·•〈〉《》【】[]()（）\"'\"'“”‘’<>-—_/\\|"

_EMPTYISH = re.compile(r"^[\s\u3000\u00a0。！？!?，,、；;：:．.\"'≥≤．…～~·•@#￥$%^&*()（）\[\]{}<>《》【】_\-—/\\|，。、；：！”“‘’]*$")

# 连续语气词（嗯/呃/啊/哦/唉/咿/唔/呐 等）整体视为无意义 -> 需要考虑：单字"嗯/好"是确认词保留
# 这里仅当结果全由语气词构成时清空（"嗯嗯嗯"、"呃呃"），不处理"嗯"单个确认词。
_ONLY_FILLER = re.compile(r"^[嗯哪哦呃哎唉啊哈咿唔欸]*$")
# 常见唤醒/唤醒词残留尾巴：如"小爱小爱"、"你好你好"、"唤醒唤醒"（可扩展，按本地习惯）
_WAKEWORD_LEFT_TAIL = ("小爱小爱", "你好你好", "唤醒唤醒", "小度小度", "天猫精灵")

# 语音转写常见的重复字/叠词残留：连续重复 3 次以上的单字（嗯嗯嗯/哈哈哈哈）
_REPEATED = re.compile(r"(.)\1{3,}")


def _normalize_width(text: str) -> str:
    """全角 → 半角（字母数字标点），全角空格 → 普通空格。"""
    out = []
    for ch in text:
        if ch == "\u3000":
            out.append(" ")
            continue
        if ch in "，。！？、；：“”‘’（）【】《》":
            out.append(ch)
            continue
        if ch.isalpha() or ch.isdigit() or unicodedata.category(ch) != "Cc":
            normalized = unicodedata.normalize("NFKC", ch)
            # NFKC 会把中文保持原样、把全角字母/数字/标点折叠为半角
            if len(normalized) == 1:
                out.append(normalized)
                continue
        out.append(ch)
    return "".join(out)


def correct_stt_text(raw: Any) -> str:
    """清洗 ASR 原始文本；无效输入返回空字符串。"""
    if raw is None:
        return ""
    text = str(raw)
    # 1. 控制字符/零宽字符剥离
    text = "".join(
        ch for ch in text
        if ord(ch) >= 32 and ch not in "\u200b\u200c\u200d\ufeff"
    )
    # 2. 全角 → 半角、全角空格 → 空格
    text = _normalize_width(text.strip())
    # 3. 合并连续空白
    text = re.sub(r"\s+", " ", text).strip()
    # 4. 剥除首尾标点（ASR 常把句号/省略号拼在头尾）
    text = text.strip(_TRIM_PUNCT).strip()
    if not text:
        return ""
    # 5. 纯标点/纯语气词 → 空
    if _EMPTYISH.match(text) or _ONLY_FILLER.match(text):
        return ""
    # 6. 唤醒词残留尾巴（连续说两遍同一唤醒词）
    lowered = text.lower()
    for tail in _WAKEWORD_LEFT_TAIL:
        if lowered == tail:
            return ""
    # 7. 叠字噪音（哈哈哈/嗯嗯嗯嗯）折叠为单字，避免污染关键词匹配
    text = _REPEATED.sub(lambda m: m.group(1) * 2, text)
    return text.strip()