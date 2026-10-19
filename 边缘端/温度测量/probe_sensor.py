import serial
import time
import struct
from datetime import datetime

BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

def probe_sensor(port='COM6'):
    print(f"开始探测传感器协议 on {port}...")
    print("-" * 60)

    for baudrate in BAUDRATES:
        print(f"\n尝试波特率: {baudrate} bps")
        try:
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=2
            )
            time.sleep(0.5)

            ser.reset_input_buffer()
            ser.write(b'\xFF\x01\x03\x00\x00\x00\x01\x79\xD5')

            raw_data = ser.read(100)
            ser.close()

            if raw_data:
                print(f"  收到 {len(raw_data)} 字节: {raw_data.hex()}")

                if len(raw_data) == 9 and raw_data[0] == 0xFF:
                    temp = (raw_data[3] << 8 | raw_data[4]) / 10.0
                    print(f"  ✓ Modbus RTU 格式识别成功! 温度: {temp}°C")
                    return {'type': 'modbus', 'baudrate': baudrate, 'temperature': temp}

                elif len(raw_data) >= 4:
                    try:
                        temp = struct.unpack('>f', raw_data[:4])[0]
                        if -100 < temp < 200:
                            print(f"  ✓ 浮点数格式识别成功! 温度: {temp}°C")
                            return {'type': 'float', 'baudrate': baudrate, 'temperature': temp}
                    except:
                        pass

                    try:
                        temp = struct.unpack('<f', raw_data[:4])[0]
                        if -100 < temp < 200:
                            print(f"  ✓ 小端浮点数格式识别成功! 温度: {temp}°C")
                            return {'type': 'float_le', 'baudrate': baudrate, 'temperature': temp}
                    except:
                        pass

                text = raw_data.decode('utf-8', errors='ignore').strip()
                if text and any(c.isdigit() for c in text):
                    print(f"  ASCII 文本: {text}")

        except Exception as e:
            print(f"  错误: {e}")

    print("\n尝试 7 数据位 + 偶校验...")
    for baudrate in [9600, 19200]:
        try:
            ser = serial.Serial(port=port, baudrate=baudrate, bytesize=7, parity='E', stopbits=1, timeout=2)
            time.sleep(0.5)
            raw_data = ser.read(50)
            ser.close()

            if raw_data:
                print(f"  收到 {len(raw_data)} 字节: {raw_data.hex()}")
                text = raw_data.decode('utf-8', errors='ignore').strip()
                if text:
                    print(f"  文本: {text}")
                    import re
                    match = re.search(r'[-+]?\d+\.?\d*', text)
                    if match:
                        temp = float(match.group())
                        print(f"  ✓ 7E1 格式识别成功! 温度: {temp}°C")
                        return {'type': 'ascii_7e1', 'baudrate': baudrate, 'temperature': temp}
        except Exception as e:
            print(f"  错误: {e}")

    print("\n尝试发送查询命令...")
    commands = [
        (b'\x01\x03\x00\x00\x00\x02\xC4\x0B', "Modbus查询"),
        (b'RD\r\n', "文本命令"),
        (b'TEMP\r\n', "温度命令"),
        (b'\xFF\x01\x03', "自定义协议"),
    ]

    for cmd, name in commands:
        try:
            ser = serial.Serial(port='COM6', baudrate=9600, timeout=1)
            ser.write(cmd)
            time.sleep(0.5)
            raw = ser.read(50)
            ser.close()

            if raw and len(raw) > 0:
                print(f"  {name}: {raw.hex()} -> {raw}")
        except:
            pass

    return None

if __name__ == "__main__":
    result = probe_sensor('COM6')
    if result:
        print("\n" + "=" * 60)
        print(f"传感器识别成功!")
        print(f"协议类型: {result['type']}")
        print(f"波特率: {result['baudrate']}")
        print(f"当前温度: {result['temperature']}°C")
    else:
        print("\n无法识别传感器协议，请检查:")
        print("1. 传感器是否已正确连接到COM6")
        print("2. 传感器是否已上电")
        print("3. 传感器的产品手册中的通信协议")
