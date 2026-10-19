import serial
import time
from datetime import datetime

BAUDRATES = [300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 56000, 57600, 115200, 128000, 230400, 256000]

def scan_all_baudrates():
    print("="*70)
    print("全波特率扫描程序")
    print("="*70)
    print(f"尝试 {len(BAUDRATES)} 个波特率")
    print("-"*70)

    for baudrate in BAUDRATES:
        print(f"\n尝试 {baudrate} bps... ", end="", flush=True)

        try:
            ser = serial.Serial(
                port='COM13',
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1.5
            )

            ser.reset_input_buffer()
            time.sleep(0.3)

            # 等待数据
            start = time.time()
            data_received = False

            while time.time() - start < 2:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                    # 检查是否像正确的协议
                    is_valid = False
                    if len(data) >= 10:
                        # 检查是否有合理的帧头帧尾
                        if data[0] == 0x28 and data[-1] == 0xFF:
                            is_valid = True
                        # 或者检查是否有0x5A帧头
                        elif data[0] == 0x5A:
                            is_valid = True
                        # 或者检查是否大部分是可见ASCII字符
                        ascii_ratio = sum(1 for b in data if 32 <= b <= 126) / len(data)
                        if ascii_ratio > 0.7:
                            is_valid = True

                    if is_valid:
                        print(f"\n\n!!! 可能找到正确波特率 !!!")
                        print(f"{'='*70}")
                        print(f"波特率: {baudrate} bps")
                        print(f"时间: {timestamp}")
                        print(f"数据长度: {len(data)} 字节")
                        print(f"HEX: {data.hex()}")
                        print(f"ASCII: {data.decode('ascii', errors='ignore')}")
                        print(f"{'='*70}\n")

                        # 保存成功配置
                        with open('successful_config.txt', 'w') as f:
                            f.write(f"波特率: {baudrate}\n")
                            f.write(f"数据: {data.hex()}\n")
                            f.write(f"时间: {timestamp}\n")

                        ser.close()
                        return baudrate, data
                    else:
                        print(f"\n  收到 {len(data)} 字节，但数据格式不对: {data[:20].hex()}")

                    data_received = True

                time.sleep(0.05)

            ser.close()

            if not data_received:
                print(f"无数据", end="")
            else:
                print(f"数据格式不对", end="")

        except serial.SerialException as e:
            print(f"端口错误: {e}", end="")
        except Exception as e:
            print(f"错误: {e}", end="")

    print(f"\n\n{'='*70}")
    print("扫描完成 - 未找到正确的波特率")
    print(f"{'='*70}")

    print("\n可能原因:")
    print("  1. 单片机未发送数据")
    print("  2. 波特率不在列表中")
    print("  3. 硬件连接问题")

    return None, None

def listen_with_baudrate(baudrate):
    """使用指定波特率持续监听"""
    print(f"\n使用 {baudrate} bps 持续监听...")
    print("按 Ctrl+C 停止\n")

    try:
        ser = serial.Serial(
            port='COM13',
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=None
        )

        print("✓ 串口已连接，等待数据...\n")

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                print(f"\n[{timestamp}] {len(data)} 字节:")
                print(f"  HEX: {data.hex()}")

                # 检查帧头帧尾
                if data[0] == 0x28 and data[-1] == 0xFF:
                    print(f"  ✓ 协议匹配! 帧头=0x28, 帧尾=0xFF")

                    # 提取重量值
                    if len(data) >= 5:
                        # 尝试在偏移2-4位置找重量
                        for offset in range(2, 8):
                            if offset + 1 < len(data):
                                weight = (data[offset+1] << 8) | data[offset]
                                if 0 < weight < 10000:
                                    print(f"  可能的重量值[{offset},{offset+1}]: {weight}")

            time.sleep(0.01)

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n停止监听")
        if 'ser' in locals():
            ser.close()
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    baudrate, data = scan_all_baudrates()

    if baudrate:
        print(f"\n找到正确的波特率: {baudrate}")
        print("\n是否开始持续监听? (y/n): ", end="")
        if input().strip().lower() == 'y':
            listen_with_baudrate(baudrate)
    else:
        print("\n未能找到正确的波特率")
