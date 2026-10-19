import serial
import time
from datetime import datetime

def analyze_data():
    print("="*70)
    print("16进制数据分析工具")
    print("="*70)

    # 用户提供的原始数据
    raw_hex = "28 43 91 FF 0E 03 A3 FE 91 95 87 E5 38 C2 0A 83 83 1A 1A FB A1 1D 06 C3 60 14 83 E3 E3 E7 06 8D 8F 02 49 8B 91 89 E3 87 08 8D FB 93 67 FA A2 C2 22 FF"

    # 转换为字节数组
    data = bytes.fromhex(raw_hex.replace(' ', ''))

    print(f"\n原始数据: {raw_hex}")
    print(f"\n数据长度: {len(data)} 字节")
    print(f"\n{'='*70}")
    print("数据分析:")
    print(f"{'='*70}")

    # 检查帧头帧尾
    print(f"\n1. 帧头帧尾分析:")
    print(f"   首字节: 0x{data[0]:02X} ({data[0]})")
    print(f"   末字节: 0x{data[-1]:02X} ({data[-1]})")

    if data[0] == 0x28:
        print(f"   ✓ 0x28 可能是帧头或长度标记")
    if data[-1] == 0xFF:
        print(f"   ✓ 0xFF 可能是帧尾")

    # 显示所有字节
    print(f"\n2. 字节详情:")
    print(f"   索引   HEX     DEC     二进制")
    print(f"   " + "-"*45)
    for i, b in enumerate(data):
        print(f"   [{i:2d}]   0x{b:02X}    {b:3d}    {b:08b}")

    # 尝试不同的解析方式
    print(f"\n{'='*70}")
    print("3. 协议猜测:")
    print(f"{'='*70}")

    # 猜测1：第一个字节是长度
    if data[0] == len(data):
        print(f"\n猜测A: 数据长度在首字节")
        print(f"   第1字节 = {data[0]} (数据长度)")
        print(f"   剩余 {data[0]} 字节")
        payload = data[1:]
        print(f"   数据段: {payload.hex()}")

    # 猜测2：查找校验和
    print(f"\n猜测B: 校验和验证")
    # 尝试XOR校验
    xor_sum = 0
    for i, b in enumerate(data[:-1]):
        xor_sum ^= b
    print(f"   XOR校验和: 0x{xor_sum:02X}, 实际末字节: 0x{data[-1]:02X}")
    if xor_sum == data[-1]:
        print(f"   ✓ XOR校验通过!")
    else:
        print(f"   ✗ XOR校验失败")

    # 尝试求和校验
    sum_val = sum(data[:-1]) & 0xFF
    print(f"   求和校验(低8位): 0x{sum_val:02X}, 实际末字节: 0x{data[-1]:02X}")
    if sum_val == data[-1]:
        print(f"   ✓ 求和校验通过!")
    else:
        print(f"   ✗ 求和校验失败")

    # 猜测3：常见传感器协议
    print(f"\n猜测C: 常见协议解析")
    print(f"   可能的协议格式:")

    # 协议1: 长度 + 命令 + 数据 + 校验
    if len(data) >= 6:
        print(f"\n   协议格式1: [长度][命令][数据...][校验]")
        print(f"     长度: 0x{data[0]:02X} = {data[0]}")
        print(f"     命令: 0x{data[1]:02X}")
        if len(data) >= 4:
            # 尝试提取2字节重量值
            weight = (data[3] << 8) | data[2]
            print(f"     可能的数据字: 0x{data[2]:02X} 0x{data[3]:02X} = {weight}")

    # 协议2: 0x5A帧头 (虽然数据里没有，但显示分析思路)
    if data[1] == 0x5A or data[2] == 0x5A:
        print(f"   检测到0x5A帧头")
    else:
        print(f"   无0x5A帧头")

    # 尝试提取可能的重量值
    print(f"\n猜测D: 提取可能的重量值")
    print(f"   尝试2字节低字节在前 (Little Endian):")

    for i in range(len(data) - 1):
        val = (data[i+1] << 8) | data[i]
        if 0 < val < 10000:  # 合理的重量范围
            print(f"     [{i},{i+1}]: {val} (0x{data[i]:02X} 0x{data[i+1]:02X})")

    print(f"\n   尝试2字节高字节在前 (Big Endian):")
    for i in range(len(data) - 1):
        val = (data[i] << 8) | data[i+1]
        if 0 < val < 10000:
            print(f"     [{i},{i+1}]: {val} (0x{data[i]:02X} 0x{data[i+1]:02X})")

    # 显示数据特征
    print(f"\n{'='*70}")
    print("4. 数据特征:")
    print(f"{'='*70}")

    # 统计
    zero_count = data.count(0)
    ff_count = data.count(0xFF)
    print(f"   0x00 字节数: {zero_count}")
    print(f"   0xFF 字节数: {ff_count}")

    # 查找重复模式
    print(f"\n   重复模式分析:")
    for length in [2, 3, 4]:
        for i in range(len(data) - length * 2 + 1):
            chunk = data[i:i+length]
            next_chunk = data[i+length:i+length*2]
            if chunk == next_chunk:
                print(f"     [{i}:{i+length}] = [{i+length}:{i+length*2}]: {chunk.hex()}")


def listen_and_parse():
    """监听COM13并实时解析"""
    print("\n" + "="*70)
    print("开始监听COM13并实时解析数据...")
    print("按 Ctrl+C 停止")
    print("="*70 + "\n")

    try:
        ser = serial.Serial(
            port='COM13',
            baudrate=9600,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=None
        )

        print("✓ 串口已连接，等待数据...\n")

        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                print(f"{'='*70}")
                print(f"[{timestamp}] 收到 {len(data)} 字节")
                print(f"HEX: {data.hex()}")
                print(f"{'='*70}")

                # 尝试解析
                if len(data) >= 40:
                    print("\n尝试解析为40字节协议...")

                    # 检查帧头帧尾
                    if data[0] == 0x28 and data[-1] == 0xFF:
                        print("✓ 帧头(0x28) 和 帧尾(0xFF) 匹配")

                    # 提取重量值 - 尝试不同的位置
                    for offset in range(10, 20):
                        if offset + 1 < len(data):
                            val = (data[offset+1] << 8) | data[offset]
                            if 0 < val < 10000:
                                print(f"  可能的重量[{offset},{offset+1}]: {val}")

            time.sleep(0.01)

    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n\n停止监听")
        if 'ser' in locals():
            ser.close()
    except Exception as e:
        print(f"错误: {e}")


if __name__ == "__main__":
    # 先分析已有数据
    analyze_data()

    # 然后开始监听
    print("\n")
    listen_and_parse()
