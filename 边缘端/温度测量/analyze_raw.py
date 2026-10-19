import serial
import time
from datetime import datetime

def analyze_raw_data():
    print("="*70)
    print("分析原始数据包 - 查看完整50字节内容")
    print("="*70)

    try:
        ser = serial.Serial(
            port='COM13',
            baudrate=2400,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=None
        )

        print("\n✓ 串口已连接，等待数据...\n")

        buffer = b''

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]

                    print("="*70)
                    print("收到完整数据包 (50字节):")
                    print("="*70)

                    print(f"\nHEX: {packet.hex()}")
                    print(f"\n按字节位置显示:")
                    for i in range(0, 50, 10):
                        line = f"[{i:2d}] "
                        for j in range(i, min(i+10, 50)):
                            line += f"{packet[j]:02X} "
                        print(line)

                    # 检查是否符合协议
                    print(f"\n协议检查:")
                    print(f"  帧头 (第一个字节): 0x{packet[0]:02X} (期望 0x28)")
                    print(f"  帧尾 (最后一个字节): 0x{packet[49]:02X} (期望 0xFF)")

                    # 尝试找零值
                    print(f"\n查找可能的零值位置:")
                    for i in range(1, 49):
                        val = (packet[i] << 8) | packet[i+1]
                        if val < 100:  # 接近0的值
                            print(f"  位置[{i},{i+1}]: {val} (HEX: 0x{packet[i]:02X} 0x{packet[i+1]:02X})")

                    # 检查字节分布
                    print(f"\n字节值分布:")
                    non_zero = sum(1 for b in packet if b != 0)
                    print(f"  非零字节数: {non_zero}/50")
                    print(f"  0x28出现次数: {packet.count(0x28)}")
                    print(f"  0xFF出现次数: {packet.count(0xFF)}")

                    break

            time.sleep(0.01)

        ser.close()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    analyze_raw_data()
