import serial
import time
from datetime import datetime

def analyze_weight_position():
    print("="*70)
    print("重量位置自动分析程序")
    print("="*70)
    print("当前状态: 请先让秤空载，然后放上50g砝码")
    print("波特率: 2400 bps")
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
        empty_readings = []
        loaded_readings = []

        print("收集空载数据 (前20组)...")
        print("-"*70)

        while len(empty_readings) < 20:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                while True:
                    idx = buffer.find(b'\x28')
                    if idx == -1:
                        buffer = buffer[-5:] if len(buffer) > 5 else b''
                        break

                    if idx + 50 <= len(buffer):
                        packet = buffer[idx:idx+50]
                        buffer = buffer[idx+50:]

                        # 提取所有可能的2字节位置的值
                        values = []
                        for offset in range(len(packet) - 1):
                            val = (packet[offset] << 8) | packet[offset+1]
                            values.append(val)

                        empty_readings.append(values)

                        if len(empty_readings) % 5 == 0:
                            print(f"  已收集 {len(empty_readings)} 组...")

                        break
                    elif idx + 10 <= len(buffer):
                        buffer = buffer[idx:]
                        break
                    else:
                        buffer = buffer[idx:]
                        break

            time.sleep(0.01)

        print(f"\n✓ 空载数据收集完成!")
        print("\n" + "="*70)
        print("现在请放上50g砝码...")
        print("="*70 + "\n")

        print("收集50g砝码数据 (前20组)...")

        while len(loaded_readings) < 20:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                while True:
                    idx = buffer.find(b'\x28')
                    if idx == -1:
                        buffer = buffer[-5:] if len(buffer) > 5 else b''
                        break

                    if idx + 50 <= len(buffer):
                        packet = buffer[idx:idx+50]
                        buffer = buffer[idx+50:]

                        values = []
                        for offset in range(len(packet) - 1):
                            val = (packet[offset] << 8) | packet[offset+1]
                            values.append(val)

                        loaded_readings.append(values)

                        if len(loaded_readings) % 5 == 0:
                            print(f"  已收集 {len(loaded_readings)} 组...")

                        break
                    elif idx + 10 <= len(buffer):
                        buffer = buffer[idx:]
                        break
                    else:
                        buffer = buffer[idx:]
                        break

            time.sleep(0.01)

        print(f"\n✓ 50g数据收集完成!")

        # 分析哪个位置的数值变化最明显
        print("\n" + "="*70)
        print("分析结果:")
        print("="*70)
        print(f"\n{'位置':<8} {'空载平均':<12} {'50g平均':<12} {'差值':<12} {'差值/50':<12}")
        print("-" * 60)

        best_position = None
        best_diff = 0

        for offset in range(48):
            empty_vals = [r[offset] for r in empty_readings if offset < len(r)]
            loaded_vals = [r[offset] for r in loaded_readings if offset < len(r)]

            if empty_vals and loaded_vals:
                empty_avg = sum(empty_vals) / len(empty_vals)
                loaded_avg = sum(loaded_vals) / len(loaded_vals)
                diff = loaded_avg - empty_avg
                diff_per_gram = diff / 50 if diff > 0 else 0

                if diff > 1000:  # 差值大于1000认为是重量相关的位置
                    print(f"[{offset},{offset+1}]: {empty_avg:<12.1f} {loaded_avg:<12.1f} {diff:<12.1f} {diff_per_gram:<12.2f}")

                    if diff > best_diff:
                        best_diff = diff
                        best_position = offset

        if best_position is not None:
            print(f"\n" + "="*70)
            print(f"✓ 最佳位置: [{best_position}, {best_position+1}]")
            print(f"✓ 差值: {best_diff:.1f}")
            print(f"✓ 每个重量单位: {best_diff/50:.2f}")
            print(f"\n建议在代码中使用:")
            print(f"  weight = (packet[{best_position}] << 8) | packet[{best_position+1}]")
            print(f"  weight_grams = weight / {best_diff/50:.2f}")
            print("="*70)

        ser.close()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n程序已停止")
        if 'ser' in locals():
            ser.close()
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    analyze_weight_position()
