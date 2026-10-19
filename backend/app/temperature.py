import platform
import serial
import serial.tools.list_ports
import time
import struct
import math
import random
from datetime import datetime
from threading import Thread, Lock

from .config import settings

START_COM_NUM = 5  # Windows: 从COM5之后开始搜索
TEMP_BAUDRATE = 115200  # 温度传感器只搜索115200
MAX_CONNECT_RETRIES = 3  # 无传感器时最大重试次数，避免无限5s循环
MAX_DEVICE_INSERTION_SEARCH = 10  # 检测到新设备插入时最多搜索次数
DEVICE_CHECK_INTERVAL = 2  # 检测新设备插入的间隔（秒）
WAIT_RESCAN_CYCLES = 15  # 等待新设备期间每 N 个检查周期（约30s）强制全量重扫
CH340_VID = 0x1A86  # CH340/CH341 USB转串口：红外/称重/电磁阀三块板卡均为该芯片
CH340_PID = 0x7523

# 双源优先级（2026-09-12）：MLX90640 有两条采集链路——香橙派远程推流（remote_ir，
# /ws/opi-ir，源名 opi）与本机 USB 串口 reader / 温重二合一板卡注入（源名 local）。
# 两路各写独立源槽，读取时按"远程 fresh 优先、断流回落本机"选择，替代旧的
# "最后写入者胜"（双源同时在线时互相覆盖、数值抖动）。
REMOTE_SOURCE = "opi"
LOCAL_SOURCE = "local"
REMOTE_FRESH_SECONDS = 10.0  # 远程源多久没新帧视为断流（Pi 连续推流，正常帧间隔远小于此）


