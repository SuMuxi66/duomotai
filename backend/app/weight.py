import platform
import serial
import serial.tools.list_ports
import re
import time
import json
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Thread, Lock

START_COM_NUM = 0  # Windows: 搜索所有 COM 口（温重二合一板卡可能枚举在低端口号上）
WEIGHT_BAUDRATE = 9600  # 重量传感器使用9600波特率
MAX_CONNECT_RETRIES = 3  # 无传感器时最大重试次数，避免无限5s循环
MAX_DEVICE_INSERTION_SEARCH = 10  # 检测到新设备插入时最多搜索次数
DEVICE_CHECK_INTERVAL = 2  # 检测新设备插入的间隔（秒）

# 双源优先级（2026-09-12 硬件迁移）：称重板卡移到香橙派后有两路数据——
# 远程 Pi 推流（remote_sensors /ws/opi-sensors，源名 opi，走同一套解析管线）
# 与本机 USB 串口 reader（源名 local）。两路各写独立源槽，读取时按
# "远程 fresh 优先、断流回落本机"选择（与 temperature.py 双源同构）。
REMOTE_SOURCE = "opi"
LOCAL_SOURCE = "local"
REMOTE_FRESH_SECONDS = 5.0  # 板卡约 0.7s/行，5s 没有新行视为远程断流

# 两点标定增益文件（50g 砝码标定产物，data/ 下运行时数据）：板卡 W= 直读克数有
# 固有增益误差（2026-09-11 实测 50g 砝码读 53.36g，偏高 6.7%），宿主侧乘
# gain_factor 修正为真实克数。文件缺失/损坏/系数超理范围时按 1.0（不修正）。
CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "data" / "weight_calibration.json"


