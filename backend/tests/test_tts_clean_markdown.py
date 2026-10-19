# -*- coding: utf-8 -*-
"""TTS 清洗·markdown 残留回归（2026-09-09 温度播报实测翻车用例）。

背景：模型输出 markdown 列表"- **目标温度：26°C**"，部分轮次为紧凑写法
"：-目标温度：26°C"（横杠后无空格、无换行），列表横杠被 TTS 读成"杠"。
"""
import app.tts_clean as ttsc
from app.tts_clean import clean_text_for_tts as clean


def test_temperature_markdown_block_spaced_bullets():
    """标准 markdown 列表（横杠后有空格）：横杠/星号/°C 全部不进 TTS。"""
    raw = (
        "当前实测温度：\n\n"
        "- **目标温度：26°C**\n"
        "- **平均温度：26°C**\n"
        "- **最高温度：27°C**\n"
        "- **最低温度：24°C**\n"
        "- **传感器温度：31°C**\n\n"
        "室内整体约 26°C，属于舒适常温区间。"
    )
    out = clean(raw)
    assert "*" not in out
    assert "°" not in out
    assert "摄氏度" in out
    assert "目标温度：26摄氏度" in out
    assert "属于舒适常温区间" in out


def test_compact_dash_after_punctuation():
    """紧凑写法"：-目标温度：26°C-平均温度"（2026-09-09 15:21 实测被读成"杠"）。"""
    out = clean("当前实测温度：-目标温度：26°C-平均温度：26°C-最高温度：27°C")
    assert "-" not in out
    assert "*" not in out
    assert "°" not in out
    assert "目标温度" in out and "平均温度" in out and "最高温度" in out


def test_compact_dash_line_start():
    """小米 TTS 实测分段"-温度实测约26°C"——行首横杠紧贴 CJK 必须剥掉。"""
    out = clean("实测如下\n-温度实测约26°C，属于常温")
    assert "-温度" not in out
    assert "温度实测约26摄氏度" in out


def test_slash_between_cjk_becomes_pause():
    """小米 TTS 实测"并非灶台/锅面温度"被读出"斜杠"。"""
    out = clean("并非灶台/锅面温度")
    assert "/" not in out
    assert "灶台、锅面" in out


def test_negative_number_bullet_guard():
    """行首横杠紧跟数字是负数，不当作列表符号剥掉。"""
    out = clean("-26°C以下算低温")
    assert "-26摄氏度" in out


def test_decimal_number_guard():
    """序号规则不能吃掉小数（"26.5克"）。"""
    assert "26.5克" in clean("重26.5克")
    assert "1.5克" in clean("放盐 1.5克")


def test_digit_range_reads_dao():
    assert "150到180度" in clean("油温保持150-180度")


def test_code_fence_link_url():
    out = clean("看这个```json\n{}\n```说明[文档](http://x.com/a)与 http://y.com 谢谢")
    assert "```" not in out
    assert "http" not in out
    assert "说明" in out and "谢谢" in out


def test_word_inner_dash_preserved():
    """词条内部连接横杠（"番茄-鸡蛋"）是 2026-09-05 锁定的保留约束。"""
    assert clean("番茄-鸡蛋") == "番茄-鸡蛋"


def test_single_source_of_truth():
    """三处历史副本必须同源，防止规则再次漂移。"""
    import inspect

    from app.providers import dashscope_tts
    from app.ws import tts_handler
    assert "tts_clean" in inspect.getsource(dashscope_tts.clean_text_for_tts)
    sample = "当前实测温度：-目标温度：26°C"
    assert tts_handler.clean_text_for_tts(sample) == clean(sample)
    # dashscope 变体在权威规则之上保留"换行→空格"的历史行为
    assert "\n" not in dashscope_tts.clean_text_for_tts("a\n\nb")
    assert dashscope_tts.clean_text_for_tts(sample) == clean(sample).replace("\n", " ")
