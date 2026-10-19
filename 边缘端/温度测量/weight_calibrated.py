import serial
import time
from datetime import datetime

class WeightScaleWithCalibration:
    def __init__(self):
        self.ser = None
        self.buffer = b''
        self.zero_offset = None
        self.weight_data = {
            "weight": 0.0,
            "unit": "g",
            "stable": True,
            "calibrated": False,
            "timestamp": None
        }
        self.running = False
        self.packet_count = 0

    def calibrate_zero(self, samples=20):
        """校准零点 - 收集空载时的数据"""
        print(f"\n开始校准零点 (收集 {samples} 个样本)...")
        print("请确保秤上无负载!\n")

        values = []
        buffer = b''
        start_time = time.time()

        while len(values) < samples and time.time() - start_time < 30:
            if self.ser.in_waiting > 0:
                data = self.ser.read(self.ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]

                    # 提取位置[4,5]的值 (高字节在前)
                    val = (packet[4] << 8) | packet[5]
                    values.append(val)

                    if len(values) % 5 == 0:
                        print(f"  已收集 {len(values)}/{samples}...")

                    time.sleep(0.1)

        if values:
            self.zero_offset = sum(values) / len(values)
            print(f"\n✓ 零点校准完成!")
            print(f"  平均零点值: {self.zero_offset:.1f}")
            print(f"  样本数: {len(values)}")
            self.weight_data["calibrated"] = True
            return True
        else:
            print("\n✗ 校准失败，未收到足够数据")
            return False

    def calculate_weight(self, packet):
        """计算重量"""
        if not self.zero_offset:
            return None, None

        val = (packet[4] << 8) | packet[5]

        raw_weight = (val - self.zero_offset) / 222.14

        return max(0, raw_weight), val

    def run(self):
        """持续读取并计算重量"""
        self.running = True
        buffer = b''

        while self.running:
            if self.ser and self.ser.in_waiting:
                data = self.ser.read(self.ser.in_waiting)
                buffer += data

                idx = buffer.find(b'\x28')
                if idx != -1 and idx + 50 <= len(buffer):
                    packet = buffer[idx:idx+50]
                    buffer = buffer[idx+50:]

                    weight, val = self.calculate_weight(packet)
                    self.packet_count += 1

                    if weight is not None:
                        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                        print(f"[{timestamp}] #{self.packet_count} | 重量: {weight:7.2f} g | RAW: {val}")

            time.sleep(0.01)

    def stop(self):
        self.running = False

def main():
    print("="*70)
    print("重量传感器 - 自动校准程序")
    print("="*70)
    print("步骤:")
    print("  1. 程序会自动连接 COM13 @ 2400 bps")
    print("  2. 先进行零点校准 (确保秤上无负载)")
    print("  3. 然后放上50g砝码验证")
    print("-"*70)

    scale = WeightScaleWithCalibration()

    try:
        print("\n正在连接串口...")
        scale.ser = serial.Serial(
            port='COM13',
            baudrate=2400,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=None
        )
        print("✓ 串口已连接\n")

        # 校准零点
        if scale.calibrate_zero(samples=20):
            # 校准完成后开始读取
            print("\n校准完成，开始读取重量数据...")
            print("请放上砝码进行验证!\n")
            print("按 Ctrl+C 停止\n")

            # 清空缓冲区
            scale.ser.reset_input_buffer()
            time.sleep(0.5)

            scale.run()

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n程序已停止")
    except Exception as e:
        print(f"错误: {e}")
    finally:
        if scale.ser:
            scale.ser.close()

if __name__ == "__main__":
    main()