class MLX90640Reader:
    def __init__(self, port=None, baudrate=None):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.buffer = b''
        self.temperature_data = {
            "target": 0.0,
            "average": 0.0,
            "maximum": 0.0,
            "minimum": 0.0,
            "sensor": 0.0,
            "timestamp": None
        }
        self.running = False
        self.thread = None
        self.lock = Lock()
        self.start_lock = Lock()
        self.known_ports = set()  # 已知的串口列表，用于检测新设备插入
        # 双源槽：source -> 最近一帧数据（含 ISO timestamp）；source_mono 为单调钟写入时刻
        self.source_data = {}
        self.source_mono = {}
    @staticmethod
    def _is_bluetooth_port(port) -> bool:
        """蓝牙虚拟串口：打开会长时间超时（OSError 121 信号灯超时），扫描必须跳过"""
        desc = port.description or ''
        hwid = (getattr(port, 'hwid', '') or '').upper()
        return 'BTHENUM' in hwid or 'BLUETOOTH' in desc.upper() or '蓝牙' in desc

    @staticmethod
    def _is_ch340_port(port) -> bool:
        """是否 CH340/CH341 USB 转串口（VID_1A86&PID_7523）"""
        try:
            return port.vid == CH340_VID and port.pid == CH340_PID
        except Exception:
            return False

    @classmethod
    def _is_temperature_sensor_port(cls, port) -> bool:
        """判断串口是否可能是温度传感器端口（跨平台）"""
        if cls._is_bluetooth_port(port):
            return False
        system = platform.system()

        if system == 'Windows':
            # Windows: 只搜索 COM > START_COM_NUM 的 CH340 口，蓝牙口已在上面剔除
            port_name = port.device.upper()
            if not port_name.startswith('COM'):
                return False
            try:
                com_num = int(port_name[3:])
            except ValueError:
                return False
            return com_num > START_COM_NUM and cls._is_ch340_port(port)
        else:
            # Linux/Mac: 搜索 USB 转串口和 CDC 设备
            return (
                '/dev/ttyUSB' in port.device
                or '/dev/ttyACM' in port.device
                or 'tty.usbserial' in port.device
                or 'tty.usbmodem' in port.device
            )

    def find_port(self):
        print("正在查找 MLX90640 传感器...")
        try:
            ports = serial.tools.list_ports.comports()
        except Exception as e:
            print(f"无法列出串口: {e}")
            return False
        if not ports:
            print("未找到任何串口")
            return False
        print(f"检测到 {len(ports)} 个串口: {[p.device for p in ports]}")

        filtered_ports = [p for p in ports if self._is_temperature_sensor_port(p)]

        if not filtered_ports:
            print(f"未找到符合条件的串口 (系统: {platform.system()})")
            return False

        print(f"将搜索以下串口: {[p.device for p in filtered_ports]}")
        
        for port in filtered_ports:
            baudrate = TEMP_BAUDRATE
            print(f"\n尝试 {port.device} @ {baudrate}...", end="", flush=True)
            try:
                ser = serial.Serial(
                    port=port.device,
                    baudrate=baudrate,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1
                )
                ser.reset_input_buffer()
                time.sleep(0.2)
                ser.write(bytes([0xA5, 0x35, 0x02, 0xDC]))
                time.sleep(0.5)
                if ser.in_waiting >= 100:
                    data = ser.read(ser.in_waiting)
                    if b'\x5A\x5A' in data[:10]:
                        ser.close()
                        self.port = port.device
                        self.baudrate = baudrate
                        print(f" ✓ 找到传感器!")
                        return True
                ser.close()
                print(f" 无响应", end="")
            except Exception as e:
                print(f" {e}", end="")
        return False
    def connect(self):
        if not self.port:
            if not self.find_port():
                return False
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.5
            )
            self.ser.reset_input_buffer()
            print(f"✓ 已连接到 {self.port} @ {self.baudrate} bps")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False
    def read_frame(self):
        while self.running:
            if self.ser and self.ser.in_waiting:
                data = self.ser.read(self.ser.in_waiting)
                self.buffer += data
            while len(self.buffer) >= 1544:
                idx = self.buffer.find(b'\x5A\x5A')
                if idx == -1:
                    self.buffer = self.buffer[-2:]
                    break
                if len(self.buffer) >= idx + 1544:
                    frame = self.buffer[idx:idx+1544]
                    self.buffer = self.buffer[idx+1544:]
                    return frame
                break
            time.sleep(0.01)
        return None
    def parse_temperatures(self, frame):
        temps = []
        for i in range(768):
            offset = 4 + i * 2
            temp_raw = (frame[offset+1] << 8) | frame[offset]
            temp = temp_raw / 100.0

            temps.append(temp)
        sensor_temp_offset = 4 + 768 * 2
        sensor_temp_raw = (frame[sensor_temp_offset+1] << 8) | frame[sensor_temp_offset]
        sensor_temp = sensor_temp_raw / 100.0

        return temps, sensor_temp
    def update_temperature_data(self, temps, sensor_temp, source: str = LOCAL_SOURCE):
        avg_temp = sum(temps) / len(temps)
        max_temp = max(temps)
        min_temp = min(temps)
        center_temp = temps[len(temps)//2]
        data = {
            "target": round(center_temp, 2),
            "average": round(avg_temp, 2),
            "maximum": round(max_temp, 2),
            "minimum": round(min_temp, 2),
            "sensor": round(sensor_temp, 2),
            "timestamp": datetime.now().isoformat()
        }
        with self.lock:
            self._write_source_locked(source, data)

    def _write_source_locked(self, source: str, data: dict):
        """写入指定源槽并重选生效源（调用方须持有 self.lock）"""
        self.source_data[source] = data
        self.source_mono[source] = time.monotonic()
        self._select_source_locked()

    def _select_source_locked(self):
        """按优先级选出当前生效源（调用方须持有 self.lock）。

        远程（opi）源在 REMOTE_FRESH_SECONDS 内有新帧则优先；断流回落本机 local；
        两路都过期时取写入时刻最新的一路（timestamp 已过期，下游新鲜度判定自然判无效）。
        """
        if not self.source_data:
            return
        now = time.monotonic()
        remote_mono = self.source_mono.get(REMOTE_SOURCE)
        if (
            REMOTE_SOURCE in self.source_data
            and remote_mono is not None
            and now - remote_mono <= REMOTE_FRESH_SECONDS
        ):
            chosen = REMOTE_SOURCE
        else:
            chosen = max(self.source_data, key=lambda s: self.source_mono.get(s, 0.0))
        selected = dict(self.source_data[chosen])
        selected["source"] = chosen
        self.temperature_data = selected
    def _has_new_device(self) -> bool:
        """检测是否有新设备插入"""
        try:
            current_ports = set()
            for p in serial.tools.list_ports.comports():
                if self._is_temperature_sensor_port(p):
                    current_ports.add(p.device)
            
            has_new = False
            for port in current_ports:
                if port not in self.known_ports:
                    has_new = True
                    print(f"[温度] 检测到新设备插入: {port}")
                    break
            
            self.known_ports = current_ports
            return has_new
        except Exception as e:
            print(f"[温度] 检测新设备时出错: {e}")
            return False
    
    def _search_with_retries(self, max_retries: int = MAX_DEVICE_INSERTION_SEARCH) -> bool:
        """在检测到新设备时进行多次搜索"""
        for attempt in range(1, max_retries + 1):
            print(f"[温度] 设备插入搜索 {attempt}/{max_retries}...")
            if self.find_port():
                return True
            time.sleep(1)  # 每次搜索间隔1秒
        return False

    def run(self):
        self.running = True
        connect_retries = 0
        while self.running:
            try:
                if not self.ser or not self.ser.is_open:
                    if not self.connect():
                        connect_retries += 1
                        if connect_retries >= MAX_CONNECT_RETRIES:
                            print(f"[温度] 连续 {MAX_CONNECT_RETRIES} 次连接失败，停止重试，等待新设备插入...")
                            # 记录当前已知端口，等待新设备插入
                            try:
                                for p in serial.tools.list_ports.comports():
                                    if self._is_temperature_sensor_port(p):
                                        self.known_ports.add(p.device)
                            except:
                                pass
                            
                            # 静默等待新设备插入；另外周期性强制重扫——端口被占用
                            # （如串口调试助手）后释放时端口集合不变，
                            # _has_new_device() 不会触发，只能靠重扫恢复
                            wait_cycles = 0
                            while self.running:
                                time.sleep(DEVICE_CHECK_INTERVAL)
                                wait_cycles += 1
                                if self._has_new_device():
                                    print(f"[温度] 检测到新设备，开始搜索...")
                                    if self._search_with_retries():
                                        print(f"[温度] 找到设备: {self.port}")
                                        connect_retries = 0  # 重置计数
                                        break
                                    else:
                                        print(f"[温度] {MAX_DEVICE_INSERTION_SEARCH}次搜索未找到，继续等待新设备...")
                                elif wait_cycles >= WAIT_RESCAN_CYCLES:
                                    wait_cycles = 0
                                    if self.find_port():
                                        print(f"[温度] 周期重扫找到设备: {self.port}")
                                        connect_retries = 0  # 重置计数
                                        break
                            continue
                        time.sleep(5)
                        continue
                    connect_retries = 0  # 连接成功，重置计数
                frame = self.read_frame()
                if frame:
                    temps, sensor_temp = self.parse_temperatures(frame)
                    self.update_temperature_data(temps, sensor_temp, source=LOCAL_SOURCE)
            except Exception as e:
                print(f"读取温度数据出错: {e}")
                if self.ser:
                    try:
                        self.ser.close()
                    except:
                        pass
                # 重置端口和串口，以便下次重新扫描
                self.ser = None
                self.port = None
                self.baudrate = None
                connect_retries += 1
                if connect_retries >= MAX_CONNECT_RETRIES:
                    print(f"[温度] 连续 {MAX_CONNECT_RETRIES} 次读取失败，停止重试，等待新设备插入...")
                    # 记录当前已知端口，等待新设备插入
                    try:
                        for p in serial.tools.list_ports.comports():
                            if self._is_temperature_sensor_port(p):
                                self.known_ports.add(p.device)
                    except:
                        pass
                    
                    # 静默等待新设备插入；周期性强制重扫同上（占用释放场景）
                    wait_cycles = 0
                    while self.running:
                        time.sleep(DEVICE_CHECK_INTERVAL)
                        wait_cycles += 1
                        if self._has_new_device():
                            print(f"[温度] 检测到新设备，开始搜索...")
                            if self._search_with_retries():
                                print(f"[温度] 找到设备: {self.port}")
                                connect_retries = 0  # 重置计数
                                break
                            else:
                                print(f"[温度] {MAX_DEVICE_INSERTION_SEARCH}次搜索未找到，继续等待新设备...")
                        elif wait_cycles >= WAIT_RESCAN_CYCLES:
                            wait_cycles = 0
                            if self.find_port():
                                print(f"[温度] 周期重扫找到设备: {self.port}")
                                connect_retries = 0  # 重置计数
                                break
                    continue
                time.sleep(5)
    def start(self):
        with self.start_lock:
            if self.running and self.thread and self.thread.is_alive():
                return

            self.running = True
            if self.thread and self.thread.is_alive():
                return

            self.thread = Thread(target=self.run, daemon=True)
            self.thread.start()
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
    def get_temperature_data(self):
        with self.lock:
            # 读取时重选一次：远程源断流老化后无需等下一帧写入，读数即自动回落本机
            self._select_source_locked()
            return self.temperature_data.copy()

# 全局温度传感器实例（延迟初始化）
_temperature_reader = None

def _get_reader():
    global _temperature_reader
    if _temperature_reader is None:
        _temperature_reader = MLX90640Reader()
    return _temperature_reader

def _mock_temperature_enabled() -> bool:
    return bool(getattr(settings, "temperature_mock_enabled", False))


class MockTemperatureSource:
    """环境温度左右的模拟数据源（TEMPERATURE_MOCK_ENABLED=true 时替代串口读取）。

    每秒生成一次：围绕基准温度的平滑随机游走 + 约 1 分钟周期的缓慢正弦漂移，
    数值不会秒级跳变，观感接近真实红外传感器。get_temperature()/油温监测/
    温度语音快答共用此数据，/temperature 接口照常返回。
    """

    TICK_INTERVAL = 1.0  # 与前端 MonitorOverlay 的 /temperature 轮询周期一致

    def __init__(self):
        self.lock = Lock()
        self.running = False
        self.thread = None
        base = settings.temperature_mock_base
        self._target = base
        self._hotspot = base + 1.5  # 视野热点（模拟手/锅具等略高于环境的物体）
        self._sensor = base + 0.8  # 传感器自身温度通常略高于环境
        self.data = {
            "target": round(base, 2),
            "average": round(base, 2),
            "maximum": round(base + 1.5, 2),
            "minimum": round(base - 0.6, 2),
            "sensor": round(base + 0.8, 2),
            "timestamp": None,
        }

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _tick(self):
        amp = settings.temperature_mock_amplitude
        base = settings.temperature_mock_base
        drift = 0.2 * math.sin(time.time() / 60.0)  # 慢周期漂移，避免纯随机游走的机械感
        self._target = self._clamp(self._target + random.uniform(-0.08, 0.08), base - amp, base + amp)
        self._hotspot = self._clamp(
            self._hotspot + random.uniform(-0.15, 0.15), self._target + 0.5, self._target + 3.0
        )
        self._sensor = self._clamp(self._sensor + random.uniform(-0.05, 0.05), base + 0.3, base + 1.5)
        target = round(self._target + drift, 2)
        maximum = round(self._hotspot + drift, 2)
        minimum = round(self._target - 0.6 + drift, 2)
        average = round(target * 0.6 + maximum * 0.25 + minimum * 0.15, 2)
        with self.lock:
            self.data = {
                "target": target,
                "average": average,
                "maximum": maximum,
                "minimum": minimum,
                "sensor": round(self._sensor + drift, 2),
                "timestamp": datetime.now().isoformat(),
            }

    def _run(self):
        while self.running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[温度][mock] 生成失败: {exc}")
            time.sleep(self.TICK_INTERVAL)

    def start(self):
        if self.running and self.thread and self.thread.is_alive():
            return
        self._tick()  # 启动即有数据，不等第一个周期
        self.running = True
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        print(
            f"[温度] 假数据源已启动: 基准 {settings.temperature_mock_base}°C ± "
            f"{settings.temperature_mock_amplitude}°C（不读串口）"
        )

    def stop(self):
        self.running = False

    def get_data(self):
        with self.lock:
            return self.data.copy()


_mock_source = None


def _get_mock():
    global _mock_source
    if _mock_source is None:
        _mock_source = MockTemperatureSource()
    return _mock_source

def get_temperature():
    """获取当前温度数据（懒加载，不阻塞启动）"""
    try:
        reader = _get_reader()
        if not reader.running:
            reader.start()
        return reader.get_temperature_data()
    except Exception as e:
        return {"target": 0.0, "average": 0.0, "maximum": 0.0, "minimum": 0.0, "sensor": 0.0, "timestamp": None, "error": str(e)}

def get_temperature():
    """获取当前温度数据（懒加载，不阻塞启动）"""
    if _mock_temperature_enabled():
        mock = _get_mock()
        if not mock.running:
            mock.start()
        return mock.get_data()
    try:
        reader = _get_reader()
        if not reader.running:
            reader.start()
        return reader.get_temperature_data()
    except Exception as e:
        return {"target": 0.0, "average": 0.0, "maximum": 0.0, "minimum": 0.0, "sensor": 0.0, "timestamp": None, "error": str(e)}

def apply_external_temperature(maximum: float, target: float, average: float, sensor: float):
    """写入本机外部来源的温度数据（温重二合一板卡的文本协议字段）。

    本机没有独立的 MLX90640 串口时，温度数据由重量模块代为解析。写入 local 源槽
    （与远程 opi 推流槽分开，读取时按优先级选择），/temperature 接口照常返回。
    假数据模式下忽略写入，保持 mock 数值不被覆盖。
    """
    if _mock_temperature_enabled():
        return
    reader = _get_reader()
    with reader.lock:
        reader._write_source_locked(LOCAL_SOURCE, {
            "target": round(target, 2),
            "average": round(average, 2),
            "maximum": round(maximum, 2),
            "minimum": round(average, 2),
            "sensor": round(sensor, 2),
            "timestamp": datetime.now().isoformat()
        })

def start_temperature_monitoring():
    """启动温度监控"""
    if _mock_temperature_enabled():
        _get_mock().start()
        return {"status": "started", "mock": True}
    try:
        _get_reader().start()
        return {"status": "started"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def stop_temperature_monitoring():
    """停止温度监控"""
    if _mock_temperature_enabled():
        if _mock_source:
            _mock_source.stop()
        return {"status": "stopped", "mock": True}
    try:
        if _temperature_reader:
            _temperature_reader.stop()
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def looks_like_general_temp_query(text: str) -> bool:
    """"现在温度多少度"类实时查询（"油温/锅温"问句由油温快答处理，此处排除）。

    必须带"现在/当前/实时/此刻"即时限定词且为短句，排除知识类问句
    （"水多少度沸腾""人体正常温度"不含即时限定词，自然不命中）。
    """
    normalized = (text or "").strip()
    if not normalized or len(normalized) > 16:
        return False
    if "油温" in normalized or "锅温" in normalized:
        return False
    if not any(kw in normalized for kw in ("温度", "多少度", "几度")):
        return False
    return any(kw in normalized for kw in ("现在", "当前", "实时", "此刻"))


async def answer_temperature_query(runtime, ws, state, text: str) -> bool:
    """确定性快答"现在温度多少度"类问句（不走 LLM）。

    GLM 等模型即使收到实测数据仍可能对齐拒答"无法获取实时温度"，故温度查询
    直接用传感器实测数据组织回复。数据缺失/超过 30 秒返回 False（调用方走原路径）。
    """
    try:
        data = get_temperature()
        ts = data.get("timestamp")
        if not ts:
            return False
        age = (datetime.now() - datetime.fromisoformat(str(ts))).total_seconds()
        if age > 30:
            return False
        maximum = data.get("maximum")
        average = data.get("average")
        if maximum is None or average is None:
            return False
        from . import cooking_core

        reply = (
            f"当前红外传感器实测：锅面最高 {round(float(maximum))}°C、"
            f"平均 {round(float(average))}°C，目标温度 {round(float(data.get('target') or 0))}°C。"
        )
        await cooking_core.send_cooking_text_with_tts(runtime, ws, state, reply)
        return True
    except Exception as exc:
        from .logger import backend_log

        backend_log(f"[温度] 快答失败: {exc}")
        return False