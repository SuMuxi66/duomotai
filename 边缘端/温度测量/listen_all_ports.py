import serial
import serial.tools.list_ports
import time
from datetime import datetime

def listen_all():
    print("="*70)
    print("监听所有CH340端口")
    print("="*70)

    ports = serial.tools.list_ports.comports()
    ch340 = [(p.device, p.description) for p in ports if 'CH340' in p.description or 'USB-SERIAL' in p.description]

    print(f"\n找到CH340端口: {[p[0] for p in ch340]}")

    if not ch340:
        print("未找到CH340设备!")
        return

    serials = []
    for port, desc in ch340:
        try:
            s = serial.Serial(port=port, baudrate=2400, timeout=1)
            serials.append((port, s))
            print(f"✓ 打开 {port}")
        except Exception as e:
            print(f"✗ 无法打开 {port}: {e}")

    if not serials:
        return

    print("\n开始监听，按 Ctrl+C 停止...\n")

    buffer = {port: b'' for port, _ in serials}

    try:
        while True:
            for port, s in serials:
                if s.in_waiting > 0:
                    data = s.read(s.in_waiting)
                    buffer[port] += data

                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                    print(f"\n[{timestamp}] {port} 收到 {len(data)} 字节:")
                    print(f"  HEX: {data.hex()}")

                    # 检查是否有0x28帧头
                    if b'\x28' in data:
                        print(f"  ✓ 检测到帧头 0x28")

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\n停止监听")
    finally:
        for port, s in serials:
            s.close()

if __name__ == "__main__":
    listen_all()
