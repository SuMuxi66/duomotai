import serial
import serial.tools.list_ports
import time
from datetime import datetime
import threading

def listen_port(port_name, baudrate, results, index):
    """监听单个端口"""
    try:
        ser = serial.Serial(
            port=port_name,
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=1
        )
        ser.reset_input_buffer()

        print(f"[{port_name}] ✓ 已连接 @ {baudrate}")

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                results[index] = {
                    'port': port_name,
                    'time': timestamp,
                    'data': data,
                    'hex': data.hex()
                }

                print(f"\n{'='*70}")
                print(f"[{timestamp}] {port_name} 收到 {len(data)} 字节:")
                print(f"  HEX: {data.hex()}")
                try:
                    ascii_text = data.decode('ascii', errors='ignore')
                    if ascii_text.strip():
                        print(f"  ASCII: {repr(ascii_text)}")
                except:
                    pass
                print(f"{'='*70}\n")

            time.sleep(0.01)

    except Exception as e:
        print(f"[{port_name}] 错误: {e}")

def main():
    print("="*70)
    print("CH340 多端口监听程序")
    print("="*70)

    # 查找所有CH340端口
    ports = serial.tools.list_ports.comports()
    ch340_ports = []
    for p in ports:
        if 'CH340' in p.description or 'USB-SERIAL' in p.description:
            ch340_ports.append((p.device, p.description))

    print(f"\n找到 CH340 端口:")
    for device, desc in ch340_ports:
        print(f"  {device}: {desc}")

    if not ch340_ports:
        print("\n未找到 CH340 设备!")
        return

    # 同时监听所有端口
    print(f"\n开始监听 {len(ch340_ports)} 个端口...")
    print("按 Ctrl+C 停止\n")

    results = [None] * len(ch340_ports)
    threads = []

    for i, (port, _) in enumerate(ch340_ports):
        t = threading.Thread(target=listen_port, args=(port, 9600, results, i), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.2)

    print("\n所有端口已开始监听，等待数据...\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\n停止监听")
        return

if __name__ == "__main__":
    main()
