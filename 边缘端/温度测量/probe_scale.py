import serial
import time
from datetime import datetime

def probe_scale_module(port='COM6'):
    print("=" * 70)
    print("电子秤模块探测程序")
    print("=" * 70)

    baudrates = [9600, 1200, 2400, 4800, 19200, 38400, 57600, 115200]

    for baudrate in baudrates:
        print(f"\n尝试波特率: {baudrate}")

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

            print("  等待数据...", end="", flush=True)

            start = time.time()
            all_data = b''

            while time.time() - start < 3:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    all_data += data
                    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                    print(f"\n  [{ts}] {len(data)} 字节: {data.hex()}")
                    try:
                        print(f"         ASCII: {data.decode('ascii', errors='ignore')}")
                    except:
                        pass
                time.sleep(0.05)

            ser.close()

            if all_data:
                print(f"  ✓ 收到 {len(all_data)} 字节")
                return baudrate, all_data
            else:
                print(" 无数据")

        except Exception as e:
            print(f"  错误: {e}")

    return None, None

if __name__ == "__main__":
    baudrate, data = probe_scale_module('COM6')
    if baudrate:
        print(f"\n成功! 波特率: {baudrate}")
    else:
        print("\n未检测到数据")
