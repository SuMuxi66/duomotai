from typing import Dict, Any
import logging
import sys
import os
import requests
import miio

# 确保本地 _miio_deps 里的依赖（construct/appdirs/micloud 等）优先被加载
# 因为 python-miio 的依赖 netifaces 需要 C 编译器，我们用本地预编译包降级
_MIIO_DEPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_miio_deps")
if os.path.isdir(_MIIO_DEPS_DIR) and _MIIO_DEPS_DIR not in sys.path:
    sys.path.insert(0, _MIIO_DEPS_DIR)

# 禁用日志
logging.getLogger().setLevel(logging.ERROR)

# ====================== 设备信息 ======================
# 小米智能插座3 (cuco.plug.v3)
DEVICE_TYPE = "miio"  # 可选: "miio", "http"

# 小米设备配置
IP = "192.168.1.57"
TOKEN = "6a216485ef49f90b294bdbd37f1caa71"
MODEL = "cuco.plug.v3"

# HTTP设备配置（如果使用http模式）
HTTP_API_URL = f"http://{IP}:8080"
HTTP_API_KEY = "your_api_key"  # 如果需要API密钥
# ==========================================================

def get_plug():
    """获取设备实例"""
    if DEVICE_TYPE == "miio":
        try:
            from miio import DeviceFactory
            # 使用 DeviceFactory 创建设备实例
            return DeviceFactory.create(IP, TOKEN, model=MODEL, lazy_discover=True)
        except Exception as e:
            # 如果 DeviceFactory 不可用，回退到 Device
            try:
                from miio import Device
                return Device(IP, TOKEN, model=MODEL, lazy_discover=True)
            except Exception as e2:
                raise Exception(f"小米设备初始化失败: {e}, {e2}")
    elif DEVICE_TYPE == "http":
        return None
    else:
        raise Exception(f"不支持的设备类型: {DEVICE_TYPE}")

def fan_status() -> Dict[str, Any]:
    """获取风扇状态"""
    try:
        if DEVICE_TYPE == "miio":
            plug = get_plug()
            # 对于 cuco.plug.v3，开关状态在 siid=2, piid=1
            res = plug.send("get_properties", [{"did": "switch", "siid": 2, "piid": 1}])
            if isinstance(res, list) and len(res) > 0:
                is_on = res[0].get("value", False)
                return {"success": True, "is_on": is_on, "message": f"风扇当前状态: {'开启' if is_on else '关闭'}"}
            else:
                return {"success": False, "error": f"设备返回异常: {res}"}
        elif DEVICE_TYPE == "http":
            return {"success": True, "is_on": False, "message": "风扇当前状态: 关闭"}
        else:
            return {"success": False, "error": f"不支持的设备类型: {DEVICE_TYPE}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def fan_on() -> Dict[str, Any]:
    """开启风扇"""
    try:
        if DEVICE_TYPE == "miio":
            plug = get_plug()
            # 对于 cuco.plug.v3，设置开关状态
            plug.send("set_properties", [{"did": "switch", "siid": 2, "piid": 1, "value": True}])
            # 验证设备状态
            import time
            time.sleep(0.5)
            result = plug.send("get_properties", [{"did": "switch", "siid": 2, "piid": 1}])
            if isinstance(result, list) and len(result) > 0:
                is_on = result[0].get("value", False)
                if is_on:
                    return {"success": True, "is_on": True, "message": "✅ 已开启风扇"}
                else:
                    return {"success": False, "error": "设备未响应，请检查网络连接"}
            else:
                return {"success": False, "error": f"设备返回异常: {result}"}
        elif DEVICE_TYPE == "http":
            return {"success": True, "is_on": True, "message": "✅ 已开启风扇"}
        else:
            return {"success": False, "error": f"不支持的设备类型: {DEVICE_TYPE}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def fan_off() -> Dict[str, Any]:
    """关闭风扇"""
    try:
        if DEVICE_TYPE == "miio":
            plug = get_plug()
            plug.send("set_properties", [{"did": "switch", "siid": 2, "piid": 1, "value": False}])
            # 验证设备状态
            import time
            time.sleep(0.5)
            result = plug.send("get_properties", [{"did": "switch", "siid": 2, "piid": 1}])
            if isinstance(result, list) and len(result) > 0:
                is_on = result[0].get("value", False)
                if not is_on:
                    return {"success": True, "is_on": False, "message": "❌ 已关闭风扇"}
                else:
                    return {"success": False, "error": "设备未响应，请检查网络连接"}
            else:
                return {"success": False, "error": f"设备返回异常: {result}"}
        elif DEVICE_TYPE == "http":
            return {"success": True, "is_on": False, "message": "❌ 已关闭风扇"}
        else:
            return {"success": False, "error": f"不支持的设备类型: {DEVICE_TYPE}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def fan_control(action: str) -> Dict[str, Any]:
    """统一风扇控制接口"""
    if action == "on":
        return fan_on()
    elif action == "off":
        return fan_off()
    else:
        return {"success": False, "error": f"不支持的动作: {action}"}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(fan_control(sys.argv[1]))
    else:
        print(fan_status())