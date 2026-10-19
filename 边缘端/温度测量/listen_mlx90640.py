import serial
import time
from datetime import datetime

def listen_all_configs(port='COM6', listen_duration=5):
    configs = [
        {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 460800, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 19200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 57600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
    ]

    print(f"监听 {port} 端口 {listen_duration} 秒/配置...")
    print("=" * 70)

    for i, config in enumerate(configs):
        config_str = f"{config['baudrate']}-{config['bytesize']}{config['parity']}{config['stopbits']}"
        print(f"\n[{i+1}/{len(configs)}] 配置: {config_str}")

        try:
            ser = serial.Serial(port=port, timeout=0.5, **config)

            ser.reset_input_buffer()
            time.sleep(0.2)

            print("  等待数据...", end="", flush=True)

            all_data = b''
            start = time.time()

            while time.time() - start < listen_duration:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    all_data += data
                    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                    print(f"\n  [{ts}] 收到 {len(data)} 字节: {data.hex()}")
                    if len(data) < 50:
                        try:
                            print(f"    ASCII: {data.decode('utf-8', errors='ignore')}")
                        except:
                            pass
                time.sleep(0.1)

            ser.close()

            if all_data:
                print(f"  ✓ 在此配置下检测到数据! 共 {len(all_data)} 字节")

                if all_data[0:2] == b'\x5A\x5A':
                    print("    → 检测到 MLX90640 帧头 (0x5A5A)")

                frame_len = len(all_data)
                if frame_len == 1544:
                    print("    → 帧长度 1544 字节，符合 MLX90640 协议")
                elif frame_len > 100:
                    print(f"    → 帧长度 {frame_len} 字节")

                return config, all_data
            else:
                print(" 无数据")

        except Exception as e:
            print(f"  错误: {e}")

    print("\n" + "=" * 70)
    print("未检测到任何数据输出")
    print("\n可能原因:")
    print("1. 传感器需要特定触发才会输出数据")
    print("2. TX/RX 接线可能接反了（尝试交换）")
    print("3. 传感器未上电或供电不足")
    print("4. 传感器不是 MLX90640 串口版本")
    print("\n请确认:")
    print("- 传感器的 TX 引脚连接到 COM6 的 RX")
    print("- 传感器的 RX 引脚连接到 COM6 的 TX")
    print("- 传感器供电电压是否正确 (通常 3.3V 或 5V)")

    return None, None

def parse_mlx90640_frame(data):
    if len(data) < 1544:
        return None

    print(f"\n解析 MLX90640 数据帧 ({len(data)} 字节)")

    temps = []
    for i in range(768):
        idx = 4 + i * 2
        if idx + 1 < len(data):
            temp_raw = (data[idx + 1] << 8) | data[idx]
            temp = temp_raw / 100.0
            temps.append(temp)

    sensor_temp_idx = 4 + 768 * 2
    sensor_temp_raw = (data[sensor_temp_idx + 1] << 8) | data[sensor_temp_idx]
    sensor_temp = sensor_temp_raw / 100.0

    print(f"\n温度统计:")
    print(f"  视场平均: {sum(temps)/len(temps):.2f} °C")
    print(f"  最高温度: {max(temps):.2f} °C")
    print(f"  最低温度: {min(temps):.2f} °C")
    print(f"  传感器温度: {sensor_temp:.2f} °C")

    print(f"\n8x8 温度矩阵采样:")
    step = len(temps) // 64
    for row in range(8):
        row_temps = []
        for col in range(8):
            idx = (row * 8 + col) * step
            if idx < len(temps):
                row_temps.append(f"{temps[idx]:5.1f}")
        print("  " + " ".join(row_temps))

    return temps

if __name__ == "__main__":
    print("MLX90640 传感器探测程序")
    print("=" * 70)

    config, data = listen_all_configs('COM15', listen_duration=5)

    if config and data:
        if data[0:2] == b'\x5A\x5A' and len(data) >= 1544:
            parse_mlx90640_frame(data[:1544])

        print("\n" + "=" * 70)
        print("传感器识别成功!")
        print(f"推荐配置: port='COM6', baudrate={config['baudrate']}")
        print("=" * 70)
