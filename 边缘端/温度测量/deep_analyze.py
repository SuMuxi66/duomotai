import serial
import time
from datetime import datetime

def deep_analyze():
    print("="*70)
    print("深度数据分析 - 对比空载和50g的数据")
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

        print("\n请先让秤空载，等待收集10组数据...\n")
        buffer = b''
        empty_samples = []

        while len(empty_samples) < 10:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]

                    empty_samples.append({
                        'raw_hex': packet.hex(),
                        'bytes': list(packet)
                    })
                    print(f"空载[{len(empty_samples)}]: {packet.hex()[:20]}... (显示前20字节)")
                    time.sleep(0.5)

        print("\n" + "="*70)
        print("现在放上50g砝码，等待收集10组数据...\n")
        loaded_samples = []

        while len(loaded_samples) < 10:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]

                    loaded_samples.append({
                        'raw_hex': packet.hex(),
                        'bytes': list(packet)
                    })
                    print(f"50g[{len(loaded_samples)}]: {packet.hex()[:20]}... (显示前20字节)")
                    time.sleep(0.5)

        # 详细对比分析
        print("\n" + "="*70)
        print("详细对比分析 (只显示有变化的字节位置):")
        print("="*70)

        for pos in range(48):
            empty_vals = [s['bytes'][pos] for s in empty_samples if pos < len(s['bytes'])]
            loaded_vals = [s['bytes'][pos] for s in loaded_samples if pos < len(s['bytes'])]

            if empty_vals and loaded_vals:
                empty_avg = sum(empty_vals) / len(empty_vals)
                loaded_avg = sum(loaded_vals) / len(loaded_vals)

                # 计算相对于空载的变化
                change = loaded_avg - empty_avg
                change_percent = (change / empty_avg * 100) if empty_avg > 0 else 0

                # 只显示有显著变化的位置
                if abs(change) > 50:  # 变化超过50
                    # 尝试计算重量
                    if change > 0:
                        weight_per_unit = change / 50
                        print(f"\n位置[{pos},{pos+1}] (BE):")
                        print(f"  空载值: {empty_avg:.1f} (0x{int(empty_avg):04X})")
                        print(f"  50g值:  {loaded_avg:.1f} (0x{int(loaded_avg):04X})")
                        print(f"  变化量: +{change:.1f}")
                        print(f"  变化率: {change_percent:.1f}%")
                        print(f"  推算: {change:.1f}/50 = {weight_per_unit:.2f} per gram")

        ser.close()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    deep_analyze()
