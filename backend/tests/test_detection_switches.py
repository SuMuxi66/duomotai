# -*- coding: utf-8 -*-
"""
检测开关（危险报警 / 油烟检测）门控单元测试
覆盖：两项开关全关 → 整帧跳过 VLM 分析；流式/缓存路径按各自开关门控危险与油烟检测
"""
import asyncio
import base64
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.ws import vision_handler


# ---------- 假件 ----------

NORMAL_JSON = '{"has_danger": false, "danger_types": [], "description": "正常烹饪中", "severity": "low"}'
DANGER_JSON = '{"has_danger": true, "danger_types": ["烟雾"], "description": "锅里有烟雾", "severity": "high"}'
OIL_JSON = '{"oil_smoke_level": "很高", "confidence": 0.9, "description": "油烟很浓"}'


def make_state(danger: bool, oil: bool, cached_analysis=None):
    st = SimpleNamespace(
        vision_enabled=True,
        interrupted=False,
        danger_alert_enabled=danger,
        oil_smoke_alert_enabled=oil,
        video_frame_queue=asyncio.Queue(),
        video_frame_processing=False,
        last_danger_alert_time=0.0,
        last_smoke_alert_time=0.0,
        last_pot_alert_time=0.0,
        oil_smoke_pending_response=False,
        last_oil_smoke_alert_time=0.0,
        oil_smoke_timeout_timer=None,
        observations=[],
        cached=[],
    )
    st.get_frame_hash = lambda img: "h1"
    st.get_cached_analysis = lambda h: cached_analysis
    st.should_analyze_frame = lambda h: True
    st.add_observation = lambda text: st.observations.append(text)
    st.cache_analysis_result = lambda h, a: st.cached.append((h, a))
    return st


class FakeVLM:
    """流式/非流式接口都实现（真实 VLM 命中流式优先分支），记录调用次数"""

    def __init__(self, analysis: str):
        self.analysis = analysis
        self.stream_calls = 0
        self.image_calls = 0
        self.last_prompt: str | None = None

    async def analyze_image_stream(self, image_bytes, prompt, enable_thinking=False):
        self.stream_calls += 1
        self.last_prompt = prompt
        yield self.analysis

    async def analyze_image(self, image_bytes, prompt, enable_thinking=False):
        self.image_calls += 1
        self.last_prompt = prompt
        return self.analysis


class FakeWS:
    pass


@pytest.fixture
def sent_messages(monkeypatch):
    msgs = []

    async def _fake_ws_send(ws, typ, payload):
        msgs.append(typ)

    monkeypatch.setattr(vision_handler, "ws_send", _fake_ws_send)
    return msgs


@pytest.fixture
def no_tts(monkeypatch):
    async def _fake_tts(*a, **k):
        pass

    monkeypatch.setattr(vision_handler, "generate_tts", _fake_tts)


async def _feed_one_frame_and_run(state, ws, runtime):
    """投一帧并让队列处理器跑起来，处理完一帧后取消任务退出"""
    state.video_frame_queue.put_nowait({
        "runtime": runtime,
        "ws": ws,
        "jpeg_b64": base64.b64encode(b"fake-jpeg").decode(),
        "frame_id": 1,
        "timestamp": 1,
    })
    task = asyncio.create_task(vision_handler.process_video_frame_queue(state, ws))
    await asyncio.sleep(0.6)  # 覆盖一帧处理 + 一个 0.5s 队列空转周期
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    if state.oil_smoke_timeout_timer:
        state.oil_smoke_timeout_timer.cancel()


# ---------- 两项开关全关：整帧跳过 ----------

