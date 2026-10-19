import serial
import time
from datetime import datetime

def collect_samples(label, samples=20, ser=None):
    """收集样本"""
    should_close = False
    if ser is None:
        ser = serial.Serial(
            port='COM13',
            baudrate=2400,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=None
        )
        should_close = True

    print(f"\n收集 {label} 数据 ({samples} 个样本)...")

    buffer = b''
    packets = []

    while len(packets) < samples:
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer += data

            idx = buffer.find(b'\x28')
            if idx != -1 and idx + 50 <= len(buffer):
                packet = buffer[idx:idx+50]
                buffer = buffer[idx+50:]

                packets.append(list(packet))

                if len(packets) % 5 == 0:
                    print(f"  {len(packets)}/{samples}...", end="", flush=True)

                time.sleep(0.3)

    if should_close:
        ser.close()

    return packets, ser

def analyze(empty, loaded):
    """分析最佳位置"""
    print(f"\n\n{'='*70}")
    print("分析结果:")
    print(f"{'='*70}")
    print(f"\n空载样本: {len(empty)}")
    print(f"50g样本: {len(loaded)}")

    print(f"\n{'位置':<10} {'空载均值':<12} {'50g均值':<12} {'差值':<12} {'系数':<12} {'50g计算值':<12}")
    print("-" * 70)

    results = []

    for pos in range(48):
        empty_vals = [(p[pos] << 8) | p[pos+1] for p in empty if pos < 49]
        loaded_vals = [(p[pos] << 8) | p[pos+1] for p in loaded if pos < 49]

        if empty_vals and loaded_vals:
            empty_avg = sum(empty_vals) / len(empty_vals)
            loaded_avg = sum(loaded_vals) / len(loaded_vals)
            diff = loaded_avg - empty_avg

            if diff > 100:  # 只显示差值>100的位置
                factor = diff / 50.0
                calc_50g = diff / factor if factor > 0 else 0

                results.append((pos, empty_avg, loaded_avg, diff, factor))

                print(f"[{pos},{pos+1}]     {empty_avg:<12.1f} {loaded_avg:<12.1f} {diff:<12.1f} {factor:<12.2f} {calc_50g:<12.1f}")

    # 排序找出最佳位置
    if results:
        results.sort(key=lambda x: abs(x[3] / x[4] - 50), reverse=False)  # 按误差排序

        print(f"\n{'='*70}")
        print("最佳候选位置 (按50g计算误差排序):")
        print("="*70)

        for i, (pos, empty_avg, loaded_avg, diff, factor) in enumerate(results[:5]):
            calc = diff / factor
            error = abs(calc - 50)
            print(f"\n候选 {i+1}: 位置[{pos},{pos+1}]")
            print(f"  空载均值: {empty_avg:.1f}")
            print(f"  50g均值:  {loaded_avg:.1f}")
            print(f"  差值:     {diff:.1f}")
            print(f"  系数:     {factor:.2f}")
            print(f"  计算50g:  {calc:.1f}g (误差: {error:.1f}g)")

    return results

def main():
    print("="*70)
    print("快速校准程序")
    print("="*70)

    ser = serial.Serial(
        port='COM13',
        baudrate=2400,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=None
    )

    print("\n第一步: 收集空载数据")
    print("请确保秤上无负载，然后按 Enter...")
    input()

    empty, ser = collect_samples("空载", 20, ser)

    print("\n\n第二步: 收集50g数据")
    print("请放上50g砝码，然后按 Enter...")
    input()

    loaded, ser = collect_samples("50g", 20, ser)

    ser.close()

    results = analyze(empty, loaded)

    if results:
        best = results[0]
        pos = best[0]
        factor = best[4]

        print(f"\n\n{'='*70}")
        print("最终配置:")
        print("="*70)
        print(f"  位置: [{pos}, {pos+1}]")
        print(f"  换算系数: {factor:.2f}")
        print(f"\n  公式: weight = ((packet[{pos}] << 8) | packet[{pos+1}]) / {factor:.2f}")
        print(f"  (如果需要零点校准: weight = ((packet[{pos}] << 8) | packet[{pos+1}]) - zero) / {factor:.2f}")

if __name__ == "__main__":
    main()
