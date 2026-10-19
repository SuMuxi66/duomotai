"""统一设备控制：按 hazard_rules 的 action 路由到具体设备动作。

设计要点：
- 参考 fan.py 的 miIO 调用风格（plug.send("set_properties", ...)）。
- 缺真实设备 / 依赖缺失时安全降级：仅 log + 返回 simulated=True 的成功结果，
  保证聚合 + 路由逻辑可测、可演示，且不影响主视觉流程。
- 后端 callable 可注入，便于单测 mock 设备。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from .hazard_rules import (
    ACTION_ALERT_ONLY,
    ACTION_EMERGENCY_SHUTDOWN,
    ACTION_OPEN_FAN,
    ACTION_VOICE_REMIND,
    get_action,
    get_rule,
)

logger = logging.getLogger(__name__)

# 设备操作 op 名称（与 fan.py 现有函数名对齐，便于真实调用）
OP_FAN_ON = "fan_on"
OP_FAN_OFF = "fan_off"
OP_GAS_VALVE_OFF = "gas_valve_off"
OP_GAS_VALVE_ON = "gas_valve_on"


class DeviceController:
    """按 action 路由的统一设备控制器。

    Args:
        backend: 可选的设备后端 callable(op: str, device_config: dict) -> dict。
            注入时（测试/仿真）直接调用；为 None 时走真实 fan.py 风格调用。
        dry_run: True（默认）时，真实设备调用失败也返回 simulated 成功，
            不向上抛异常，确保主流程不中断。
    """

    def __init__(
        self,
        backend: Optional[Callable[[str, Dict], Dict[str, Any]]] = None,
        dry_run: bool = True,
    ) -> None:
        self._backend = backend
        self._dry_run = dry_run

    def control(self, action: str, device_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """按 hazard_rules 的 action 路由到具体设备动作。

        Args:
            action: hazard_rules 中的 action 值（alert_only/voice_remind/open_fan/emergency_shutdown）。
            device_config: 设备配置（IP/token/model 等）；缺省走 fan.py 默认。

        Returns:
            {success, action, executed, message, [results]}
        """
        device_config = device_config or {}

        # 纯告警 / 语音提醒：不触发物理设备
        if action == ACTION_ALERT_ONLY:
            return {
                "success": True,
                "action": action,
                "executed": False,
                "message": "仅告警，无设备动作",
            }
        if action == ACTION_VOICE_REMIND:
            return {
                "success": True,
                "action": action,
                "executed": False,
                "message": "语音提醒（由 TTS 通道下发）",
            }

        # 需要物理设备的动作
        ops = self._route_action(action)
        if not ops:
            return {
                "success": False,
                "action": action,
                "executed": False,
                "message": f"未知动作: {action}",
            }

        results = [self._call_device(op, device_config) for op in ops]
        all_ok = all(r.get("success", False) for r in results)
        return {
            "success": all_ok,
            "action": action,
            "executed": True,
            "results": results,
            "message": "已完成" if all_ok else "部分失败",
        }

    def control_hazard(self, hazard_type: str, device_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """便捷方法：按 hazard_type 查规则后路由。"""
        return self.control(get_action(hazard_type), device_config)

    @staticmethod
    def _route_action(action: str) -> list:
        """action -> 设备操作 op 列表。"""
        if action == ACTION_OPEN_FAN:
            return [OP_FAN_ON]
        if action == ACTION_EMERGENCY_SHUTDOWN:
            # 关燃气阀 + 开排气扇（排散可燃气体 / 烟雾）
            return [OP_GAS_VALVE_OFF, OP_FAN_ON]
        return []

    def _call_device(self, op: str, device_config: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个设备操作。注入 backend 优先；否则惰性走 fan.py 真实调用。

        任何异常都被捕获，dry_run 模式下返回 simulated 成功。
        """
        # 1) 注入后端（测试/仿真）
        if self._backend is not None:
            try:
                result = self._backend(op, device_config)
                if not isinstance(result, dict):
                    result = {"success": True, "raw": result}
                result.setdefault("op", op)
                return result
            except Exception as exc:  # pragma: no cover - 异常路径
                logger.warning("[DeviceController] 注入后端 %s 异常: %s", op, exc)
                return {"success": False, "op": op, "error": str(exc)}

        # 2) 真实设备：惰性导入 fan.py（避免 requests/miio 缺失时模块加载失败）
        try:
            return self._call_real_device(op, device_config)
        except Exception as exc:
            logger.warning("[DeviceController] 真实设备 %s 不可用，降级模拟: %s", op, exc)
            if self._dry_run:
                return {
                    "success": True,
                    "op": op,
                    "simulated": True,
                    "message": f"真实设备不可用，模拟执行 {op}",
                    "error": str(exc),
                }
            return {"success": False, "op": op, "error": str(exc)}

    @staticmethod
    def _call_real_device(op: str, device_config: Dict[str, Any]) -> Dict[str, Any]:
        """真实设备调用，复用 fan.py 的 miIO 风格。

        device_config 可覆盖默认 IP/TOKEN/MODEL；为空走 fan.py 内置默认。
        未知 op 返回 simulated（不抛错，避免主流程中断）。
        """
        if op == OP_FAN_ON:
            from .fan import fan_on
            return fan_on()
        if op == OP_FAN_OFF:
            from .fan import fan_off
            return fan_off()
        # 燃气阀：项目暂无独立模块，按 miIO 风格直接发指令（参考 fan.py）
        if op in (OP_GAS_VALVE_OFF, OP_GAS_VALVE_ON):
            ip = device_config.get("ip") or device_config.get("IP")
            token = device_config.get("token") or device_config.get("TOKEN")
            model = device_config.get("model") or device_config.get("MODEL") or "chuangmi.plug.v3"
            value = (op == OP_GAS_VALVE_ON)
            if not (ip and token):
                raise RuntimeError(f"燃气阀 {op} 缺少 ip/token 配置")
            try:
                from miio import DeviceFactory  # type: ignore
                dev = DeviceFactory.create(ip, token, model=model, lazy_discover=True)
                dev.send("set_properties", [{"did": "switch", "siid": 2, "piid": 1, "value": value}])
                return {"success": True, "op": op, "is_on": value, "message": f"燃气阀 {'开' if value else '关'}"}
            except Exception:
                # DeviceFactory 不可用时回退 Device
                from miio import Device  # type: ignore
                dev = Device(ip, token, model=model, lazy_discover=True)
                dev.send("set_properties", [{"did": "switch", "siid": 2, "piid": 1, "value": value}])
                return {"success": True, "op": op, "is_on": value, "message": f"燃气阀 {'开' if value else '关'}"}
        # 未知 op：安全降级
        return {"success": True, "op": op, "simulated": True, "message": f"未知 op {op}，模拟执行"}


# ====================== 默认单例 ======================
_default_controller: Optional[DeviceController] = None


def get_default_controller(dry_run: bool = True) -> DeviceController:
    """惰性获取进程级默认控制器（dry_run 默认 True，保证主流程不中断）。"""
    global _default_controller
    if _default_controller is None:
        _default_controller = DeviceController(backend=None, dry_run=dry_run)
    return _default_controller


def reset_default_controller() -> None:
    """重置默认单例（测试隔离用）。"""
    global _default_controller
    _default_controller = None


__all__ = [
    "DeviceController",
    "OP_FAN_ON",
    "OP_FAN_OFF",
    "OP_GAS_VALVE_OFF",
    "OP_GAS_VALVE_ON",
    "get_default_controller",
    "reset_default_controller",
]