class TestBothSwitchesOff:
    async def test_skip_vlm_analysis_entirely(self, sent_messages, no_tts):
        """危险报警与油烟检测都关 → 不调 VLM、不缓存、不发任何消息（省 API 调用）"""
        state = make_state(danger=False, oil=False)
        vlm = FakeVLM(NORMAL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 0
        assert vlm.image_calls == 0
        assert state.observations == []
        assert state.cached == []
        assert sent_messages == []


# ---------- 单项开关门控 ----------

class TestPerSwitchGate:
    async def test_danger_off_blocks_danger_alert_on_stream_path(self, sent_messages, no_tts):
        """危险报警关、油烟检测开 → 分析照跑，但即使画面有危险也不发 danger_detected"""
        state = make_state(danger=False, oil=True)
        vlm = FakeVLM(DANGER_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 1
        assert "danger_detected" not in sent_messages

    async def test_oil_on_triggers_oil_check_on_stream_path(self, sent_messages, no_tts):
        """油烟检测开 → 流式分析结果也要送油烟检测（此前流式路径漏接）"""
        state = make_state(danger=False, oil=True)
        vlm = FakeVLM(OIL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 1
        # fumes 分支：油烟检测改为纯语音询问（assistant_delta），不再发 oil_smoke_detected 弹窗事件
        assert "oil_smoke_detected" not in sent_messages
        assert "assistant_delta" in sent_messages
        assert "assistant_final" in sent_messages

    async def test_cached_frame_honors_oil_gate(self, sent_messages, no_tts):
        """缓存命中帧同样按油烟开关门控（此前缓存路径从不接油烟检测）"""
        state = make_state(danger=False, oil=True, cached_analysis=OIL_JSON)
        vlm = FakeVLM(NORMAL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 0  # 命中缓存不再调 VLM
        assert "oil_smoke_detected" not in sent_messages
        assert "assistant_delta" in sent_messages
        assert "assistant_final" in sent_messages

    async def test_cached_frame_no_alerts_when_both_off(self, sent_messages, no_tts):
        """缓存命中帧 + 两项全关 → 既无危险警报也无油烟询问"""
        state = make_state(danger=False, oil=False, cached_analysis=DANGER_JSON)
        vlm = FakeVLM(NORMAL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert "danger_detected" not in sent_messages
        assert "oil_smoke_detected" not in sent_messages


# ---------- 开关=行为开关：分析范围随开关收窄（2026-09-06） ----------

class TestPromptScopeBySwitch:
    async def test_oil_only_uses_oil_prompt(self, sent_messages, no_tts):
        """仅油烟开启 → 用油烟专用提示词（JSON 含 oil_smoke_level），危险检测不参与"""
        state = make_state(danger=False, oil=True)
        vlm = FakeVLM(OIL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 1
        assert vlm.last_prompt == vision_handler.OIL_SMOKE_ANALYSIS_PROMPT

    async def test_danger_on_uses_unified_prompt(self, sent_messages, no_tts):
        """危险报警开启（油烟关）→ 统一危险提示词，且不发油烟询问"""
        state = make_state(danger=True, oil=False)
        vlm = FakeVLM(NORMAL_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert vlm.stream_calls == 1
        assert vlm.last_prompt == vision_handler.UNIFIED_DANGER_PROMPT
        assert "assistant_delta" not in sent_messages


# ---------- 烟雾→排气扇联动（2026-09-20）：排气扇已开不做弹窗检测 ----------

class TestSmokeSuppressedWhenFanOn:
    async def test_fan_on_suppresses_smoke_alert(self, sent_messages, no_tts, monkeypatch):
        """纯烟雾 + 排气扇已开 → 不发 danger_detected，也不发风扇询问"""
        import app.fan as fan_mod
        monkeypatch.setattr(fan_mod, "fan_status", lambda: {"success": True, "is_on": True})
        state = make_state(danger=True, oil=False)
        vlm = FakeVLM(DANGER_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert "danger_detected" not in sent_messages
        assert "assistant_final" not in sent_messages

    async def test_fan_off_still_alerts(self, sent_messages, no_tts, monkeypatch):
        """纯烟雾 + 排气扇关 → 照常弹窗并语音询问是否开风扇"""
        import app.fan as fan_mod
        monkeypatch.setattr(fan_mod, "fan_status", lambda: {"success": True, "is_on": False})
        state = make_state(danger=True, oil=False)
        vlm = FakeVLM(DANGER_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert "danger_detected" in sent_messages
        assert "assistant_final" in sent_messages

    async def test_fan_query_failure_still_alerts(self, sent_messages, no_tts, monkeypatch):
        """排气扇状态查询失败 → 按未开启处理，照常弹窗（宁可误报不可漏报）"""
        import app.fan as fan_mod

        def _boom():
            raise RuntimeError("miio down")

        monkeypatch.setattr(fan_mod, "fan_status", _boom)
        state = make_state(danger=True, oil=False)
        vlm = FakeVLM(DANGER_JSON)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert "danger_detected" in sent_messages

    async def test_flame_still_alerts_even_fan_on(self, sent_messages, no_tts, monkeypatch):
        """烟雾+明火混合 → 明火不受风扇联动影响，照常弹窗"""
        import app.fan as fan_mod
        monkeypatch.setattr(fan_mod, "fan_status", lambda: {"success": True, "is_on": True})
        flame_json = '{"has_danger": true, "danger_types": ["烟雾", "明火"], "description": "起火并冒烟", "severity": "high"}'
        state = make_state(danger=True, oil=False)
        vlm = FakeVLM(flame_json)
        await _feed_one_frame_and_run(state, FakeWS(), SimpleNamespace(vlm=vlm))

        assert "danger_detected" in sent_messages
