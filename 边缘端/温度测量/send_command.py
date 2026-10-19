import serial
import time
from datetime import datetime

def send_command_and_listen():
    print("="*70)
    print("CH340 命令发送和监听程序")
    print("="*70)
    print("\n常用命令:")
    print("  1. 发送 'RD'    - 读取重量")
    print("  2. 发送 'READ'  - 读取重量")
    print("  3. 发送 'GET'   - 获取数据")
    print("  4. 发送 'WT'    - 获取重量")
    print("  5. 发送 '\\x55'  - 发送0x55")
    print("  6. 发送 '\\xFF'  - 发送0xFF")
    print("  7. 自定义命令")
    print("  8. 扫描模式 - 尝试发送多个常用命令")
    print("\n按 Ctrl+C 退出")
    print("-"*70)

    baudrate = 9600

    try:
        ser = serial.Serial(
            port='COM13',
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=2
        )

        print(f"\n✓ 串口已打开 @ {baudrate} bps")
        ser.reset_input_buffer()
        time.sleep(0.5)

        while True:
            print(f"\n请输入命令编号 (1-8): ", end="")
            choice = input().strip()

            if choice == '8':
                # 扫描模式
                commands = [
                    b'RD\r\n',
                    b'READ\r\n',
                    b'GET\r\n',
                    b'WT\r\n',
                    b'\x55\x55',
                    b'\xFF\xFF',
                    b'\xA5\x35',
                    b'?',
                    b'STATUS\r\n',
                    b'UNIT\r\n',
                ]

                print(f"\n{'='*70}")
                print("扫描模式 - 尝试 {len(commands)} 个常用命令")
                print(f"{'='*70}\n")

                for i, cmd in enumerate(commands):
                    print(f"[{i+1}] 发送: {cmd!r}")
                    try:
                        ser.reset_input_buffer()
                        ser.write(cmd)
                        time.sleep(0.5)

                        if ser.in_waiting > 0:
                            data = ser.read(ser.in_waiting)
                            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                            print(f"    ✓ 收到 {len(data)} 字节!")
                            print(f"    HEX: {data.hex()}")
                            try:
                                ascii_text = data.decode('ascii', errors='ignore')
                                if ascii_text.strip():
                                    print(f"    ASCII: {repr(ascii_text)}")
                            except:
                                pass
                            print()
                        else:
                            print(f"    无响应")

                    except Exception as e:
                        print(f"    错误: {e}")

                    time.sleep(0.3)

                print(f"\n扫描完成\n")

            elif choice == '7':
                print("请输入自定义命令(支持\\x格式): ", end="")
                cmd_str = input().strip()

                # 简单的\x格式解析
                try:
                    if cmd_str.startswith('\\x'):
                        cmd = bytes.fromhex(cmd_str.replace('\\x', ''))
                    else:
                        cmd = cmd_str.encode('ascii')
                except:
                    print("命令格式错误")
                    continue

                print(f"发送: {cmd!r}")
                ser.reset_input_buffer()
                ser.write(cmd)

                time.sleep(1)
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                    print(f"收到: {data.hex()} | {data.decode('ascii', errors='ignore')}")
                else:
                    print("无响应")

            elif choice in ['1', '2', '3', '4', '5', '6']:
                commands = {
                    '1': (b'RD\r\n', '读取重量'),
                    '2': (b'READ\r\n', '读取重量'),
                    '3': (b'GET\r\n', '获取数据'),
                    '4': (b'WT\r\n', '获取重量'),
                    '5': (b'\x55\x55', '发送0x55'),
                    '6': (b'\xFF\xFF', '发送0xFF'),
                }

                cmd, desc = commands[choice]
                print(f"发送 [{desc}]: {cmd!r}")
                ser.reset_input_buffer()
                ser.write(cmd)

                time.sleep(1)
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    print(f"收到: {data.hex()} | {data.decode('ascii', errors='ignore')}")
                else:
                    print("无响应")

            elif choice == 'q':
                break

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n程序已停止")
    finally:
        if 'ser' in locals():
            ser.close()

if __name__ == "__main__":
    send_command_and_listen()
