import serial
import time
from datetime import datetime

BAUDRATES = [300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 56000, 57600, 115200]

def scan_com12():
    print("="*70)
    print("扫描 COM12 所有波特率")
    print("="*70)

    for baudrate in BAUDRATES:
        print(f"\n尝试 {baudrate} bps... ", end="", flush=True)

        try:
            ser = serial.Serial(
                port='COM12',
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=2
            )

            ser.reset_input_buffer()
            time.sleep(0.5)

            # 等待数据
            start = time.time()
            data_received = False

            while time.time() - start < 2:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                    print(f"\n\n!!! 找到数据 !!!")
                    print(f"波特率: {baudrate}")
                    print(f"时间: {timestamp}")
                    print(f"数据: {len(data)} 字节")
                    print(f"HEX: {data.hex()}")
                    print(f"ASCII: {data.decode('ascii', errors='ignore')}")

                    ser.close()
                    return baudrate, data

                time.sleep(0.05)

            ser.close()
            print(f"无数据", end="")

        except serial.SerialException as e:
            print(f"错误: {e}", end="")
        except Exception as e:
            print(f"错误: {e}", end="")

    print(f"\n\n未找到正确波特率")
    return None, None

if __name__ == "__main__":
    scan_com12()
