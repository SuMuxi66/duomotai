"""端侧视觉模块：调 RK3588 NPU 推理服务做厨房危险检测。

双通道端侧感知：
- 8766/detect_fire: 火焰/烟雾检测 (fire.rknn) -> oil_fire / smoke
- 8765/detect: COCO 通用检测 (yolov5s.rknn) -> knife=knife_danger 等

与 safety_aggregator(多帧聚合降误报) + device_controller(设备处置) 协同，
构成"端侧感知 -> 聚合判定 -> 设备处置"闭环。断网/服务不可用时安全降级，不中断主流程。

新增模块，不改 glasses_cam；接入由 vision_handler/glasses_cam 在后续接线阶段完成。
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .device_controller import DeviceController, get_default_controller
from .hazard_rules import get_action, get_rule, is_critical
from .safety_aggregator import TemporalAggregator, get_default_aggregator

logger = logging.getLogger(__name__)

DEFAULT_DETECT_URL = "http://192.168.1.248:8765/detect"
DEFAULT_FIRE_URL = "http://192.168.1.248:8766/detect_fire"

# COCO 类名 -> hazard_type（基于通用检测结果做厨房危险推断）
# 检测到刀具即提醒（knife_danger = voice_remind）；可按需扩展
COCO_HAZARD_MAP: Dict[str, str] = {
    "knife": "knife_danger",
    "scissors": "knife_danger",
}


class EdgeVLMClient:
    """调 RK3588 端侧推理服务的 HTTP 客户端。

    用标准库 urllib，不依赖 requests。任何网络错误都捕获并返回降级结果，
    保证主视觉流程不被端侧服务故障拖垮。
    """

    def __init__(
        self,
        detect_url: str = DEFAULT_DETECT_URL,
        fire_url: str = DEFAULT_FIRE_URL,
        timeout: float = 10.0,
    ) -> None:
        self.detect_url = detect_url
        self.fire_url = fire_url
        self.timeout = timeout

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST JSON 并解析响应；失败返回 {"error": ...} 不抛异常。"""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
            logger.warning("[EdgeVLM] 请求 %s 失败，降级: %s", url, exc)
            return {"error": str(exc)}

    def detect_objects(self, image_bytes: bytes) -> Dict[str, Any]:
        """调 8765/detect 通用检测。返回 {detections, latency_ms, error?}。"""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return self._post_json(self.detect_url, {"image": b64})

    def detect_fire(self, image_bytes: bytes) -> Dict[str, Any]:
        """调 8766/detect_fire 火焰/烟雾检测。返回 {fire_detected, smoke_detected, conf, latency_ms, error?}。"""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return self._post_json(self.fire_url, {"image": b64})

    def health(self) -> Dict[str, Dict[str, Any]]:
        """查两个服务的 /health。"""
        out: Dict[str, Dict[str, Any]] = {}
        for name, base in (("detect", self.detect_url.rsplit("/", 1)[0]),
                           ("fire", self.fire_url.rsplit("/", 1)[0])):
            try:
                with urllib.request.urlopen(base + "/health", timeout=self.timeout) as resp:
                    out[name] = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                out[name] = {"error": str(exc)}
        return out


def infer_hazards(
    image_bytes: bytes,
    client: Optional[EdgeVLMClient] = None,
) -> Dict[str, Any]:
    """综合两个端侧服务，推断当前帧的危险类型。

    Returns:
        {
          "hazards": {hazard_type: detected_bool, ...},  # 当前帧命中的危险
          "details": {"fire": {...}, "detect": {...}},   # 原始服务返回
          "latency_ms": float,
        }
    """
    client = client or EdgeVLMClient()
    t0 = time.time()
    hazards: Dict[str, bool] = {}

    # 1) 火焰/烟雾端侧模型
    fire_resp = client.detect_fire(image_bytes)
    if fire_resp.get("fire_detected"):
        hazards["oil_fire"] = True  # 火焰 -> 油锅起火(critical, emergency_shutdown)
    if fire_resp.get("smoke_detected"):
        hazards["smoke"] = True

    # 2) COCO 通用检测 -> 厨房危险推断
    det_resp = client.detect_objects(image_bytes)
    det_list = det_resp.get("detections", []) or []
    for d in det_list:
        cls = (d.get("class_name") or d.get("class") or "").lower()
        ht = COCO_HAZARD_MAP.get(cls)
        if ht:
            hazards[ht] = True

    return {
        "hazards": hazards,
        "details": {"fire": fire_resp, "detect": det_resp},
        "latency_ms": round((time.time() - t0) * 1000, 2),
    }


def run_safety_pipeline(
    image_bytes: bytes,
    client: Optional[EdgeVLMClient] = None,
    aggregator: Optional[TemporalAggregator] = None,
    controller: Optional[DeviceController] = None,
) -> Dict[str, Any]:
    """完整端侧安全闭环：感知 -> 多帧聚合 -> 达阈值 -> 设备处置。

    每帧调 infer_hazards，把每个 hazard_type 的 detected 喂给 TemporalAggregator；
    聚合达阈值时触发 device_controller.control_hazard(action)，返回告警+动作。

    Returns:
        {
          "alerts": [{"hazard_type", "level", "action"}],  # 本帧触发的告警
          "actions": [device_controller 返回结果],          # 本帧执行的设备动作
          "aggregator_snapshot": {hazard_type: 命中帧数},
          "latency_ms": float,
        }
    """
    client = client or EdgeVLMClient()
    aggregator = aggregator or get_default_aggregator()
    controller = controller or get_default_controller()

    t0 = time.time()
    result = infer_hazards(image_bytes, client)
    hazards = result["hazards"]

    # 所有可能的 hazard_type（命中 + 规则表里的，确保未命中的也 feed False 让窗口滑出）
    all_types = set(hazards.keys()) | {"smoke", "oil_fire", "knife_danger"}
    alerts: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []

    for ht in all_types:
        detected = hazards.get(ht, False)
        should_alert = aggregator.feed(ht, detected)
        if should_alert:
            rule = get_rule(ht)
            alerts.append({
                "hazard_type": ht,
                "level": rule["level"],
                "action": rule["action"],
                "label": rule["label"],
            })
            # 触发设备处置（dry_run 默认 True，安全）
            try:
                act = controller.control_hazard(ht)
                actions.append(act)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[EdgeVLM] 设备处置 %s 异常: %s", ht, exc)
                actions.append({"success": False, "hazard_type": ht, "error": str(exc)})

    return {
        "alerts": alerts,
        "actions": actions,
        "aggregator_snapshot": aggregator.snapshot(),
        "latency_ms": round((time.time() - t0) * 1000, 2),
    }


__all__ = [
    "EdgeVLMClient",
    "infer_hazards",
    "run_safety_pipeline",
    "COCO_HAZARD_MAP",
    "DEFAULT_DETECT_URL",
    "DEFAULT_FIRE_URL",
]
