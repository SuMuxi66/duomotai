import serial
import time
import struct
from datetime import datetime

class MLX90640:
    def __init__(self, port='COM6', baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=2
        )
        print(f"Connected to {self.port} @ {self.baudrate}")

    def send_cmd(self, cmd):
        self.ser.write(cmd)
        time.sleep(0.2)

    def set_auto(self):
        self.send_cmd(bytes([0xA5, 0x35, 0x02, 0xDC]))

    def read_frame(self):
        if self.ser.in_waiting >= 1544:
            data = self.ser.read(1544)
            if data[0:2] == b'\x5A\x5A':
                return data
        return None

    def parse(self, data):
        temps = []
        for i in range(768):
            idx = 4 + i * 2
            t = (data[idx+1] << 8 | data[idx]) / 100.0
            temps.append(t)
        
        sensor_temp = (data[4+768*2+1] << 8 | data[4+768*2]) / 100.0
        return temps, sensor_temp

    def close(self):
        if self.ser:
            self.ser.close()

def main():
    mlx = MLX90640(port='COM6', baudrate=115200)
    try:
        mlx.connect()
        mlx.set_auto()
        
        print("\n读取温度数据中...")
        print("="*70)
        
        start = time.time()
        while time.time() - start < 10:
            frame = mlx.read_frame()
            if frame:
                temps, st = mlx.parse(frame)
                
                avg = sum(temps)/len(temps)
                max_t = max(temps)
                min_t = min(temps)
                
                ts = datetime.now().strftime('%H:%M:%S')
                print(f"[{ts}] 平均: {avg:.2f}°C | 最高: {max_t:.2f}°C | 最低: {min_t:.2f}°C | 传感器: {st:.2f}°C")
                
                break
            
            time.sleep(0.1)
        
    except Exception as e:
        print(f"错误: {e}")
    finally:
        mlx.close()

if __name__ == "__main__":
    main()
