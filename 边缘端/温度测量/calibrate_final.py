import serial
import time
from datetime import datetime
import json

def collect_data(samples=30):
    """收集数据"""
    print(f"收集 {samples} 个样本...\n")

    ser = serial.Serial(
        port='COM13',
        baudrate=2400,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=None
    )

    buffer = b''
    packets = []

    start_time = time.time()
    while len(packets) < samples and time.time() - start_time < 60:
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer += data

            idx = buffer.find(b'\x28')
            if idx != -1 and idx + 50 <= len(buffer):
                packet = buffer[idx:idx+50]
                buffer = buffer[idx+50:]

                packets.append({
                    'hex': packet.hex(),
                    'bytes': list(packet),
                    'time': datetime.now().strftime('%H:%M:%S.%f')
                })

                if len(packets) % 10 == 0:
                    print(f"  已收集 {len(packets)}/{samples}...")

                time.sleep(0.3)

    ser.close()
    return packets

def analyze(packets1, label1, packets2, label2):
    """对比分析两组数据"""
    print(f"\n{'='*70}")
    print(f"对比分析: {label1} vs {label2}")
    print(f"{'='*70}\n")

    print(f"{label1} 样本数: {len(packets1)}")
    print(f"{label2} 样本数: {len(packets2)}\n")

    print(f"分析位置变化 (高字节在前):")
    print(f"{'位置':<12} {'空载平均':<12} {'50g平均':<12} {'差值':<12} {'每克变化':<12}")
    print("-" * 60)

    best_positions = []

    for pos in range(48):
        vals1 = [p['bytes'][pos] << 8 | p['bytes'][pos+1] for p in packets1 if pos < len(p['bytes'])-1]
        vals2 = [p['bytes'][pos] << 8 | p['bytes'][pos+1] for p in packets2 if pos < len(p['bytes'])-1]

        if vals1 and vals2:
            avg1 = sum(vals1) / len(vals1)
            avg2 = sum(vals2) / len(vals2)
            diff = avg2 - avg1

            if abs(diff) > 100:  # 只显示变化超过100的位置
                per_gram = diff / 50 if diff > 0 else 0
                best_positions.append((pos, avg1, avg2, diff, per_gram))
                print(f"[{pos},{pos+1}]:      {avg1:<12.1f} {avg2:<12.1f} {diff:<12.1f} {per_gram:<12.2f}")

    if best_positions:
        # 找最稳定的位置
        print(f"\n{'='*70}")
        print("最可能的位置 (按差值排序):")
        print("="*70)

        best_positions.sort(key=lambda x: abs(x[3]), reverse=True)

        for i, (pos, avg1, avg2, diff, per_gram) in enumerate(best_positions[:5]):
            print(f"\n候选 {i+1}: 位置[{pos},{pos+1}]")
            print(f"  零点值: {avg1:.1f}")
            print(f"  50g值:  {avg2:.1f}")
            print(f"  差值:   {diff:.1f}")
            print(f"  换算系数: {per_gram:.2f} (即重量 = (RAW - 零点) / {per_gram:.2f})")

            # 计算用这个系数的50g时的误差
            if per_gram > 0:
                calc_weight = diff / per_gram
                print(f"  验证: {diff:.1f} / {per_gram:.2f} = {calc_weight:.1f}g (目标50g, 误差{abs(calc_weight-50):.1f}g)")

    return best_positions

def main():
    print("="*70)
    print("重量传感器校准程序")
    print("="*70)

    # 第一步：收集空载数据
    print("\n" + "="*70)
    print("第一步: 收集空载数据")
    print("="*70)
    print("\n请确保秤上没有任何负载!\n")
    input("准备就绪后按 Enter 键开始...")

    print()
    empty_packets = collect_data(30)

    # 第二步：收集50g数据
    print("\n" + "="*70)
    print("第二步: 收集50g数据")
    print("="*70)
    print("\n请放上50g砝码!\n")
    input("准备就绪后按 Enter 键开始...")

    print()
    loaded_packets = collect_data(30)

    # 第三步：分析
    print("\n" + "="*70)
    print("第三步: 分析数据")
    print("="*70)

    best = analyze(empty_packets, "空载", loaded_packets, "50g")

    if best:
        print("\n" + "="*70)
        print("建议配置:")
        print("="*70)
        pos = best[0][0]
        per_gram = best[0][4]
        zero = best[0][1]

        print(f"\n零点偏移: {zero:.1f}")
        print(f"换算系数: {per_gram:.2f}")
        print(f"\n在代码中使用:")
        print(f"  zero_offset = {zero:.1f}")
        print(f"  scale_factor = {per_gram:.2f}")
        print(f"  weight = (raw_value - zero_offset) / scale_factor")

        # 保存配置
        config = {
            'zero_offset': zero,
            'scale_factor': per_gram,
            'position': pos
        }
        with open('weight_config.json', 'w') as f:
            json.dump(config, f, indent=2)
        print(f"\n配置已保存到 weight_config.json")

if __name__ == "__main__":
    main()
