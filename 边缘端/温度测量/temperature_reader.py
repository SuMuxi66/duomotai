import serial
import time
import struct
from datetime import datetime

class MLX90640Sensor:
    def __init__(self, port='COM6', baudrate=115200, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_port = None
        self.frame_length = 1544

    def connect(self):
        try:
            self.serial_port = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self.timeout
            )
            print(f"成功连接到 {self.port} @ {self.baudrate} bps")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            print("串口已关闭")

    def send_command(self, cmd):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.write(cmd)
            time.sleep(0.2)

    def set_auto_mode(self):
        cmd = bytes([0xA5, 0x35, 0x02, 0xDC])
        self.send_command(cmd)
        print("已设置为自动输出模式")

    def set_query_mode(self):
        cmd = bytes([0xA5, 0x35, 0x01, 0xDB])
        self.send_command(cmd)
        print("已设置为查询模式")

    def set_baudrate_115200(self):
        cmd = bytes([0xA5, 0x15, 0x02, 0xBC])
        self.send_command(cmd)
        print("波特率已设置为 115200")

    def set_refresh_rate(self, hz):
        rate_cmd = {0.5: (0x00, 0xCA), 1: (0x01, 0xCB), 2: (0x02, 0xCC), 4: (0x03, 0xCD), 8: (0x04, 0xCE)}
        if hz in rate_cmd:
            cmd = bytes([0xA5, 0x25, rate_cmd[hz][0], rate_cmd[hz][1]])
            self.send_command(cmd)
            print(f"刷新率已设置为 {hz} Hz")

    def save_settings(self):
        cmd = bytes([0xA5, 0x65, 0x01, 0x0B])
        self.send_command(cmd)
        print("设置已保存")

    def parse_frame(self, data):
        if len(data) < self.frame_length:
            return None

        if data[0] != 0x5A or data[1] != 0x5A:
            return None

        temps = []
        for i in range(768):
            idx = 4 + i * 2
            if idx + 1 < len(data):
                temp_raw = (data[idx + 1] << 8) | data[idx]
                temp = temp_raw / 100.0
                temps.append(temp)

        sensor_temp_idx = 4 + 768 * 2
        if sensor_temp_idx + 1 < len(data):
            sensor_temp_raw = (data[sensor_temp_idx + 1] << 8) | data[sensor_temp_idx]
            sensor_temp = sensor_temp_raw / 100.0
        else:
            sensor_temp = None

        return {'pixels': temps, 'sensor_temp': sensor_temp}

    def read_frame(self):
        if not self.serial_port or not self.serial_port.is_open:
            return None

        try:
            if self.serial_port.in_waiting >= self.frame_length:
                data = self.serial_port.read(self.frame_length)
                return self.parse_frame(data)
        except Exception as e:
            print(f"读取错误: {e}")
        return None

    def read_query(self):
        cmd = bytes([0xA5, 0x35, 0x01, 0xDB])
        self.send_command(cmd)
        return self.read_frame()

def main():
    sensor = MLX90640Sensor(port='COM6', baudrate=115200)

    if not sensor.connect():
        return

    sensor.set_auto_mode()
    sensor.set_refresh_rate(2)
    sensor.save_settings()

    time.sleep(0.5)

    print("\n开始实时读取温度数据 (按 Ctrl+C 停止)...")
    print("-" * 60)

    try:
        while True:
            result = sensor.read_frame()

            if result and result['pixels']:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                temps = result['pixels']

                min_temp = min(temps)
                max_temp = max(temps)
                avg_temp = sum(temps) / len(temps)

                print(f"\n[{timestamp}]")
                print(f"  视场平均温度: {avg_temp:.2f} °C")
                print(f"  最高温度: {max_temp:.2f} °C")
                print(f"  最低温度: {min_temp:.2f} °C")

                if result['sensor_temp'] is not None:
                    print(f"  传感器温度: {result['sensor_temp']:.2f} °C")

                print("  温度矩阵 (8x8 采样):")
                step = len(temps) // 64
                for row in range(8):
                    row_temps = []
                    for col in range(8):
                        idx = (row * 8 + col) * step
                        if idx < len(temps):
                            row_temps.append(f"{temps[idx]:5.1f}")
                    print("    " + " ".join(row_temps))

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n停止读取")
    finally:
        sensor.disconnect()

if __name__ == "__main__":
    main()
