import serial
import serial.tools.list_ports
import time
import struct
from datetime import datetime

BAUDRATES = [115200, 9600, 460800, 19200, 57600, 38400, 2400, 1200]

CH340_VID = 0x1A86  # CH340/CH341 USB转串口：红外/称重/电磁阀三块板卡均为该芯片
CH340_PID = 0x7523


def is_bluetooth_port(port):
    """蓝牙虚拟串口：打开会长时间超时，扫描必须跳过"""
    desc = port.description or ''
    hwid = (getattr(port, 'hwid', '') or '').upper()
    return 'BTHENUM' in hwid or 'BLUETOOTH' in desc.upper() or '蓝牙' in desc


def is_ch340_port(port):
    try:
        return port.vid == CH340_VID and port.pid == CH340_PID
    except Exception:
        return False


def find_mlx90640_port():
    print("正在查找 MLX90640 传感器...")

    ports = [p for p in serial.tools.list_ports.comports() if not is_bluetooth_port(p)]
    # CH340 口优先（传感器就在 CH340 上），其余 USB 串口排后面兜底
    ports.sort(key=lambda p: not is_ch340_port(p))

    if not ports:
        print("未找到任何串口")
        return None, None

    print(f"检测到 {len(ports)} 个串口: {[p.device for p in ports]}")

    for port in ports:
        for baudrate in BAUDRATES:
            print(f"\n尝试 {port.device} @ {baudrate}...", end="", flush=True)

            try:
                ser = serial.Serial(
                    port=port.device,
                    baudrate=baudrate,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1
                )

                ser.reset_input_buffer()
                time.sleep(0.2)

                ser.write(bytes([0xA5, 0x35, 0x02, 0xDC]))
                time.sleep(0.5)

                if ser.in_waiting >= 100:
                    data = ser.read(ser.in_waiting)
                    if b'\x5A\x5A' in data[:10]:
                        ser.close()
                        print(f" ✓ 找到传感器!")
                        return port.device, baudrate

                ser.close()
                print(f" {baudrate}", end="")

            except Exception as e:
                print(f" {e}", end="")

    return None, None

class MLX90640Reader:
    def __init__(self, port, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.buffer = b''

    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=0.5
        )
        self.ser.reset_input_buffer()
        print(f"✓ 已连接到 {self.port} @ {self.baudrate} bps")

    def read_frame(self):
        while True:
            if self.ser.in_waiting:
                data = self.ser.read(self.ser.in_waiting)
                self.buffer += data

            while len(self.buffer) >= 1544:
                idx = self.buffer.find(b'\x5A\x5A')
                if idx == -1:
                    self.buffer = self.buffer[-2:]
                    break

                if len(self.buffer) >= idx + 1544:
                    frame = self.buffer[idx:idx+1544]
                    self.buffer = self.buffer[idx+1544:]
                    return frame

                break

            time.sleep(0.01)

    def parse_temperatures(self, frame):
        temps = []
        for i in range(768):
            offset = 4 + i * 2
            temp_raw = (frame[offset+1] << 8) | frame[offset]
            temp = temp_raw / 100.0
            temps.append(temp)

        sensor_temp_offset = 4 + 768 * 2
        sensor_temp_raw = (frame[sensor_temp_offset+1] << 8) | frame[sensor_temp_offset]
        sensor_temp = sensor_temp_raw / 100.0

        return temps, sensor_temp

    def display(self, temps, sensor_temp):
        avg_temp = sum(temps) / len(temps)
        max_temp = max(temps)
        min_temp = min(temps)
        center_temp = temps[len(temps)//2]
        max_pos = temps.index(max_temp)

        ts = datetime.now().strftime('%H:%M:%S')
        print(f"\r[{ts}] 目标: {center_temp:5.2f}°C | 平均: {avg_temp:5.2f}°C | 最高: {max_temp:5.2f}°C | 最低: {min_temp:5.2f}°C | 传感器: {sensor_temp:5.2f}°C", end='')

    def close(self):
        if self.ser:
            self.ser.close()

def main():
    port, baudrate = find_mlx90640_port()

    if not port:
        print("\n未找到 MLX90640 传感器，请检查:")
        print("1. 传感器是否已连接")
        print("2. USB转TTL模块是否正确连接")
        print("3. TX/RX接线是否正确")
        return

    reader = MLX90640Reader(port=port, baudrate=baudrate)

    try:
        reader.connect()
        print("正在读取温度数据... (按 Ctrl+C 停止)")
        print("=" * 80)

        while True:
            frame = reader.read_frame()
            if frame:
                temps, sensor_temp = reader.parse_temperatures(frame)
                reader.display(temps, sensor_temp)

    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print("程序已停止")
    except Exception as e:
        print(f"\n错误: {e}")
    finally:
        reader.close()

if __name__ == "__main__":
    main()
