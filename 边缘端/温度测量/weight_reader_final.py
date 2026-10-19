import serial
import time
from datetime import datetime

def parse_weight_data():
    print("="*70)
    print("重量传感器解析程序")
    print("="*70)
    print("波特率: 2400 bps")
    print("协议: 50字节, 帧头=0x28, 重量偏移=[6,7] 高字节在前")
    print("-"*70)

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
        packet_count = 0

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                # 处理完整数据包
                while True:
                    idx = buffer.find(b'\x28')
                    if idx == -1:
                        buffer = buffer[-5:] if len(buffer) > 5 else b''
                        break

                    if idx + 50 <= len(buffer):
                        packet = buffer[idx:idx+50]
                        buffer = buffer[idx+50:]
                        packet_count += 1

                        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                        # 提取重量值 (偏移6-7, 高字节在前)
                        weight = (packet[6] << 8) | packet[7]
                        weight_grams = weight / 1000.0  # 转换为克

                        # 打印结果
                        print(f"[{timestamp}] #{packet_count:3d} | 重量: {weight_grams:7.3f} g | HEX: {packet.hex()}")

                        # 也显示其他可能的解析位置作为参考
                        for offset in [2, 3, 4, 5, 8, 9, 10, 11]:
                            if offset + 1 < len(packet):
                                val = (packet[offset] << 8) | packet[offset+1]
                                if 0 < val < 10000:
                                    print(f"            其他位置[{offset},{offset+1}]: {val/1000:.3f}g", end="")
                        print()

                    elif idx + 10 <= len(buffer):
                        buffer = buffer[idx:]
                        break
                    else:
                        buffer = buffer[idx:]
                        break

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
    parse_weight_data()
