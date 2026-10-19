import serial
import time
from datetime import datetime

BAUDRATES = [9600, 115200, 4800, 19200, 38400, 57600, 1200, 2400]

COMMANDS = [
    b'RD\r\n',
    b'READ\r\n',
    b'GET\r\n',
    b'WT\r\n',
    b'\x55\x55',
    b'\xFF\xFF',
    b'\xA5\x35',
    b'\x5A\xA5',
    b'\x5A\x5A',
    b'?',
    b'STATUS\r\n',
    b'UNIT\r\n',
    b'ZERO\r\n',
    b'CAL\r\n',
    b'DATA\r\n',
    b'VALUE\r\n',
    b'WEIGHT\r\n',
    b'MASS\r\n',
    b'G\r\n',
    b'KG\r\n',
    b'CR',
    b'\r\n',
    b'\x00\x00',
    b'\x01\x00',
]

def auto_scan():
    print("="*70)
    print("CH340 全自动扫描程序")
    print("="*70)
    print(f"尝试 {len(BAUDRATES)} 个波特率 x {len(COMMANDS)} 个命令")
    print(f"共 {len(BAUDRATES) * len(COMMANDS)} 种组合")
    print("-"*70)

    found_response = False

    for baudrate in BAUDRATES:
        print(f"\n{'='*70}")
        print(f"波特率: {baudrate}")
        print(f"{'='*70}")

        try:
            ser = serial.Serial(
                port='COM13',
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.5
            )
            print(f"✓ 端口已打开 @ {baudrate}")

            for i, cmd in enumerate(COMMANDS):
                print(f"\r[{baudrate}] {i+1}/{len(COMMANDS)}: {cmd!r:15} ", end="", flush=True)

                try:
                    ser.reset_input_buffer()
                    ser.write(cmd)
                    time.sleep(0.3)

                    if ser.in_waiting > 0:
                        data = ser.read(ser.in_waiting)
                        print(f"\n\n!!! 收到响应 !!!")
                        print(f"命令: {cmd!r}")
                        print(f"波特率: {baudrate}")
                        print(f"数据: {data.hex()}")
                        try:
                            print(f"ASCII: {data.decode('ascii', errors='ignore')}")
                        except:
                            pass

                        # 保存成功配置
                        with open('successful_config.txt', 'w') as f:
                            f.write(f"波特率: {baudrate}\n")
                            f.write(f"命令: {cmd!r}\n")
                            f.write(f"响应: {data.hex()}\n")

                        found_response = True
                        ser.close()
                        return baudrate, cmd, data

                except Exception as e:
                    print(f"错误: {e}")

            ser.close()
            print(f"\n{baudrate} bps: 无响应")

        except serial.SerialException as e:
            print(f"无法打开端口: {e}")
        except Exception as e:
            print(f"错误: {e}")

    if not found_response:
        print(f"\n\n{'='*70}")
        print("扫描完成 - 未找到任何响应")
        print(f"{'='*70}")
        print("\n可能原因:")
        print("  1. 单片机未连接或未上电")
        print("  2. TX/RX线路接反")
        print("  3. 单片机固件问题")
        print("  4. 单片机使用非标准波特率")
        print("\n请检查:")
        print("  1. 单片机是否正常供电")
        print("  2. CH340 TX连接单片机RX")
        print("  3. CH340 RX连接单片机TX")
        print("  4. 共地连接")

    return None, None, None

if __name__ == "__main__":
    auto_scan()
