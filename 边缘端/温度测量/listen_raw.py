import serial
import time
from datetime import datetime

def listen_raw(port='COM6', duration=10):
    print(f"监听 {port} 原始数据 {duration} 秒...")
    print("按 Ctrl+C 提前停止")
    print("-" * 70)

    configs = [
        {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 9600, 'bytesize': 8, 'parity': 'E', 'stopbits': 1},
        {'baudrate': 9600, 'bytesize': 8, 'parity': 'O', 'stopbits': 1},
        {'baudrate': 4800, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 19200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
        {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1},
    ]

    for i, config in enumerate(configs):
        print(f"\n配置 {i+1}: {config['baudrate']}-{config['bytesize']}{config['parity']}{config['stopbits']}")
        try:
            ser = serial.Serial(port=port, timeout=1, **config)
            ser.reset_input_buffer()

            start = time.time()
            data_list = []

            while time.time() - start < 2:
                if ser.in_waiting:
                    raw = ser.read(ser.in_waiting)
                    if raw:
                        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                        print(f"  [{ts}] {len(raw)} bytes: {raw.hex()} | ASCII: {raw}")
                        data_list.append(raw)

            ser.close()

            if data_list:
                print(f"  ✓ 在此配置下检测到数据!")
                return config, b''.join(data_list)

        except Exception as e:
            print(f"  错误: {e}")

    print("\n" + "=" * 70)
    print("未检测到任何数据。可能原因:")
    print("1. 传感器需要接收特定命令才发送数据")
    print("2. 传感器需要硬件触发才输出数据")
    print("3. 传感器连接有问题")
    print("\n请提供传感器品牌型号，我可以帮你查询具体协议")

    return None, None

if __name__ == "__main__":
    try:
        config, data = listen_raw('COM6', 15)
    except KeyboardInterrupt:
        print("\n监听已停止")
