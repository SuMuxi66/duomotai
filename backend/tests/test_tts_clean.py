# -*- coding: utf-8 -*-
"""
TTS 文本清洗回归（2026-09-05）：
GLM 输出紧凑标题"##原因"（井号后无空格）与行中"##"（换行被下游拼接）曾被读出
"井号井号…"——清洗规则须不限行首、不要求井号后有空格。
"""
from app.ws.tts_handler import clean_text_for_tts


def test_compact_heading_stripped():
    """GLM 紧凑写法：井号后无空格。"""
    assert "#" not in clean_text_for_tts("##原因：自动下料设备通常只支持单向投放。")
    assert clean_text_for_tts("##原因：自动下料设备") == "原因：自动下料设备"


def test_compact_heading_with_bullet_glued():
    """标题与列表粘在同一行的实测案例。"""
    out = clean_text_for_tts("###新手避坑关键提示- 鸡蛋要嫩：蛋液入锅后不要频繁翻动。")
    assert "#" not in out
    assert "新手避坑关键提示" in out
    assert "鸡蛋要嫩" in out


def test_mid_line_heading_stripped():
    """换行被拼接后"##"出现在行中的实测案例。"""
    out = clean_text_for_tts("实测重量：0克（稳定）## 结论尚未添加任何调料。")
    assert "#" not in out
    assert "结论尚未添加" in out


def test_standard_heading_still_stripped():
    """标准写法（井号+空格+行首）回归。"""
    assert clean_text_for_tts("## 标准标题\n内容") == "标准标题\n内容"
    assert clean_text_for_tts("### 三级标题内容") == "三级标题内容"


def test_bold_and_stray_asterisks_stripped():
    """成对加粗去星号；跨缓冲区未成对的残留星号兜底清掉。"""
    assert clean_text_for_tts("请手动添加 **3克** 盐") == "请手动添加 3克 盐"
    out = clean_text_for_tts("建议放*3克盐")  # 未成对的单星号
    assert "*" not in out
    assert "3克盐" in out


def test_existing_rules_preserved():
    """既有规则回归：数字区间连字符读"到"、代码块移除。"""
    assert "150到180度" in clean_text_for_tts("油温 150-180度")
    assert "`" not in clean_text_for_tts("结果 `code` 结束")
    assert "code" not in clean_text_for_tts("结果 ```py\ncode\n``` 结束")


def test_glued_bullet_and_number_read_naturally():
    """GLM 紧凑输出的行中列表横杠与序号（实测"视觉分析- 核心主体""处理建议1. 手动补救"）。"""
    out = clean_text_for_tts("##视觉分析- 核心主体：一名男子坐在桌前")
    assert "#" not in out and "-" not in out
    assert "视觉分析" in out and "核心主体" in out
    out2 = clean_text_for_tts("##处理建议1. 手动补救：若食材掉错位置")
    assert "#" not in out2
    assert "处理建议" in out2 and "手动补救" in out2
    assert "1." not in out2


def test_digit_ranges_and_decimals_not_broken():
    """误伤边界：数字区间、小数、无空格连字符不受新规则影响。"""
    assert "3到5" in clean_text_for_tts("放 3-5 克盐")
    assert "1.5克" in clean_text_for_tts("放盐 1.5克")
    assert clean_text_for_tts("番茄-鸡蛋") == "番茄-鸡蛋"  # 横杠后无空格不处理


def test_plain_text_unchanged():
    """纯中文/数字文本不被误伤。"""
    assert clean_text_for_tts("已计算完成，请把锅铲放置到调料出口") == "已计算完成，请把锅铲放置到调料出口"
    assert clean_text_for_tts("好的，开始自动下料 3 克。") == "好的，开始自动下料 3 克。"
