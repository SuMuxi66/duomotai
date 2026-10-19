import serial
import time
import re


class WeightReader:
    def __init__(self, port='COM3', baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None

    def connect(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=self.timeout
            )
            print(f"成功连接到 {self.port} @ {self.baudrate} bps")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    def parse_weight(self, line):
        patterns = [
            (r'原始值[:]\s*(\d+)\s*\|\s*真实重量[:]\s*(\d+\.?\d*)\s*g', 'g'),
            (r'原始值[:]\s*(\d+)\s*\|\s*真实重量[:]\s*g', 'g'),
            (r'W=(\d+\.?\d*)', 'g'),
            (r'w=(\d+\.?\d*)', 'g'),
            (r'weight=(\d+\.?\d*)', 'g'),
            (r'(\d+\.?\d*)\s*g', 'g'),
            (r'(\d+\.?\d*)\s*kg', 'kg'),
            (r'(\d+\.?\d*)', None),
        ]
        for pattern, unit in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                if len(match.groups()) >= 1:
                    if len(match.groups()) == 2:
                        weight_str = match.group(2)
                        if weight_str:
                            value = float(weight_str)
                            return value, unit if unit else ''
                    raw_value = match.group(1)
                    if raw_value:
                        value = float(raw_value)
                        return value, unit if unit else ''
        return None, None

    def read_once(self):
        if not self.ser or not self.ser.is_open:
            return None, None
        try:
            if self.ser.in_waiting:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    weight, unit = self.parse_weight(line)
                    return line, (weight, unit)
        except Exception as e:
            print(f"读取错误: {e}")
        return None, None

    def run(self, callback=None):
        if not self.connect():
            return
        print("开始接收称重数据... 按 Ctrl+C 停止")
        print("-" * 50)
        try:
            while True:
                raw, parsed = self.read_once()
                if raw:
                    if parsed[0] is not None:
                        unit = parsed[1] if parsed[1] else ''
                        print(f"原始数据: {raw}")
                        print(f"解析结果: {parsed[0]} {unit}")
                        print("-" * 50)
                        if callback:
                            callback(parsed[0], unit, raw)
                    else:
                        print(f"原始数据: {raw} (未解析)")
                        print("-" * 50)
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n停止接收")
        finally:
            self.close()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("串口已关闭")


def find_available_ports():
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    print("发现的可用串口:")
    for port in ports:
        print(f"  {port.device} - {port.description}")
    return [port.device for port in ports]

def test_port(port, baudrates=[9600, 115200], test_duration=3):
    for baudrate in baudrates:
        print(f"  测试 {port} @ {baudrate} bps...")
        config = {
            'port': port,
            'baudrate': baudrate,
            'timeout': 1
        }
        reader = WeightReader(**config)
        if not reader.connect():
            continue
        
        start_time = time.time()
        received_data = False
        while time.time() - start_time < test_duration:
            raw, parsed = reader.read_once()
            if raw:
                received_data = True
                print(f"  收到数据: {raw}")
                break
            time.sleep(0.1)
        
        if received_data:
            reader.close()
            return True, reader, baudrate
        
        reader.close()
    return False, None, None

def main():
    ports = find_available_ports()
    print()
    
    if not ports:
        print("未找到可用串口！请检查设备连接。")
        return
    
    priority_ports = ['COM5', 'COM6']
    all_ports = list(ports)
    for p in priority_ports:
        if p in all_ports:
            all_ports.remove(p)
            all_ports.insert(0, p)
    
    print(f"将按以下顺序尝试: {all_ports}")
    print("-" * 50)
    
    working_reader = None
    found_port = None
    found_baudrate = None
    
    for port in all_ports:
        print(f"正在尝试 {port}...")
        received, reader, baudrate = test_port(port, baudrates=[9600, 115200], test_duration=3)
        if received:
            found_port = port
            working_reader = reader
            found_baudrate = baudrate
            print(f"  [OK] {port} @ {baudrate} bps 有数据!")
            print()
            break
        else:
            print(f"  [X] {port} 无数据")
        print()
    
    if found_port and working_reader and found_baudrate:
        print(f"成功找到设备: {found_port} @ {found_baudrate} bps")
        print("开始持续接收数据... 按 Ctrl+C 停止")
        print("-" * 50)
        
        working_reader.port = found_port
        working_reader.baudrate = found_baudrate
        if working_reader.connect():
            try:
                while True:
                    raw, parsed = working_reader.read_once()
                    if raw:
                        if parsed[0] is not None:
                            unit = parsed[1] if parsed[1] else ''
                            print(f"原始数据: {raw}")
                            print(f"解析结果: {parsed[0]} {unit}")
                            print("-" * 50)
                        else:
                            print(f"原始数据: {raw} (未解析)")
                            print("-" * 50)
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n停止接收")
            finally:
                working_reader.close()
    else:
        print("未在任何串口找到数据！请检查：")
        print("  1. 设备是否已通电（测试板电源灯是否亮）")
        print("  2. Tx/Rx 是否接反（测试板 TX → TTL RX，TTL TX → 测试板 RX）")
        print("  3. 尝试交换 Tx/Rx 接线后重试")


if __name__ == '__main__':
    main()
