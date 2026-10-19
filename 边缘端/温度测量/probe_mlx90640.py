import serial
import time
import struct
from datetime import datetime

def probe_and_read_mlx90640(port='COM6'):
    baudrates = [115200, 9600, 460800]

    for baudrate in baudrates:
        print(f"\n{'='*60}")
        print(f"尝试波特率: {baudrate} bps")
        print(f"{'='*60}")

        try:
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=2
            )

            ser.reset_input_buffer()
            time.sleep(0.3)

            print("发送查询模式命令...")
            ser.write(bytes([0xA5, 0x35, 0x01, 0xDB]))
            time.sleep(0.5)

            print("等待数据...")
            start_time = time.time()
            received_data = b''

            while time.time() - start_time < 3:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    received_data += data
                    if len(received_data) >= 100:
                        break

            ser.close()

            if received_data:
                print(f"收到 {len(received_data)} 字节")
                print(f"前 32 字节: {received_data[:32].hex()}")

                if received_data[0:2] == b'\x5A\x5A':
                    print("✓ 检测到 MLX90640 帧头!")

                    frame_len = received_data[2] + (received_data[3] << 8)
                    print(f"数据量字段: {frame_len} (0x{received_data[2]:02X} 0x{received_data[3]:02X})")

                    if frame_len == 1538 or frame_len == 1544:
                        print("✓ 正确的 MLX90640 帧长度!")
                        return baudrate, received_data

            else:
                print("未收到数据")

        except Exception as e:
            print(f"错误: {e}")

    return None, None

def parse_and_display(data, baudrate):
    if not data or len(data) < 1544:
        print("数据不足以解析")
        return

    print(f"\n{'='*60}")
    print(f"解析数据 (波特率: {baudrate})")
    print(f"{'='*60}")

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
    print(f"  视场平均温度: {sum(temps)/len(temps):.2f} °C")
    print(f"  最高温度: {max(temps):.2f} °C")
    print(f"  最低温度: {min(temps):.2f} °C")
    print(f"  传感器温度: {sensor_temp:.2f} °C")

    print(f"\n温度矩阵 (8x8 采样):")
    step = len(temps) // 64
    for row in range(8):
        row_temps = []
        for col in range(8):
            idx = (row * 8 + col) * step
            if idx < len(temps):
                row_temps.append(f"{temps[idx]:5.1f}")
        print("    " + " ".join(row_temps))

    return temps

if __name__ == "__main__":
    baudrate, data = probe_and_read_mlx90640('COM6')

    if baudrate and data:
        temps = parse_and_display(data, baudrate)

        print(f"\n{'='*60}")
        print("传感器识别成功!")
        print(f"推荐配置: port='COM6', baudrate={baudrate}")
        print(f"{'='*60}")
    else:
        print("\n" + "="*60)
        print("无法识别 MLX90640 传感器")
        print("请检查:")
        print("1. 传感器是否已正确上电")
        print("2. 传感器TX/RX接线是否正确")
        print("3. 尝试交换传感器的TX和RX接线")
        print("="*60)