def _load_gain_factor(path=CALIBRATION_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        factor = float(data.get("gain_factor", 1.0))
        if 0.5 <= factor <= 2.0:
            return factor
    except Exception:
        pass
    return 1.0

class WeightScaleReader:
    # 支持的重量数据格式（与原始代码对齐）；W= 值允许负数（去皮后为负）
    # 顺序即优先级：新板卡 Weight=<克数>g 优先，避免被 W= 误命中 RAW= 中的子串
    WEIGHT_PATTERNS = [
        # 新板卡固件：Weight=<克数>g  RAW=<ADC> 同一行（克数 + g 紧邻，RAW 是 ADC 计数）
        (r'Weight=(-?\d+\.?\d*)\s*g', 'g'),
        # 旧板卡：原始值: XXXX | 真实重量: YYY g（保留兼容，可能换回旧板卡）
        (r'原始值[:]\s*\d+\s*\|\s*真实重量[:]\s*(\d+\.?\d*)\s*g', 'g'),
        # W= / w= 直读克重：负向回顾 (?<![A-Za-z]) 防止命中 RAW= 中的 W= 子串
        (r'(?<![A-Za-z])W=(-?\d+\.?\d*)', 'g'),
        (r'(?<![A-Za-z])w=(-?\d+\.?\d*)', 'g'),
        # weight= 兜底（同样加负向回顾）
        (r'(?<![A-Za-z])weight=(-?\d+\.?\d*)', 'g'),
    ]
    
    # 默认常数值
    CALIBRATION_FACTOR = 205.0
    FILTER_SIZE = 15  # 增大滤波窗口，使读数更稳定
    # 空秤死区（克）：HX711 零点随温度/蠕变漂移，±该范围内的读数视为空秤，
    # 避免空秤显示异常小数值。
    # 曾为 5.0g——烹饪调料投放是克级场景，倒 3~4g 盐被死区吞成 0（2026-09-02 实测事故）；
    # 2.0g 仍会吞掉"一撮盐"（倒时冲击冲到 3g，稳定后回落 1.x g 又被吞），降至 1.0
    WEIGHT_DEAD_ZONE_G = 1.0
    # 显示用 EMA 平滑系数：板卡 W= 原生噪声峰峰 ~1.2g（实测 -14.1~-15.3 往复），
    # 直通显示会在 0.x g 间跳动。α=0.2 时稳态残余约 ±0.2g，配合取整显示基本稳定；
    # 收敛约 5~7 秒。下料达标检测不用 EMA（滞后会过冲），用 weight_data["raw"] 即时值。
    EMA_ALPHA = 0.2
    # 突变直通阈值（克）：raw 与平滑值偏差超过它视为真实放/取物品（噪声峰峰仅 ~1.2g，
    # 不会误触发），EMA 直接重建到 raw——否则拿走物品后显示值要按 α=0.2 衰减十几秒才归零，
    # 秤上长期残留旧读数（2026-09-11 实测反馈）。
    EMA_RESYNC_THRESHOLD_G = 5.0
    # 显示路径中值预滤波窗口：继电器吸合/下料电机/振动会耦合出单发毛刺，中值剔除
    # 比降 α 更有效（降 α 会拖慢倒盐等真实小变化的显示）。
    MEDIAN_WINDOW = 3
    # 显示整数迟滞（克）：平滑值偏离当前显示值超过该值才更新显示。EMA 稳态残余
    # ±0.2~0.3g 落在带内，x.5 取整边界不再来回翻转（2026-09-11 反馈"偶尔跳 1-2g"）。
    DISPLAY_HYSTERESIS_G = 0.7
    # ---- 零点自动跟踪（自动校准 v2：块均值慢漂移累积）----
    # 空秤蠕变/温漂是"持续的微小爬行"（相邻样本、相邻块均值变化都极小），而真实
    # 负载必然伴随"到达瞬间的较大跳变或爬升"；静止不动的负载块均值完全不变。
    # 按 20s 块均值累积慢漂移：累积 ≥ AUTO_ZERO_TOTAL_TRIM_G 且历史无块间突升/
    # 突降 → 判为漂移，整段 Trim 归零。
    # （v1 用 ±2g 绝对带守卫，漂移一旦超过 2g 就永远不触发，实际漂到了 7g——已废弃）
    AUTO_ZERO_BLOCK_S = 20.0        # 块时长：块均值抗单样本噪声（σ≈0.6g/√28≈0.11g）
    AUTO_ZERO_MIN_BLOCKS = 4        # 至少 4 块（80s）历史才评估
    AUTO_ZERO_BLOCK_DELTA_G = 0.8   # 相邻块均值差 ≥ 此值 = 有交互（放置/倾倒），清历史
    AUTO_ZERO_TOTAL_TRIM_G = 0.8    # 累积慢漂移达到此值才 Trim（显示死区已覆盖更小的）
    AUTO_ZERO_MAX_TRIM_G = 15.0     # 单次 Trim 上限（超过视为可疑，宁可不抖动基准）
    AUTO_ZERO_JUMP_G = 1.8          # 相邻样本跳变 ≥ 此值 = 重量事件，清历史并进等待期
    AUTO_ZERO_HOLDOFF_S = 10.0      # 重量事件后的等待期
    AUTO_ZERO_COOLDOWN_S = 60.0     # 两次 Trim 最小间隔（块机制下通常不绑定，保险用）

    def __init__(self, port=None, baudrate=WEIGHT_BAUDRATE, gain_factor=None):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        # 两点标定增益修正（50g 砝码实测 0.9370）；显式传参供测试注入
        self._gain_factor = gain_factor if gain_factor is not None else _load_gain_factor()
        if self._gain_factor != 1.0:
            print(f"[重量] 增益修正系数: {self._gain_factor:.4f}（两点标定）")
        self.weight_data = {
            "weight": 0.0,
            "unit": "g",
            "stable": True,
            "timestamp": None
        }
        self.running = False
        self.thread = None
        self.lock = Lock()
        self.start_lock = Lock()
        self.io_lock = Lock()  # 串口写指令与读取线程互斥（下料机/继电器控制）
        self.last_valid_weight = 0.0
        self.zero_value = None
        self.weight_queue = deque(maxlen=self.FILTER_SIZE)
        self._ema_weight = None  # 显示用 EMA 平滑值（raw 即时值随 weight_data 下发）
        self._median_buf = deque(maxlen=self.MEDIAN_WINDOW)  # 显示路径毛刺剔除窗口
        self._display_weight = 0  # 当前已下发的显示整数（迟滞基准）
        self._soft_zero_offset = 0.0  # 软件去皮基准（连接/去皮时重设，自动校准持续微调）
        self._az_blocks = deque(maxlen=60)  # 已完结的 20s 块均值（保留最近 60 块）
        self._az_block_ts = None  # 当前块起始时刻
        self._az_block_sum = 0.0
        self._az_block_n = 0
        self._az_last_sample = None  # 上一采样值（跳变检测）
        self._az_last_activity = 0.0  # 最近一次重量突变（放置/拿走/倾倒）时刻
        self._az_last_trim = 0.0  # 上次自动校准时刻（冷却用）
        # 双源槽：source -> 最近一帧数据（含 ISO timestamp）；source_mono 为单调钟写入时刻
        self.source_data = {}
        self.source_mono = {}

    def _median3(self, raw_weight: float) -> float:
        """显示路径中值预滤波：单发毛刺（振动/电磁干扰）不进 EMA。"""
        self._median_buf.append(raw_weight)
        ordered = sorted(self._median_buf)
        return ordered[len(ordered) // 2]
        self.known_ports = set()  # 已知的串口列表，用于检测新设备插入

    def _apply_ema(self, raw_weight: float) -> float:
        """显示值 EMA 平滑：压板卡原生噪声；偏差超过 EMA_RESYNC_THRESHOLD_G 视为
        真实放/取物品，直通重建（避免拿走物品后读数慢慢衰减的残影）；
        去皮基准变化时由调用方重置为 None（下个读数直通）。"""
        if self._ema_weight is None or abs(raw_weight - self._ema_weight) >= self.EMA_RESYNC_THRESHOLD_G:
            self._ema_weight = raw_weight
        else:
            self._ema_weight = self._ema_weight * (1 - self.EMA_ALPHA) + raw_weight * self.EMA_ALPHA
        return self._ema_weight

    def calculate_weight(self, raw_value):
        """计算重量（使用零点、换算系数和滤波）"""
        if self.zero_value is None:
            return 0
        
        delta = raw_value - self.zero_value
        weight = delta / self.CALIBRATION_FACTOR
        if weight < 0:
            weight = 0
        
        # 使用滑动平均滤波
        self.weight_queue.append(weight)
        avg_weight = sum(self.weight_queue) / len(self.weight_queue)
        
        # 取整，使读数稳定
        return round(avg_weight)

    @staticmethod
    def _is_weight_sensor_port(port) -> bool:
        """判断串口是否可能是重量传感器端口（跨平台）"""
        port_name = port.device.upper()
        system = platform.system()

        if system == 'Windows':
            # Windows: 只搜索 COM > START_COM_NUM 的端口
            if port_name.startswith('COM'):
                try:
                    com_num = int(port_name[3:])
                    return com_num > START_COM_NUM
                except ValueError:
                    pass
            return False
        else:
            # Linux/Mac: 搜索 USB 转串口和 CDC 设备
            # /dev/ttyUSB*  (CH340/CP2102/FTDI 等)
            # /dev/ttyACM*  (STM32 USB CDC 等)
            # /dev/tty.usbserial* (Mac)
            return (
                '/dev/ttyUSB' in port.device
                or '/dev/ttyACM' in port.device
                or 'tty.usbserial' in port.device
                or 'tty.usbmodem' in port.device
            )

    # CH340 USB 转串口芯片（沁恒）VID/PID：温重二合一板卡用的就是它
    CH340_VID_PID = ("1A86", "7523")

    @staticmethod
    def _is_ch340_port(port) -> bool:
        """按设备描述/VID:PID 识别 CH340 USB 转串口芯片（跨平台）"""
        desc = (port.description or "").upper()
        if "CH340" in desc or "CH341" in desc:
            return True
        hwid = (port.hwid or "").upper().replace(" ", "")
        vid, pid = WeightScaleReader.CH340_VID_PID
        return f"VID:PID={vid}:{pid}" in hwid

    def find_port(self):
        print("正在查找重量传感器...")
        try:
            ports = serial.tools.list_ports.comports()
        except Exception as e:
            print(f"无法列出串口: {e}")
            return False
        if not ports:
            print("未找到任何串口")
            return False
        print(f"检测到 {len(ports)} 个串口: {[p.device for p in ports]}")

        filtered_ports = [p for p in ports if self._is_weight_sensor_port(p)]

        if not filtered_ports:
            print(f"未找到符合条件的串口 (系统: {platform.system()})")
            return False

        # 先按设备类型找 CH340，CH340 优先试探；找不到 CH340 时退回全部候选口
        ch340_ports = [p for p in filtered_ports if self._is_ch340_port(p)]
        if ch340_ports:
            print(f"检测到 CH340 设备: {[p.device for p in ch340_ports]}，优先匹配")
            filtered_ports = ch340_ports + [p for p in filtered_ports if p not in ch340_ports]

        print(f"将搜索以下串口: {[p.device for p in filtered_ports]}")

        for port in filtered_ports:
            print(f"\n尝试 {port.device} @ {WEIGHT_BAUDRATE}...", end="", flush=True)
            try:
                ser = serial.Serial(
                    port=port.device,
                    baudrate=WEIGHT_BAUDRATE,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1.5
                )
                ser.reset_input_buffer()
                time.sleep(0.5)

                start = time.time()
                while time.time() - start < 2:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            # 兼容多种数据签名：原始值格式 / W= 直接克重 / 新板卡 Weight=g 格式
                            # W= / Weight= 都加负向回顾，防止命中 RAW= 中的 W= 子串
                            if (re.search(r'原始值[:]\s*(\d+)', line)
                                    or re.search(r'(?<![A-Za-z])W=-?\d', line, re.IGNORECASE)
                                    or re.search(r'Weight=-?\d+\.?\d*\s*g', line, re.IGNORECASE)):
                                ser.close()
                                self.port = port.device
                                self.baudrate = WEIGHT_BAUDRATE
                                print(f" ✓ 找到传感器!")
                                return True
                    time.sleep(0.1)
                ser.close()
                print(f" 无数据", end="")
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
            
            # 记录零点
            self._record_zero()
            
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    def _record_zero(self):
        """记录零点"""
        print("正在记录零点... 请确保秤上没有物体")
        time.sleep(2)
        
        zero_samples = []
        w_samples = []
        for i in range(10):
            try:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        match = re.search(r'原始值[:]\s*(\d+)', line)
                        if match:
                            val = int(match.group(1))
                            zero_samples.append(val)
                        # W= / Weight= 直读克重格式：连接时刻自动去皮（零点随温度漂移的补偿）
                        # 负向回顾防止命中 RAW= 中的 W= 子串；优先 Weight=g 新格式
                        w_match = (re.search(r'Weight=(-?\d+\.?\d*)\s*g', line, re.IGNORECASE)
                                   or re.search(r'(?<![A-Za-z])W=(-?\d+\.?\d*)', line, re.IGNORECASE))
                        if w_match:
                            w_samples.append(float(w_match.group(1)))
                time.sleep(0.2)
            except:
                break

        if zero_samples:
            self.zero_value = sum(zero_samples) / len(zero_samples)
            print(f"零点已记录: {self.zero_value:.0f}")
        else:
            print("无法记录零点，使用默认值")
            self.zero_value = 8226000

        # 软件去皮：以连接时刻的 W= 读数为零点基准，后续读数减去该值
        if w_samples:
            self._soft_zero_offset = sum(w_samples) / len(w_samples)
            print(f"[重量] 软件去皮基准: {self._soft_zero_offset:.1f} g")
        else:
            self._soft_zero_offset = 0.0
        self._ema_weight = None  # 基准已变，旧平滑值失效，下个读数直通重建
        self._median_buf.clear()  # 旧窗口样本基于旧基准，一并失效
        self._az_reset()  # 自动校准块历史基于旧基准，重开

    def parse_weight(self, line):
        """解析重量数据行（与原始代码对齐）"""
        # 优先匹配 "原始值: XXXX | 真实重量: YYY g" 格式
        match = re.search(r'原始值[:\s]*(\d+)\s*\|\s*真实重量[:\s]*(\d+\.?\d*)\s*g', line, re.IGNORECASE)
        if match and match.group(2):
            try:
                return float(match.group(2)), 'g'
            except ValueError:
                pass
        
        # 匹配其他格式
        for pattern, unit in self.WEIGHT_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) == 1 and groups[0]:
                    try:
                        return float(groups[0]), unit if unit else 'g'
                    except ValueError:
                        pass
        return None, None

    def request_tare(self, auto: bool = False):
        """请求手动去皮：接下来约2秒的实测读数均值将成为新零点基准。
        用于物品在秤上时误启动/重启导致基准被污染的场景——先拿走物品，
        点去皮（或重启后端），再放回物品即可正常称重。

        auto=True（首连自动去皮，ensure_remote_zero_baseline 调用）：采样结算时
        若均值明显非空秤（> AUTO_TARE_GUARD_G），拒绝更新基准——2026-09-21 事故：
        后端重启首连恰逢秤上有 ~300g 物品，自动去皮把载荷记成零点，之后"放 300g
        显示 1g"。宁可显示带偏移（用户看得见、可手动去皮），不能静默吞载荷。
        """
        self._tare_samples = []
        self._tare_auto = bool(auto)
        self._tare_capture_until = time.time() + 2.0
        print("[重量] 开始去皮采样（请确保秤面为空）..." if not auto
              else "[重量] 首连自动去皮采样中（秤面非空将拒绝）...")

    # 首连自动去皮的空秤守卫（克）：采样均值超过该值视为"秤上有物"，拒绝去皮。
    # 空秤蠕变/温漂通常十几克内；板卡直读自带数百克偏移的老固件会因此失去自动
    # 去皮（显示带偏移，用户手动去皮即可），远好于静默吞掉载荷。
    AUTO_TARE_GUARD_G = 50.0

    def _set_tare_offset(self, samples: list) -> None:
        """去皮结算共用：自动路径带空秤守卫。"""
        new_offset = sum(samples) / len(samples)
        if getattr(self, "_tare_auto", False) and abs(new_offset) > self.AUTO_TARE_GUARD_G:
            print(f"[重量] 首连自动去皮被拒绝：采样均值 {new_offset:.1f}g 超空秤守卫 "
                  f"{self.AUTO_TARE_GUARD_G:g}g（秤上有物？）——基准保持 "
                  f"{self._soft_zero_offset:.1f}g，如需去皮请清空秤面后手动操作")
            return
        self._soft_zero_offset = new_offset
        self._ema_weight = None  # 基准已变，旧平滑值失效
        self._median_buf.clear()
        print(f"[重量] 去皮完成，新零点基准: {new_offset:.1f} g")

    def _maybe_capture_tare(self, raw_weight: float) -> None:
        """去皮采样窗口内收集原始读数，窗口结束后更新零点基准"""
        until = getattr(self, "_tare_capture_until", 0)
        if not until:
            return
        samples = getattr(self, "_tare_samples", None)
        if samples is None:
            samples = self._tare_samples = []
        # 窗口已过期：用已收集样本结算（即便不足6个，也以均值更新基准，
        # 否则采样窗口永远不结算、_soft_zero_offset 维持旧值，去皮形同虚设）
        if time.time() > until:
            if samples:
                self._set_tare_offset(samples)
                print(f"[重量] 去皮窗口到期（{len(samples)} 样本）")
            self._tare_capture_until = 0
            self._tare_samples = []
            return
        samples.append(raw_weight)
        if len(samples) >= 6:  # 采样够数立即完成（设备约0.7秒/读数）
            self._set_tare_offset(samples)
            self._tare_capture_until = 0
            self._tare_samples = []

    @staticmethod
    def _auto_zero_enabled() -> bool:
        try:
            from .config import settings
            return bool(getattr(settings, "weight_auto_zero_enabled", True))
        except Exception:
            return True

    def _az_reset(self) -> None:
        """清空自动校准的块历史与当前块（基准已变/功能关闭/去皮中）。"""
        self._az_blocks.clear()
        self._az_block_ts = None
        self._az_block_sum = 0.0
        self._az_block_n = 0
        self._az_last_sample = None

    def _maybe_auto_zero(self, value: float, now: float = None) -> None:
        """零点自动跟踪（块均值慢漂移累积）：详见 AUTO_ZERO_* 常量注释。

        value 为减去当前基准后的相对读数；now 可注入时钟供测试。
        判据：慢漂移 = 块均值序列整体缓变（无块间突升/突降、无样本跳变事件）；
        静止负载块均值恒定 → drift≈0 永不触发。
        """
        if not self._auto_zero_enabled():
            self._az_reset()
            return
        now = time.time() if now is None else now
        # 去皮采样中：基准马上要重设，不参与跟踪
        if getattr(self, "_tare_capture_until", 0):
            self._az_reset()
            return
        # 重量事件：相邻样本跳变过大（放置/拿走/倾倒的到达瞬态）→ 清历史进等待期
        if self._az_last_sample is not None and abs(value - self._az_last_sample) >= self.AUTO_ZERO_JUMP_G:
            self._az_last_activity = now
            self._az_blocks.clear()
            self._az_block_ts = None
            self._az_block_sum = 0.0
            self._az_block_n = 0
        self._az_last_sample = value
        # 累积当前块
        if self._az_block_ts is None:
            self._az_block_ts = now
        self._az_block_sum += value
        self._az_block_n += 1
        if now - self._az_block_ts < self.AUTO_ZERO_BLOCK_S:
            return
        # 块完结：结算均值，开新块
        self._az_blocks.append(self._az_block_sum / max(self._az_block_n, 1))
        self._az_block_ts = None
        self._az_block_sum = 0.0
        self._az_block_n = 0
        # ---- 评估 ----
        if len(self._az_blocks) < self.AUTO_ZERO_MIN_BLOCKS:
            return
        if now - self._az_last_activity < self.AUTO_ZERO_HOLDOFF_S:
            return
        if now - self._az_last_trim < self.AUTO_ZERO_COOLDOWN_S:
            return
        means = list(self._az_blocks)
        if any(abs(means[i + 1] - means[i]) >= self.AUTO_ZERO_BLOCK_DELTA_G for i in range(len(means) - 1)):
            self._az_blocks.clear()  # 历史里有块间突升/突降：发生过交互，不是纯漂移
            return
        if means[-1] <= -self.AUTO_ZERO_TOTAL_TRIM_G:
            # 持续负读数：带物重启/带物去皮把基准抬高后拿走物品的残留
            # （真实负载不可能为负），直接 Trim 归零，无需累积。
            # 2026-09-21：负向同样受 MAX_TRIM 上限约束——无上限时一次 Trim 可吞掉
            # 整个载荷残留（实测 -300g 一次吃掉），与"带物首连去皮"缺陷组合成
            # "放 300g 显示 1g"事故；套上限后大幅残留分多次渐进归零，期间显示
            # 负数提示基准异常，而不是静默吞掉。
            drift = max(means[-1], -self.AUTO_ZERO_MAX_TRIM_G)
        else:
            drift = means[-1] - means[0]
            if abs(drift) < self.AUTO_ZERO_TOTAL_TRIM_G:
                return
            if abs(drift) > self.AUTO_ZERO_MAX_TRIM_G:
                self._az_blocks.clear()  # 可疑大幅移动，宁可不抖动基准
                return
        self._soft_zero_offset += drift
        self._ema_weight = None
        self._median_buf.clear()
        self._az_blocks.clear()
        self._az_last_trim = now
        print(f"[重量] 自动校准：慢漂移 {drift:+.1f}g 已归零（新基准 {self._soft_zero_offset:.1f}g）")

    def update_weight_data(self, weight, unit='g', raw=None, source=LOCAL_SOURCE):
        """更新重量数据（含空秤死区：漂移产生的小读数视为空秤）。

        weight 为显示值（EMA 平滑后，取整——0.x 级残余噪声对烹饪无意义，跳动反而碍眼）；
        raw 为即时原始值（保留 1 位小数），供下料达标检测使用
        （EMA 有数秒滞后，用它判定达标会过冲）。raw 缺省时与 weight 相同。
        source 指定写入哪个源槽（opi=远程推流 / local=本机串口），写入后重选生效源。
        """
        if abs(weight) < self.WEIGHT_DEAD_ZONE_G:
            weight = 0.0
        with self.lock:
            # 显示迟滞：偏离当前显示值不足 DISPLAY_HYSTERESIS_G 不更新，
            # 平滑值在 x.5 取整边界附近往复时显示整数保持不动
            if abs(weight - self._display_weight) >= self.DISPLAY_HYSTERESIS_G:
                self._display_weight = int(round(weight))
            data = {
                # 显示取整；raw 保留 1 位小数（回退时同样不取整，语义是未取整重量）
                "weight": self._display_weight,
                "unit": unit if unit else 'g',
                "stable": True,
                "timestamp": datetime.now().isoformat(),
                "raw": round(raw, 1) if raw is not None else round(weight, 1),
            }
            self.last_valid_weight = weight
            self._write_source_locked(source, data)

    def _write_source_locked(self, source: str, data: dict):
        """写入指定源槽并重选生效源（调用方须持有 self.lock）"""
        self.source_data[source] = data
        self.source_mono[source] = time.monotonic()
        self._select_source_locked()

    def _select_source_locked(self):
        """按优先级选出当前生效源（调用方须持有 self.lock）。

        远程（opi）源在 REMOTE_FRESH_SECONDS 内有新行则优先；断流回落本机 local；
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
        self.weight_data = selected

    def process_serial_line(self, line: str, source: str = LOCAL_SOURCE) -> bool:
        """处理一行板卡串口数据（本机 reader 与远程 Pi 推流共用的解析管线）。

        返回是否解析出重量。软件去皮/零点自动跟踪/增益修正/EMA 平滑全部在本方法内，
        远程数据与本机串口数据走完全相同的处理，仅写入的源槽不同。
        """
        weight, unit = self.parse_weight(line)
        if weight is not None:
            # 手动去皮采样：用原始读数（未减旧偏移）
            self._maybe_capture_tare(weight)
            # 软件去皮：减去零点基准
            weight = weight - getattr(self, "_soft_zero_offset", 0.0)
            # 空秤零点自动跟踪：在板卡克空间评估（Trim 的就是板卡基准），
            # 必须先于增益修正，否则 Trim 量会被 1/gain_factor 放大
            self._maybe_auto_zero(weight)
            # 两点标定增益修正：板卡直读克数偏高（50g 砝码实测 +6.7%），
            # 统一修正为真实克数后再进显示/下料检测
            weight *= self._gain_factor
            # 显示值走 中值剔毛刺 + EMA 平滑（压 ±0.6g 原生噪声），raw 即时值供下料检测
            raw_value = weight
            smoothed = self._apply_ema(self._median3(weight))
            self.update_weight_data(smoothed, unit if unit else 'g', raw=raw_value, source=source)
            return True

        # 尝试提取原始值并进行换算（备用方案）
        match = re.search(r'原始值[:]\s*(\d+)', line)
        if match and self.zero_value is not None:
            raw_value = int(match.group(1))
            # 直接使用原始值计算重量，不使用死区
            weight_value = (raw_value - self.zero_value) / self.CALIBRATION_FACTOR
            if weight_value < 0:
                weight_value = 0
            # 添加到滑动平均队列
            self.weight_queue.append(weight_value)
            avg_weight = sum(self.weight_queue) / len(self.weight_queue)
            self.update_weight_data(round(avg_weight), 'g', source=source)
        # 温重二合一板卡：同一行携带 Tmx/Tce/Tav/Tam 温度字段，
        # 代为解析后写入温度存储 local 槽（MLX90640 的 opi 槽 fresh 优先，此字段仅兜底）
        self._forward_temperature_fields(line)
        return weight is not None

    def _forward_temperature_fields(self, line):
        """从温重二合一板卡数据行提取温度字段，写入温度模块存储（惰性导入避免循环依赖）"""
        try:
            tmx = re.search(r'Tmx=(-?\d+\.?\d*)', line)
            tce = re.search(r'Tce=(-?\d+\.?\d*)', line)
            tav = re.search(r'Tav=(-?\d+\.?\d*)', line)
            tam = re.search(r'Tam=(-?\d+\.?\d*)', line)
            if not (tmx and tce and tav and tam):
                return
            from .temperature import apply_external_temperature
            apply_external_temperature(
                maximum=float(tmx.group(1)),
                target=float(tce.group(1)),
                average=float(tav.group(1)),
                sensor=float(tam.group(1)),
            )
        except Exception:
            pass

    def _has_new_device(self) -> bool:
        """检测是否有新设备插入"""
        try:
            current_ports = set()
            for p in serial.tools.list_ports.comports():
                if self._is_weight_sensor_port(p):
                    current_ports.add(p.device)
            
            has_new = False
            for port in current_ports:
                if port not in self.known_ports:
                    has_new = True
                    print(f"[重量] 检测到新设备插入: {port}")
                    break
            
            self.known_ports = current_ports
            return has_new
        except Exception as e:
            print(f"[重量] 检测新设备时出错: {e}")
            return False
    
    def _search_with_retries(self, max_retries: int = MAX_DEVICE_INSERTION_SEARCH) -> bool:
        """在检测到新设备时进行多次搜索"""
        for attempt in range(1, max_retries + 1):
            print(f"[重量] 设备插入搜索 {attempt}/{max_retries}...")
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
                            # 周期性重试：不使用"等待新设备插入"机制——放弃时快照的
                            # known_ports 会把重枚举后的端口误记为已知端口，导致
                            # _has_new_device 永远不触发（重连死锁）
                            print(f"[重量] 连续 {MAX_CONNECT_RETRIES} 次连接失败，转为每5秒周期性重试...")
                            while self.running:
                                time.sleep(5)
                                if self.find_port():
                                    print(f"[重量] 找到设备: {self.port}")
                                    connect_retries = 0  # 重置计数
                                    break
                            continue
                        time.sleep(5)
                        continue
                    connect_retries = 0  # 连接成功，重置计数

                if self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        # 低频 debug：每 ~25 行（约 5 秒）打一行原始数据，排障时确认板卡输出格式与数值
                        self._debug_line_count = getattr(self, "_debug_line_count", 0) + 1
                        if self._debug_line_count % 25 == 0:
                            print(f"[重量][debug] 原始行: {line[:100]}")
                        # 解析管线与本机/远程共用（process_serial_line），写入 local 源槽
                        self.process_serial_line(line, source=LOCAL_SOURCE)

            except Exception as e:
                print(f"读取重量数据出错: {e}")
                if self.ser:
                    try:
                        self.ser.close()
                    except:
                        pass
                self.ser = None
                connect_retries += 1
                if connect_retries >= MAX_CONNECT_RETRIES:
                    # 同上：周期性重试，避免"等待新设备插入"的 known_ports 死锁
                    print(f"[重量] 连续 {MAX_CONNECT_RETRIES} 次读取失败，转为每5秒周期性重试...")
                    while self.running:
                        time.sleep(5)
                        if self.find_port():
                            print(f"[重量] 找到设备: {self.port}")
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

    def get_weight_data(self):
        with self.lock:
            # 读取时重选一次：远程源断流老化后无需等下一行写入，读数即自动回落本机
            self._select_source_locked()
            return self.weight_data.copy()

    def send_relay_command(self, cmd: str) -> bool:
        """向板卡发送继电器单字节指令：'1'吸合(开) / '0'断开(关) / '3'断开50ms回弹(脉冲)。

        串口被读取线程独占，写指令走 io_lock 与读取互斥；未连接时直接失败返回。
        """
        if cmd not in ('0', '1', '3'):
            print(f"[继电器] 非法指令: {cmd!r}")
            return False
        with self.io_lock:
            if not self.ser or not self.ser.is_open:
                print("[继电器] 串口未连接，无法发送指令")
                return False
            try:
                # 与已验证可操控的 test_stm32_sensors.py 一致：继电器指令也带 \r\n
                self.ser.write((cmd + "\r\n").encode('ascii'))
                self.ser.flush()
                print(f"[继电器] 发送指令 '{cmd}' -> {self.port}")
                return True
            except Exception as e:
                print(f"[继电器] 发送指令失败: {e}")
                return False

# 全局重量传感器实例（延迟初始化）
_weight_reader = None

def _get_reader():
    global _weight_reader
    if _weight_reader is None:
        _weight_reader = WeightScaleReader()
    return _weight_reader

def get_weight():
    """获取当前重量数据（懒加载，不阻塞启动）"""
    try:
        reader = _get_reader()
        if not reader.running:
            reader.start()
        return reader.get_weight_data()
    except Exception as e:
        return {"weight": 0.0, "unit": "g", "stable": True, "timestamp": None, "error": str(e)}


def process_remote_line(line: str):
    """远程 Pi 推流的一行板卡串口数据 → 本地同款解析管线（opi 源槽）。

    由 remote_sensors 的 /ws/opi-sensors 通道调用。不触碰本机串口；
    解析/去皮/增益/EMA 与本机 reader 完全一致。
    """
    _get_reader().process_serial_line(line, source=REMOTE_SOURCE)


def ensure_remote_zero_baseline():
    """远程 opi 通道建立时的"连接时刻去皮"（对标本机 connect() 的 _record_zero）。

    板卡固件直读克数自带数百克偏移（2026-09-12 实测 ~568g），本地链路靠连接时刻
    采样建立 _soft_zero_offset；远程链路没有 connect 事件，若不补这一步，偏移
    原样透传（零点自动跟踪单次 Trim 上限 15g 兜不住）。幂等：已有任何源数据
    （含重连场景）则跳过——断流重连不能把秤上载荷误当零点；仅后端启动后首连
    时做一次采样去皮（复用 request_tare 的采样窗口，采样期间读数短暂带偏移属预期）。
    """
    try:
        reader = _get_reader()
        if reader.source_data:
            return {"triggered": False, "reason": "baseline_exists"}
        reader.request_tare(auto=True)
        return {"triggered": True}
    except Exception as e:
        return {"triggered": False, "error": str(e)}

def start_weight_monitoring(port=None, baudrate=WEIGHT_BAUDRATE):
    """启动重量监控"""
    try:
        reader = _get_reader()
        if port:
            reader.port = port
            reader.baudrate = baudrate
        reader.start()
        return {"status": "started", "port": reader.port, "baudrate": reader.baudrate}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def stop_weight_monitoring():
    """停止重量监控"""
    try:
        if _weight_reader:
            _weight_reader.stop()
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def tare_weight():
    """手动去皮：以接下来2秒的实测均值作为新零点（秤面需为空）"""
    try:
        reader = _get_reader()
        if not reader.running:
            reader.start()
            time.sleep(1.0)
        reader.request_tare()
        return {"success": True, "message": "去皮完成，当前读数已归零"}
    except Exception as e:
        return {"success": False, "error": str(e)}

RELAY_ACTIONS = {"on": "1", "off": "0", "pulse": "3"}

def relay_control(action: str) -> dict:
    """手动控制继电器（下料机开关）：on=吸合(开) / off=断开(关) / pulse=断开50ms回弹。"""
    cmd = RELAY_ACTIONS.get(action)
    if cmd is None:
        return {"status": "error", "error": f"未知动作 {action}，可选 on/off/pulse"}
    try:
        reader = _get_reader()
        if not reader.running:
            reader.start()
            time.sleep(0.5)
        ok = reader.send_relay_command(cmd)
        return {"status": "ok" if ok else "error", "action": action, "command": cmd}
    except Exception as e:
        return {"status": "error", "error": str(e)}

if __name__ == "__main__":
    print("="*50)
    print("重量传感器测试程序")
    print("="*50)

    reader = WeightScaleReader()
    if reader.find_port():
        print(f"\n找到传感器: {reader.port} @ {reader.baudrate}")
        reader.start()

        print("\n开始读取重量数据 (Ctrl+C 退出)...")
        try:
            while True:
                data = reader.get_weight_data()
                print(f"\r重量: {data['weight']} {data['unit']}  时间: {data['timestamp']}", end="")
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n\n停止监控...")
            reader.stop()
    else:
        print("\n未找到重量传感器")
