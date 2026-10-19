"""
ws 包入口点
向后兼容地重新导出所有符号，实际实现已拆分到子模块中
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..config import settings
from ..logger import backend_log
from .. import runtime_providers as provider_runtime
from ..providers.xiaomi_tts import XiaomiTTS
from ..providers.tts_piper import DummyTTS
from ..session import SessionState
from ..weight import get_weight
from ..fan import fan_on, fan_off, fan_status
from ..services.recipe_service import get_recipe_service

# 唤醒词：统一由香橙派端侧 KWS（前端直连 ws://<边缘机>:8767/kws）承担，
# 后端本地方案（FunASR / sherpa-onnx）已下线，后端不再加载任何唤醒词模型。
HAS_LOCAL_STT = False

# 统一危险检测相关配置
DANGER_ALERT_COOLDOWN = 30

UNIFIED_DANGER_PROMPT = """你是厨房安全监测员。分析画面内容，判断是否存在以下危险情况：

1. 烟雾/明火（必须区分，判定标准）：
   - 明火：画面中有橙黄色/红色的火舌、火苗、燃烧的火光——有"火"的亮色和形状才算明火
   - 烟雾：画面中有灰白色/灰黑色/白色的烟柱、烟雾团、浓烟——没有橙红色火舌时只能报"烟雾"，禁止把纯烟雾报成明火
   - 高敏感要求：灶台、锅具边缘升起的**少量细烟、烟丝、早期淡烟**也要报，不要等"浓烟滚滚"才判危险；白色烟气同样算烟雾；只报看不清就不确定的，按 has_danger=true、severity=low 上报
   - 屏幕烟雾识别：深色屏幕（平板/手机/显示器/电视）上出现的**白色或浅色烟雾状图案、雾状纹理、烟团图形**，即使面积很小、只占屏幕一角，也必须识别为烟雾并报告——这是消防演练/安全监测场景的模拟信号，不要把它们当成"屏幕污渍"或"反光"忽略
   - 火和烟同时可见时两者都报；屏幕/平板/手机/显示器上播放的火焰或烟雾同样有效
   - 火焰或烟雾即使很小、较远、只占画面一角，也要报告，并在 severity 中如实反映大小
2. 锅盖剧烈跳动：锅盖是否在剧烈晃动、抖动、跳动或即将飞出
3. 白色泡沫溢出：是否有大量白色泡沫溢出、飞溅、涌出或沸腾溢出

判定原则：宁可多报不可漏报——不确定有没有火/烟时，按 has_danger=true 报告并给低 severity；只有确信完全没有火焰、烟雾迹象时才返回 has_danger=false。

请严格按照以下JSON格式返回结果（不要包含其他内容）：
{
  "has_danger": true或false,
  "danger_types": ["烟雾" 或 "明火" 或 "锅盖跳动" 或 "泡沫溢出" 或组合],
  "description": "简要描述画面情况（若有火焰，说明位置和大小）",
  "severity": "high" 或 "medium" 或 "low"
}

如果没有危险，返回：
{"has_danger": false, "danger_types": [], "description": "正常烹饪中", "severity": "low"}
"""

SMOKE_KEYWORDS = ["烟雾", "火情", "火焰", "冒烟", "起火", "着火"]
SMOKE_ALERT_COOLDOWN = 60

OIL_SMOKE_ANALYSIS_PROMPT = """分析画面内容，判断油烟浓度情况：

请评估当前画面的油烟浓度等级：
- 无/低：几乎看不到油烟
- 中：有一定油烟，但不明显
- 高：油烟明显，厨房能见度受影响
- 很高：油烟浓重，需要立即处理

请严格按照以下JSON格式返回结果（不要包含其他内容）：
{
  "oil_smoke_level": "无/低" 或 "中" 或 "高" 或 "很高",
  "confidence": 0-1之间的数字,
  "description": "简要描述油烟情况"
}

