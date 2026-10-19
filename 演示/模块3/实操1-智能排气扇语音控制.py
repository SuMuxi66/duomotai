"""
【实操动作1：智能排气扇语音控制】实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第三模块·选手4实操手3：

双层控制方案：
- 前端本地正则解析：毫秒级识别开关风扇指令，无需调用大模型，极致缩短响应延迟
- 后端 miIO 协议控设备：对接智能插座硬件，实现排气扇启停控制
- 设备状态回读校验：下发指令后主动读取设备真实状态，校验执行结果，
  解决网络 UDP 丢包、指令执行失效问题，杜绝虚触发、无效指令
"""

import re
import sys
from typing import Optional

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# ==================== 前端本地指令快速解析 ====================

# 本地指令快速解析正则（毫秒级，无需调用大模型）
FAN_OPEN_PATTERN = re.compile(r"(打开|开启|启动)(风扇|排气扇)")
FAN_CLOSE_PATTERN = re.compile(r"(关闭|关掉|停止)(风扇|排气扇)")


def parse_fan_cmd(text: str) -> Optional[str]:
    """
    前端本地毫秒级指令解析

    匹配开关风扇指令，返回 "on" / "off" / None。
    本地正则识别规避大模型远程调用的延迟问题，响应极快。
    """
    if FAN_OPEN_PATTERN.search(text):
        return "on"
    if FAN_CLOSE_PATTERN.search(text):
        return "off"
    return None


# ==================== 后端 miIO 协议控设备 ====================

class FanDevice:
    """
    后端 miIO 协议设备控制（含状态回读校验）

    对智能插座（排气扇）下发启停指令，并主动回读设备真实运行状态
    校验指令执行结果，解决网络 UDP 丢包、指令执行失效问题。
    """

    def __init__(self, ip: str = "192.168.1.99", token: str = "demo-token"):
        self.ip = ip
        self.token = token
        self.device_power = False  # 模拟设备真实运行状态
        self.connected = True      # 模拟已连接

    def connect(self) -> bool:
        """设备初始化连接（真实场景为 miio Device(ip, token)）"""
        return self.connected

    def _send(self, command: str, *args) -> list:
        """模拟 miIO 协议下发指令（真实场景为 device.send 网络调用）"""
        if command == "set_power":
            self.device_power = (args[0] == "on")
        # 模拟 0.5s 设备响应延迟
        import time
        time.sleep(0.3)
        return ["on" if self.device_power else "off"]

    def open_fan(self) -> str:
        """开启风扇 + 状态回读校验"""
        if not self.connected:
            return "设备未连接"
        self._send("set_power", "on")
        # 状态回读校验：主动读取设备真实运行状态
        res = self._send("get_prop", "power")
        return "排气扇开启成功（状态回读校验通过）" if res[0] == "on" else "开启失败，请重试"

    def close_fan(self) -> str:
        """关闭风扇 + 状态回读校验"""
        if not self.connected:
            return "设备未连接"
        self._send("set_power", "off")
        # 状态回读校验
        res = self._send("get_prop", "power")
        return "排气扇关闭成功（状态回读校验通过）" if res[0] == "off" else "关闭失败，请重试"


# ==================== 实操运行演示 ====================

def main():
    print("=" * 60)
    print("【实操动作1】智能排气扇语音控制实操演示")
    print("=" * 60)

    # ① 前端本地毫秒级指令解析
    print("\n[1/3] 前端本地毫秒级指令解析（无需调用大模型）")
    commands = [
        "小云小云，打开排气扇",
        "帮我开启风扇",
        "把排气扇关掉",
        "今天天气不错",  # 无风扇指令
    ]
    for cmd in commands:
        action = parse_fan_cmd(cmd)
        print(f"  指令: 「{cmd}」 → {action if action else '无风扇指令（忽略）'}")

    # ② 后端 miIO 协议控制 + 状态回读校验
    print("\n[2/3] 后端 miIO 协议控制 + 设备状态回读校验")
    fan = FanDevice()
    print(f"  设备连接: {fan.connect()}（ip={fan.ip}, token={fan.token}）")

    # ③ 语音指令闭环执行
    print("\n[3/3] 语音指令 → 协议控制 → 状态回读闭环")
    for cmd in commands:
        action = parse_fan_cmd(cmd)
        if action == "on":
            print(f"  指令「{cmd}」")
            print(f"    → 下发 set_power on → {fan.open_fan()}")
        elif action == "off":
            print(f"  指令「{cmd}」")
            print(f"    → 下发 set_power off → {fan.close_fan()}")

    print("\n✅ 实操动作1 演示完成：前端快解析 + 后端协议控制 + 状态回读校验，全程闭环。")


if __name__ == "__main__":
    main()
