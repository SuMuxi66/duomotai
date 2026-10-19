import serial
import time
import re
from collections import deque

zero_value = None
calibration_factor = 205.0
filter_size = 8
dead_zone = 300

def main():
    global zero_value
    
    ports = ['COM6']
    
    for port in ports:
        print(f"尝试连接 {port} @ 9600 bps...")
        try:
            ser = serial.Serial(
                port=port,
                baudrate=9600,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1
            )
            print(f"成功连接到 {port}")
            print("-" * 50)
            print("提示：")
            print("  - 程序会先记录零点（空秤状态）")
            print("  - 零点记录完成后请放物体")
            print(f"  - 轻滤波 (窗口: {filter_size})，响应快且稳定")
            print("  - 按 Ctrl+C 停止")
            print("-" * 50)
            
            print("\n正在记录零点... 请确保秤上没有物体")
            time.sleep(2)
            
            zero_samples = []
            for i in range(10):
                if ser.in_waiting:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        match = re.search(r'原始值[:]\s*(\d+)\s*\|', line)
                        if match:
                            val = int(match.group(1))
                            zero_samples.append(val)
                            print(f"  采样 {i+1}/10: {val}")
                time.sleep(0.2)
            
            if zero_samples:
                zero_value = sum(zero_samples) / len(zero_samples)
                print(f"\n零点已记录: {zero_value:.0f}")
            else:
                print("\n无法记录零点，使用默认值")
                zero_value = 8226000
            
            print(f"\n开始接收数据 (滤波窗口: {filter_size}, 死区: ±{dead_zone})...")
            print("-" * 50)
            
            weight_queue = deque(maxlen=filter_size)
            
            try:
                while True:
                    if ser.in_waiting:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            match = re.search(r'原始值[:]\s*(\d+)\s*\|', line)
                            if match:
                                raw_value = int(match.group(1))
                                if zero_value:
                                    delta = raw_value - zero_value
                                    
                                    if abs(delta) < dead_zone:
                                        weight = 0.0
                                    else:
                                        weight = delta / calibration_factor
                                        if weight < 0:
                                            weight = 0
                                    
                                    weight_queue.append(weight)
                                    avg_weight = sum(weight_queue) / len(weight_queue)
                                    
                                    if avg_weight < 50:
                                        approx_weight = round(avg_weight)
                                    elif avg_weight < 100:
                                        approx_weight = round(avg_weight / 5) * 5
                                    else:
                                        approx_weight = round(avg_weight / 10) * 10
                                    
                                    print(f"原始值: {raw_value} | 差值: {delta:.0f} | 重量: {avg_weight:.1f} g | 约值: {approx_weight} g")
                                else:
                                    print(f"原始值: {raw_value}")
                            else:
                                print(f"原始数据: {line}")
                    time.sleep(0.05)
            except KeyboardInterrupt:
                print("\n停止接收")
            finally:
                ser.close()
                print("串口已关闭")
            break
        except Exception as e:
            print(f"连接失败: {e}")
            print()

if __name__ == '__main__':
    main()
