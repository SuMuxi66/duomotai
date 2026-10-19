import serial
import time
from datetime import datetime

def listen_com13():
    print("="*70)
    print("CH340 COM13 持续监听程序")
    print("="*70)
    print("\n按 Ctrl+C 退出")
    print("-"*70)

    baudrate = 9600

    while True:
        print(f"\n尝试连接 {baudrate} bps...")

        try:
            ser = serial.Serial(
                port='COM13',
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=None
            )

            print(f"✓ 串口已打开，持续监听中...")
            print(f"等待数据...\n")
            print("-"*70)

            buffer = b''
            last_data_time = time.time()

            while True:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                    buffer += data

                    # 实时显示
                    print(f"\n[{timestamp}] {len(data)} 字节:")
                    print(f"  HEX: {data.hex()}")
                    try:
                        ascii_text = data.decode('ascii', errors='ignore')
                        if ascii_text.strip():
                            print(f"  ASCII: {repr(ascii_text)}")
                    except:
                        pass

                    last_data_time = time.time()

                time.sleep(0.01)

        except serial.SerialException as e:
            print(f"串口错误: {e}")
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n程序已停止")
            if 'ser' in locals() and ser.is_open:
                ser.close()
            return
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(1)

if __name__ == "__main__":
    listen_com13()
