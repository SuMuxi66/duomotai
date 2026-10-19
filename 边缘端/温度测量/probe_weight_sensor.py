import serial
import serial.tools.list_ports
import time
from datetime import datetime
import re

BAUDRATES = [9600, 115200, 4800, 19200, 38400, 57600, 1200, 2400]

def find_ch340_port():
    """查找CH340串口"""
    ports = serial.tools.list_ports.comports()
    ch340_ports = []
    for port in ports:
        if 'CH340' in port.description or 'USB-SERIAL' in port.description:
            ch340_ports.append(port.device)
        print(f"  {port.device}: {port.description}")
    return ch340_ports

def probe_scale(port, baudrate=9600, timeout=3):
    """探测电子秤数据格式"""
    print(f"\n{'='*70}")
    print(f"探测 {port} @ {baudrate} bps")
    print(f"{'='*70}")

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=timeout
        )

        print(f"✓ 串口打开成功")
        ser.reset_input_buffer()
        time.sleep(0.5)

        print(f"\n开始监听数据（等待 {timeout} 秒）...")
        print("-" * 70)

        start_time = time.time()
        all_data = []
        line_count = 0

        while time.time() - start_time < timeout:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                # 记录原始数据
                all_data.append({
                    'time': timestamp,
                    'raw': data,
                    'hex': data.hex(),
                    'ascii': data.decode('ascii', errors='ignore').strip()
                })

                # 打印详细信息
                print(f"\n[{timestamp}] 收到 {len(data)} 字节:")
                print(f"  HEX: {data.hex()}")
                print(f"  ASCII: {data.decode('ascii', errors='ignore')}")

                # 尝试解析为数字
                ascii_text = data.decode('ascii', errors='ignore').strip()
                if ascii_text:
                    # 查找可能的重量值
                    numbers = re.findall(r'[-+]?\d*\.\d+|\d+', ascii_text)
                    if numbers:
                        print(f"  可能的数值: {numbers}")

                line_count += 1
                if line_count >= 10:  # 限制显示行数
                    break

            time.sleep(0.05)

        print(f"\n{'-'*70}")
        print(f"共收到 {len(all_data)} 个数据包")

        # 分析数据模式
        if all_data:
            print(f"\n{'='*70}")
            print("数据分析:")
            print(f"{'='*70}")

            # 检查是否为ASCII文本
            ascii_samples = [d['ascii'] for d in all_data[:5] if d['ascii']]
            if ascii_samples:
                print(f"✓ 看起来是ASCII文本格式")
                print(f"  示例: {ascii_samples[0]}")
                print(f"\n  建议解析方式:")
                print(f"    - 直接使用 .decode('ascii') 或 .decode('utf-8')")
                print(f"    - 使用正则表达式提取数字: re.findall(r'[-+]?\\d*\\.\\d+', text)")

            # 检查是否为二进制
            if len(all_data) > 0 and len(all_data[0]['raw']) >= 4:
                first_packet = all_data[0]['raw']
                print(f"\n  首包 HEX: {first_packet.hex()}")

                # 检查是否有帧头
                if first_packet[:2] == b'\x5a\xa5' or first_packet[:2] == b'\x5a\x5a':
                    print(f"✓ 检测到帧头: 0x{first_packet[:2].hex()}")
                    print(f"  建议: 这是常见的传感器帧头格式")

                # 检查校验和
                if len(first_packet) >= 4:
                    print(f"  数据长度: {len(first_packet)} 字节")

        ser.close()
        return all_data

    except Exception as e:
        print(f"错误: {e}")
        return None

def auto_probe(port):
    """自动探测最佳波特率"""
    print(f"\n{'='*70}")
    print(f"自动探测 {port}")
    print(f"{'='*70}")

    for baudrate in BAUDRATES:
        print(f"\n尝试 {baudrate} bps... ", end="", flush=True)

        try:
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1.5
            )

            ser.reset_input_buffer()
            time.sleep(0.3)

            # 等待一小段时间看是否有数据
            time.sleep(1.5)

            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                print(f"✓ 收到 {len(data)} 字节!")

                # 显示数据
                print(f"   HEX: {data[:20].hex()}{'...' if len(data) > 20 else ''}")
                try:
                    ascii_text = data.decode('ascii', errors='ignore').strip()
                    if ascii_text:
                        print(f"   ASCII: {ascii_text[:50]}")
                except:
                    pass

                ser.close()
                return baudrate, data
            else:
                print(f"无数据")
                ser.close()

        except Exception as e:
            print(f"错误: {e}")

    return None, None

def main():
    print(f"{'='*70}")
    print(f"CH340 重量传感器探测工具")
    print(f"{'='*70}")

    # 查找CH340端口
    print(f"\n查找 CH340 串口设备:")
    print(f"-" * 70)
    ch340_ports = find_ch340_port()

    if not ch340_ports:
        print("\n未找到 CH340 设备!")
        print("\n请检查:")
        print("  1. CH340 是否已连接到电脑")
        print("  2. 驱动是否已安装")
        print("  3. 设备管理器中是否能看到 COM 端口")
        return

    print(f"\n找到 {len(ch340_ports)} 个 CH340 设备: {ch340_ports}")

    # 自动使用COM13或第一个找到的端口
    port = 'COM13' if 'COM13' in ch340_ports else ch340_ports[0]
    print(f"\n自动选择端口: {port}")

    # 先自动探测
    print("\n" + "="*70)
    baudrate, sample_data = auto_probe(port)

    if baudrate:
        print(f"\n✓ 成功! 波特率: {baudrate}")

        # 详细探测
        print("\n是否进行详细探测? (y/n): ", end="")
        if input().strip().lower() == 'y':
            probe_scale(port, baudrate, timeout=5)
    else:
        print("\n✗ 自动探测未找到数据")

        print("\n可能原因:")
        print("  1. 波特率不在测试范围内")
        print("  2. 单片机没有自动发送数据")
        print("  3. 硬件连接问题")
        print("\n建议:")
        print("  1. 尝试手动指定波特率探测")
        print("  2. 检查单片机固件是否正常发送数据")
        print("  3. 检查TX/RX接线是否正确")

if __name__ == "__main__":
    main()
