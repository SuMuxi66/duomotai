"""ttsfrd_compat.py — 开源 ttsfrd 替代前端（桩）。

阿里 ttsfrd 是闭源 x86 引擎，aarch64 不可用。本模块以相同接口
（TtsFrontendEngine.initialize / set_lang_type / gen_tacotron_symbols）
用 jieba + pypinyin + KAN-TTS 自带的 py2phoneMap.txt 实现
中文文本 -> MIT 符号序列，供 modelscope SambertHifigan 推理。

符号格式（lfeat_type_list = sy,tone,syllable_flag,word_segment,emo_category,speaker_category）:
    {sy$toneN$sflag$wflag$EMO$SPK} ... {pau$...} ~
序列末尾必须带 "~"（推理时 inputs_len = len-1 会去掉它）。
"""
import os
import re

import jieba
import pypinyin

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py2phoneMap.txt")

try:
    import cn2an

    def _num2cn(s):
        try:
            return cn2an.transform(s, "an2cn")
        except Exception:
            return s
except Exception:
    _NUM_CN = "零一二三四五六七八九"

    def _num2cn(s):
        return "".join(_NUM_CN[int(ch)] if ch.isdigit() else ch for ch in s)


def _load_map():
    m = {}
    with open(_MAP_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) == 2:
                m[parts[0]] = parts[1].split()
    return m


_PINYIN_MAP = _load_map()
_HAN = re.compile(r"[\u4e00-\u9fff]")


def _norm_text(text):
    """数字转中文。"""
    return re.sub(r"\d+(\.\d+)?", lambda m: _num2cn(m.group(0)), text)


def _syllable_phones(base):
    """拼音（无调） -> 音素列表；未收录的音节用 xx_c 兜底。"""
    return _PINYIN_MAP.get(base, ["xx_c"])


def _char_groups(char, syl_pos, emo, spk):
    """单个汉字 -> 符号组列表；非汉字返回 []。"""
    if not _HAN.match(char):
        return []
    py = pypinyin.pinyin(char, style=pypinyin.Style.TONE3, errors="ignore")
    if not py or not py[0]:
        return []
    m = re.match(r"([a-züv]+)(\d)?$", py[0][0])
    if not m:
        return []
    base = m.group(1).replace("ü", "v").replace(":", "")
    tone_n = int(m.group(2)) if m.group(2) else 5
    tone_sym = "tone%d" % tone_n if 1 <= tone_n <= 4 else "tone5"
    phones = _syllable_phones(base)
    n = len(phones)
    groups = []
    for i, ph in enumerate(phones):
        sflag = ("s_both" if n == 1 else
                 "s_begin" if i == 0 else
                 "s_end" if i == n - 1 else "s_middle")
        if n == 1:
            wflag = "word_" + syl_pos
        else:
            pos = ("begin" if i == 0 else
                   "end" if i == n - 1 else "middle")
            if syl_pos == "begin":
                wflag = "word_begin" if pos != "end" else "word_both"
            elif syl_pos == "end":
                wflag = "word_end" if pos != "begin" else "word_both"
            elif syl_pos == "both":
                wflag = "word_both"
            else:
                wflag = "word_" + pos
        groups.append("{%s$%s$%s$%s$%s$%s}" % (ph, tone_sym, sflag, wflag, emo, spk))
    return groups


_PAUSE = "{pau$tone_none$s_none$word_none$%s$%s}"
_EOS = "~"
_SENT_SPLIT = re.compile(r"[。！？；!?;]+")
_PHRASE_SPLIT = re.compile(r"[，,、：:]+")
_TRIM = " ，,、：:—…·-（）()《》“”\"'"


def text_to_symbol_lines(text, emo="emotion_neutral", spk="F7"):
    """整句文本 -> ["0\tsymbols", ...]，每个逗号分句一行。"""
    text = _norm_text(text.strip())
    lines = []
    for seg in _SENT_SPLIT.split(text):
        seg = seg.strip(_TRIM)
        if not seg:
            continue
        for piece in _PHRASE_SPLIT.split(seg):
            piece = piece.strip(_TRIM)
            if not piece:
                continue
            words = [w for w in jieba.cut(piece) if w.strip()]
            groups = []
            for wi, word in enumerate(words):
                han = [c for c in word if _HAN.match(c)]
                if not han:
                    continue
                n = len(han)
                for ci, ch in enumerate(han):
                    syl_pos = ("both" if n == 1 else
                               "begin" if ci == 0 else
                               "end" if ci == n - 1 else "middle")
                    groups.extend(_char_groups(ch, syl_pos, emo, spk))
            if not groups:
                continue
            groups.append(_PAUSE % (emo, spk))
            lines.append("%d\t%s" % (len(lines), " ".join(groups)))
    return lines


# 运行时可由服务端覆盖的当前情绪/说话人（模块级，engine 实例读取）
emo = "emotion_neutral"
spk = "F7"


class TtsFrontendEngine:
    """与阿里 ttsfrd 兼容的最小接口。"""

    def __init__(self):
        self.lang = "zh-cn"

    def initialize(self, resources_dir):
        return True

    def set_lang_type(self, lang_type):
        self.lang = lang_type
        return True

    def gen_tacotron_symbols(self, text):
        return "\n".join(text_to_symbol_lines(text, emo=emo, spk=spk))