如果没有油烟，返回：
{"oil_smoke_level": "无/低", "confidence": 0.95, "description": "无明显油烟"}
"""

OIL_SMOKE_THRESHOLD = "中"  # 油烟浓度达到"中"即触发排气扇询问（原"高"过迟）
OIL_SMOKE_ALERT_COOLDOWN = 120


def clean_text_for_tts(text: str) -> str:
    from .tts_handler import clean_text_for_tts as _clean
    return _clean(text)


def _split_text_for_streaming(text: str) -> list[str]:
    import re
    normalized = text.strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？!?\n])", normalized)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks or [normalized]


def _split_text_for_display(text: str) -> list[str]:
    import re
    if not text.strip():
        return []
    parts = re.split(r"(?<=[。！？!?])(?!\n)", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = current + part
        if len(candidate) <= 120:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = part
    if current:
        chunks.append(current)
    return chunks or [text]


def _should_flush_tts_buffer(buffer: str, next_delta: str | None = None) -> bool:
    """判断是否应该刷新 TTS 缓冲区。

    优先按标点（句号 / 逗号）断句，避免按固定长度在词中硬切导致朗读碎片化。
    """
    if not buffer.strip():
        return False
    # 句末标点（句号/问号/感叹号）立即刷新，停顿自然
    if buffer.endswith(("。", "！", "？", "!", "?", "\n")):
        return True
    if next_delta and next_delta[:1] in {"。", "！", "？", "!", "?", "\n"}:
        return True
    # 逗号处也可刷新，让长句在逗号处自然断句
    if buffer.endswith(("，", ",")):
        return True
    # 仅当累积过长且仍无标点可断时才硬切，阈值提高以减少碎片化
    if len(buffer) >= 80:
        return True
    return False


def create_dashscope_tts(api_key: str, model: str):
    return provider_runtime.create_dashscope_tts(api_key, model)


def now_ms() -> int:
    import time
    return int(time.time() * 1000)


async def send_latency(ws: WebSocket, request_id: str | None, stage: str, extra: dict | None = None) -> None:
    if not request_id:
        return
    payload = {"request_id": request_id, "stage": stage, "at": now_ms()}
    if extra:
        payload.update(extra)
    await ws_send(ws, "latency", payload)


class AppRuntime:
    def __init__(self):
        self.vlm = provider_runtime.build_vlm_runtime()
        # LLM 已统一到 VLM 端点：runtime.llm 作为别名指向同一实例
        self.llm = self.vlm
        self.tts = DummyTTS()
        self.stt = None

    def _ensure_stt(self):
        if self.stt is None:
            self.stt = provider_runtime.build_stt_runtime(settings.stt_provider, None, settings.siliconflow_api_key)

    def _ensure_tts(self):
        self.tts = provider_runtime.build_tts_runtime(settings.tts_provider, settings.tts_model, None)

    def switch_stt(self, provider: str, model: str = None, api_key: str = None):
        self.stt = provider_runtime.build_stt_runtime(provider, model, api_key)

    def switch_tts(self, provider: str, model: str = None, api_key: str = None):
        self.tts = provider_runtime.build_tts_runtime(provider, model, api_key)

    def update_stt(self, provider: str, model: str, api_key: str = None):
        self.stt = provider_runtime.build_stt_runtime(provider, model, api_key)

    def update_tts(self, provider: str, model: str, api_key: str = None):
        self.tts = provider_runtime.build_tts_runtime(provider, model, api_key)


async def ws_send(ws: WebSocket, typ: str, payload: dict):
    """安全发送 WebSocket 消息，检查连接状态并捕获异常"""
    try:
        if ws is None:
            # 附加防御：断线后 state/payload 里残留的 ws 可能被置为 None，
            # 发送前判空跳过（此前会触发 'NoneType' object has no attribute 'client_state' 错误日志）
            backend_log(f"[WS 跳过] ws 为 None（连接已断开或未建立），跳过发送 type={typ} [debug]")
            return
        if ws.client_state != WebSocketState.CONNECTED:
            backend_log(f"[WS 忽略] 连接状态为 {ws.client_state}，无法发送消息 type={typ}")
            return
        if typ in ["assistant_delta", "assistant_final", "stt_final"]:
            backend_log(f"[WS 发送] type={typ}, request_id={payload.get('request_id', 'N/A')}, text_preview={str(payload.get('text', payload.get('delta', '')))[:50]}")
        await ws.send_json({"type": typ, "payload": payload})
    except Exception as e:
        backend_log(f"[WS 错误] 发送消息失败 type={typ}: {e}")


def get_tts_api_key(provider: str, api_key: str | None = None) -> str | None:
    return provider_runtime.resolve_tts_api_key(provider, api_key)


def get_tts_model(provider: str, model: str | None = None) -> str | None:
    return provider_runtime.resolve_tts_model(provider, model)


def get_tts_voice(provider: str) -> str:
    return provider_runtime.resolve_tts_voice(provider)


def get_stt_runtime_key(provider: str, model: str | None, api_key: str | None) -> tuple[str, str | None, str | None]:
    return provider_runtime.resolve_stt_runtime_key(provider, model, api_key)


def apply_stt_update(runtime: AppRuntime, state: SessionState, provider: str, model: str | None, api_key: str | None) -> None:
    provider_runtime.apply_stt_update(runtime, state, provider, model, api_key)


def ensure_stt_runtime(runtime: AppRuntime, state: SessionState, provider: str, model: str | None, api_key: str | None) -> None:
    provider_runtime.ensure_stt_runtime(runtime, state, provider, model, api_key)


def apply_tts_update(runtime: AppRuntime, provider: str, model: str | None, api_key: str | None) -> None:
    provider_runtime.apply_tts_update(runtime, provider, model, api_key)


def set_session_tts(
    state: SessionState,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    voice: str | None = None,
) -> None:
    provider_runtime.set_session_tts(state, provider, model, api_key, voice)


def refresh_model_runtime(runtime: AppRuntime, state: SessionState | None = None) -> None:
    provider_runtime.refresh_model_runtime(runtime, state)


# Re-export cooking_core for backward compatibility
from .. import cooking_core

# Import ws_loop from connection module for backward compatibility
# (imported here to avoid circular imports during module initialization)
def __getattr__(name):
    if name == 'ws_loop':
        from .connection import ws_loop
        return ws_loop
    if name == 'EVENT_HANDLERS':
        from .router import EVENT_HANDLERS
        return EVENT_HANDLERS
    if name == 'handle_unknown_event':
        from .router import handle_unknown_event
        return handle_unknown_event
    if name in ('handle_video_frame', 'check_and_alert_danger', 'check_and_alert_oil_smoke', 'process_video_frame_queue'):
        from .vision_handler import handle_video_frame, check_and_alert_danger, check_and_alert_oil_smoke, process_video_frame_queue
        return locals()[name]
    if name in ('handle_audio_end', 'handle_audio_stream', 'handle_audio_stream_end'):
        from .audio_handler import handle_audio_end, handle_audio_stream, handle_audio_stream_end
        return locals()[name]
    if name == 'handle_interrupt':
        from .audio_handler import handle_interrupt
        return handle_interrupt
    if name == '_start_user_text_task':
        from .llm_handler import _start_user_text_task
        return _start_user_text_task
    if name in ('generate_tts', '_StreamingTTSRelay', 'stream_xiaomi_tts', 'stream_dashscope_tts'):
        from .tts_handler import generate_tts, _StreamingTTSRelay, stream_xiaomi_tts, stream_dashscope_tts
        return locals()[name]
    if name in ('stream_llm_response', 'handle_llm_response', 'handle_user_text', 'handle_scene_response'):
        from .llm_handler import stream_llm_response, handle_llm_response, handle_user_text, handle_scene_response
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
