import serial
import time
from datetime import datetime

def listen_and_parse():
    print("="*70)
    print("COM13 重量传感器监听程序")
    print("="*70)
    print("波特率: 2400 bps")
    print("按 Ctrl+C 停止")
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

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                buffer += data

                # 显示原始数据
                print(f"\n[{timestamp}] 收到 {len(data)} 字节")
                print(f"  HEX: {data.hex()}")
                try:
                    ascii_text = data.decode('ascii', errors='ignore')
                    if ascii_text.strip():
                        print(f"  ASCII: {repr(ascii_text)}")
                except:
                    pass

                # 尝试找到完整的数据包
                while len(buffer) >= 10:
                    # 查找帧头 0x28
                    idx = buffer.find(b'\x28')
                    if idx == -1:
                        # 没有找到帧头，保留最后几个字节
                        buffer = buffer[-5:]
                        break

                    # 找到帧头，检查是否有足够数据
                    if idx + 50 <= len(buffer):
                        # 完整的50字节数据包
                        packet = buffer[idx:idx+50]
                        buffer = buffer[idx+50:]

                        print(f"\n{'='*70}")
                        print(f"完整数据包! ({len(packet)} 字节)")
                        print(f"{'='*70}")
                        print(f"HEX: {packet.hex()}")
                        print(f"\n协议分析:")
                        print(f"  帧头: 0x{packet[0]:02X}")
                        print(f"  帧尾: 0x{packet[-1]:02X}")

                        # 提取重量值 - 尝试多个位置
                        print(f"\n可能的重量值:")
                        for offset in range(2, 20):
                            if offset + 1 < len(packet):
                                # 低字节在前
                                weight_le = (packet[offset+1] << 8) | packet[offset]
                                # 高字节在前
                                weight_be = (packet[offset] << 8) | packet[offset+1]

                                if 0 < weight_le < 10000:
                                    print(f"  [{offset},{offset+1}] LE: {weight_le}")
                                if 0 < weight_be < 10000 and weight_be != weight_le:
                                    print(f"  [{offset},{offset+1}] BE: {weight_be}")

                        # 尝试更多解析
                        if packet[-1] == 0xFF:
                            print(f"\n✓ 帧尾 0xFF 正确")

                            # 尝试XOR校验
                            xor_sum = 0
                            for b in packet[:-1]:
                                xor_sum ^= b
                            print(f"  XOR校验: 计算值=0x{xor_sum:02X}")

                        print(f"{'='*70}\n")

                    elif idx + 10 <= len(buffer):
                        # 部分数据，窗口移动
                        buffer = buffer[idx:]
                        break
                    else:
                        # 数据不够，保留从帧头开始的数据
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
    listen_and_parse()
