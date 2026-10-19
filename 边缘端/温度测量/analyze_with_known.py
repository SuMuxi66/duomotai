import serial
import time
from datetime import datetime

def analyze_with_known_data():
    print("="*70)
    print("使用已知数据对比分析")
    print("="*70)
    print("\n用户提供的原始数据 (50字节, 50g砝码):")
    print("28 43 91 FF 0E 03 A3 FE 91 95 87 E5 38 C2 0A 83 83 1A 1A FB A1 1D 06 C3 60 14 83 E3 E3 E7 06 8D 8F 02 49 8B 91 89 E3 87 08 8D FB 93 67 FA A2 C2 22 FF")
    print()

    known_data = bytes.fromhex("28 43 91 FF 0E 03 A3 FE 91 95 87 E5 38 C2 0A 83 83 1A 1A FB A1 1D 06 C3 60 14 83 E3 E3 E7 06 8D 8F 02 49 8B 91 89 E3 87 08 8D FB 93 67 FA A2 C2 22 FF")

    print(f"数据长度: {len(known_data)} 字节")
    print(f"帧头: 0x{known_data[0]:02X}")
    print(f"帧尾: 0x{known_data[49]:02X}")
    print()

    # 提取所有2字节值
    print("所有2字节位置的值 (高字节在前):")
    for i in range(48):
        val = (known_data[i] << 8) | known_data[i+1]
        if 0 < val < 100000:
            print(f"  [{i},{i+1}]: {val:6d} (0x{known_data[i]:02X}{known_data[i+1]:02X})")

    print()
    print("="*70)
    print("按 Ctrl+C 停止监听，同时观察串口数据")
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

        print("\n✓ 串口已连接，等待实时数据...\n")

        buffer = b''
        count = 0

        while count < 20:  # 只显示20个数据包
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]
                    count += 1

                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                    print(f"\n[{timestamp}] 数据包 #{count}:")
                    print(f"  HEX: {packet.hex()}")

                    # 只显示前20字节
                    print(f"  前20字节: {packet[:20].hex()}")

                    break

            time.sleep(0.01)

        ser.close()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n停止监听")
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    analyze_with_known_data()
