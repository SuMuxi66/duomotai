#!/usr/bin/env python3
import sys
import time
import serial
import serial.tools.list_ports
from typing import Optional, List

class SolenoidValveController:
    def __init__(self, port: Optional[str] = None, baudrate: int = 9600):
        self.baudrate = baudrate
        self.port = port
        self.serial_conn: Optional[serial.Serial] = None
        self.current_state = False

    @staticmethod
    def list_ports() -> List[str]:
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]

    @staticmethod
    def print_available_ports() -> None:
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("未发现可用串口")
            return
        print("可用串口:")
        for i, p in enumerate(ports, 1):
            print(f"  {i}. {p.device} - {p.description}")

    def connect(self, port: Optional[str] = None) -> bool:
        if port:
            self.port = port
        if not self.port:
            print("错误: 未指定串口")
            return False
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            time.sleep(0.1)
            print(f"已连接到 {self.port}")
            return True
        except serial.SerialException as e:
            print(f"连接失败: {e}")
            return False

    def disconnect(self) -> None:
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            print("已断开连接")

    def send_command(self, cmd: str) -> None:
        if not self.serial_conn or not self.serial_conn.is_open:
            print("错误: 未连接")
            return
        try:
            self.serial_conn.write(cmd.encode())
        except serial.SerialException as e:
            print(f"发送命令失败: {e}")

    def open(self) -> bool:
        self.send_command("O")
        self.current_state = True
        return True

    def close(self) -> bool:
        self.send_command("F")
        self.current_state = False
        return True

    def toggle(self) -> bool:
        if self.current_state:
            return self.close()
        else:
            return self.open()

    def pulse(self, duration_ms: int) -> bool:
        if duration_ms <= 0 or duration_ms > 3600000:
            print("错误: 脉冲时间必须在1-3600000毫秒之间")
            return False
        self.open()
        time.sleep(duration_ms / 1000.0)
        self.close()
        return True

    def cycle(self, on_ms: int = 500, off_ms: int = 500, count: Optional[int] = None) -> None:
        loop_count = 0
        try:
            while True:
                self.open()
                print(f"[循环] 打开 ({loop_count + 1})")
                time.sleep(on_ms / 1000.0)
                self.close()
                print(f"[循环] 关闭 ({loop_count + 1})")
                time.sleep(off_ms / 1000.0)
                loop_count += 1
                if count is not None and loop_count >= count:
                    break
        except KeyboardInterrupt:
            self.close()
            print("\n[循环] 已停止")
            return

    def get_status(self) -> Optional[str]:
        return "ON" if self.current_state else "OFF"

    def is_connected(self) -> bool:
        return self.serial_conn is not None and self.serial_conn.is_open


def interactive_mode(controller: SolenoidValveController) -> None:
    print("\n=== 电磁阀控制程序 ===")
    print("命令: on/off/status/pulse <毫秒>/toggle/cycle <开时间> <关时间> <次数>/quit\n")

    while True:
        try:
            cmd = input("> ").strip().lower()
            if not cmd:
                continue

            if cmd == "quit" or cmd == "exit" or cmd == "q":
                controller.disconnect()
                break
            elif cmd == "on":
                if controller.open():
                    print("电磁阀已打开")
            elif cmd == "off":
                if controller.close():
                    print("电磁阀已关闭")
            elif cmd == "status":
                status = controller.get_status()
                if status:
                    print(f"电磁阀状态: {status}")
            elif cmd == "toggle":
                if controller.toggle():
                    status = controller.get_status()
                    print(f"电磁阀已切换，当前状态: {status}")
            elif cmd.startswith("pulse"):
                parts = cmd.split()
                if len(parts) == 2 and parts[1].isdigit():
                    duration = int(parts[1])
                    if controller.pulse(duration):
                        print(f"脉冲信号已发送，持续{duration}毫秒")
                else:
                    print("用法: pulse <毫秒>")
            elif cmd.startswith("cycle"):
                parts = cmd.split()
                on_ms = 500
                off_ms = 500
                count = None
                if len(parts) >= 2 and parts[1].isdigit():
                    on_ms = int(parts[1])
                if len(parts) >= 3 and parts[2].isdigit():
                    off_ms = int(parts[2])
                if len(parts) >= 4 and parts[3].isdigit():
                    count = int(parts[3])
                print(f"开始循环: 开{on_ms}ms, 关{off_ms}ms, 次数: {'无限' if count is None else count}")
                print("按 Ctrl+C 停止循环")
                controller.cycle(on_ms, off_ms, count)
            elif cmd == "help":
                print("可用命令:")
                print("  on/off/status/toggle - 基本控制")
                print("  pulse <毫秒>         - 脉冲控制")
                print("  cycle <开时间> <关时间> <次数> - 循环控制")
                print("  help                 - 显示帮助")
                print("  quit                 - 退出程序")
            else:
                print("未知命令，输入 'help' 查看可用命令")
        except KeyboardInterrupt:
            print("\n")
            controller.disconnect()
            break
        except Exception as e:
            print(f"错误: {e}")


def script_mode(controller: SolenoidValveController, commands: List[str]) -> None:
    for cmd in commands:
        cmd = cmd.strip()
        if not cmd or cmd.startswith('#'):
            continue
        print(f"执行: {cmd}")
        if cmd.lower() == "on":
            controller.open()
        elif cmd.lower() == "off":
            controller.close()
        elif cmd.lower() == "status":
            status = controller.get_status()
            print(f"状态: {status}")
        elif cmd.lower().startswith("pulse "):
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                controller.pulse(int(parts[1]))
        time.sleep(0.1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='电磁阀串口控制程序')
    parser.add_argument('-p', '--port', type=str, help='串口设备名 (如 COM3)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='波特率 (默认 9600)')
    parser.add_argument('-l', '--list', action='store_true', help='列出可用串口')
    parser.add_argument('-s', '--script', type=str, help='执行脚本文件')
    args = parser.parse_args()

    if args.list:
        SolenoidValveController.print_available_ports()
        return

    controller = SolenoidValveController(baudrate=args.baudrate)

    if args.port:
        if not controller.connect(args.port):
            sys.exit(1)
    else:
        ports = SolenoidValveController.list_ports()
        if not ports:
            print("错误: 未发现可用串口，请使用 -l 参数查看")
            sys.exit(1)
        SolenoidValveController.print_available_ports()
        port_num = input("\n请选择串口编号: ").strip()
        if port_num.isdigit() and 1 <= int(port_num) <= len(ports):
            selected_port = ports[int(port_num) - 1]
            if not controller.connect(selected_port):
                sys.exit(1)
        else:
            print("无效的选择")
            sys.exit(1)

    if args.script:
        try:
            with open(args.script, 'r', encoding='utf-8') as f:
                commands = f.readlines()
            script_mode(controller, commands)
            controller.disconnect()
        except FileNotFoundError:
            print(f"错误: 找不到脚本文件 {args.script}")
            sys.exit(1)
    else:
        interactive_mode(controller)


if __name__ == "__main__":
    main()
