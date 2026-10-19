"""opi_serial_common.py — 桥接客户端共享配置常量与日志（2026-09-21 从 opi_sensors_client.py 拆出）。

只做搬运，不改任何默认值/环境变量名/日志格式。所有常量原值与注释见原文件同一位置。
注意：_BASE_DIR 取本模块所在目录——本模块与三个客户端脚本同目录，取值与拆分前一致。
"""
from __future__ import annotations

import logging
import os

WEIGHT_DEVICE = os.getenv("OPI_WEIGHT_DEVICE", "")
WEIGHT_BAUD = int(os.getenv("OPI_WEIGHT_BAUD", "9600") or 9600)
VALVE_BAUD = int(os.getenv("OPI_VALVE_BAUD", "9600") or 9600)
VALVE_PROTOCOL = os.getenv("OPI_VALVE_PROTOCOL", "auto").lower()  # auto | single | word
# 开阀故障切换（2026-09-15 用户要求「电磁阀1 超过 5 秒没启动就换电磁阀2」）：
# 主阀在 OPEN_CONFIRM_TIMEOUT_S 内没拿到 'OK ON' 确认就改用备用阀。
# ⚠️ 前提是两个阀接**同一条料路**（互为备用）。若它们各管不同调料，切换会把料出错，
#    此时把 OPI_VALVE_FAILOVER 设成空串关闭本功能。
try:
    OPEN_CONFIRM_TIMEOUT_S = max(0.5, float(os.getenv("OPI_VALVE_OPEN_TIMEOUT_S", "5") or 5))
except ValueError:
    OPEN_CONFIRM_TIMEOUT_S = 5.0
FAILOVER_SPEC = os.getenv("OPI_VALVE_FAILOVER", "1>2").strip()   # "1>2,2>1" 可互备；空串=关闭
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHANNEL_STATE_FILE = os.getenv(
    "OPI_VALVE_CHANNELS_FILE", os.path.join(_BASE_DIR, ".valve_channels.json")
)
# 串口角色档案（2026-09-15）：识别一次就把「USB 拓扑段 → 角色」落盘，
# 之后只要当前设备集合与档案一致就直接绑定、**不再扫描**（扫描一次要 5~10 秒）。
SERIAL_ROLE_FILE = os.getenv(
    "OPI_SERIAL_ROLES_FILE", os.path.join(_BASE_DIR, ".serial_roles.json")
)
MAX_VALVE_CHANNELS = 4       # 最多识别几个阀口（现场 2 个，留余量）

MAX_DEVICE_INDEX = 10
WEIGHT_IDENTIFY_S = 2.0      # 候选口静读这么久，出现 Weight 数据行即认定称重板卡
STATUS_TIMEOUT_S = 0.5       # STATUS 应答等待
OPEN_MAX_ATTEMPTS = 3        # 开阀最大发送次数（与后端 valve.py 一致）
OPEN_RETRY_DELAY_S = 0.1
_OPEN_ACK_WAIT_S = 1.0       # 开/关阀后等待 OK ON/OK OFF 应答的时长
_VALVE_ACK_PROBE_S = 1.2     # 分类/验证时等 'F' 的 OK OFF 应答时长
# 1.2s（原 0.8s）：2026-09-19 重建后首次扫描时阀板上电未就绪没应答，被误记 other，
# 且档案"设备集合未变就沿用"导致永不重扫、阀口一直丢失——放宽窗口 + 删档案重扫双保险
RECONNECT_BACKOFF_MAX = 10.0
_CLASSIFY_TTL_S = 60.0       # 分类结果缓存时长（称重/阀两条找口路径共用一次探测）。
# 2026-09-15：由 10s 放宽到 60s——分类要逐口探测（阀口发 'F' 等 0.8s、非阀口静读 2s，
# 全扫一遍 3~4s），缓存太短会让"隔十几秒再操作"每次都吃一次全量重扫、命令响应明显变慢。
# 插拔设备后最多等 60s 自然重扫，或重启服务立即生效。
_SILENT_STREAK_LIMIT = 3             # 连续无应答多少次才丢弃已知口（原为 2，对密集命令太敏感）
_RECLASSIFY_MIN_INTERVAL_S = 30.0    # 两次"因无应答而重分类"之间的最小冷却（秒），防重分类风暴

# 2026-09-16 秤无数据故障后加固：半死 WS 连接（对端不收/不返回）会让 await ws.send()
# 永久挂起，把 weight_loop 卡死在分发这一步，表现为"阀正常、秤没数"且进程不死、
# systemd 不会重启。给发送加硬超时，并给整个称重循环加"连续多久没完成一轮分发"的
# 总看门狗（旧的 15s 判断只在 readline 返回空行时才生效，send 挂起时根本走不到）。
SEND_TIMEOUT_S = 2.0             # 单条数据向单个后端发送的超时（秒）
WEIGHT_DISPATCH_STALE_S = 20.0   # 超过这么久没成功分发一轮就重找称重板卡
READ_HARD_TIMEOUT_S = 5.0        # readline 硬超时（秒）：ch341 被集线器电气抖动楔死时，
                                 # 内核态 readline 会卡数分钟不返回——超时强制关开串口解卡

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 三个客户端各自 getLogger 不同名字；opi-sensors 的 logger 供 classify/valve/weight
# 子模块共享（拆分前后日志行完全一致）。
log_opi_sensors = logging.getLogger("opi-sensors")


def open_state_file(env_path: str, default_name: str, mode: str):
    """状态 JSON 的统一打开通道（串口角色档案 / 阀通道绑定表）。

    默认落在脚本同目录；OPI_*_FILE env 覆盖也必须仍落在脚本目录内（realpath 归一 +
    目录限制，防误配路径穿越）。现场部署未使用 env 覆盖，默认行为与拆分前完全一致。
    """
    raw = (env_path or "").strip()
    root = os.path.realpath(_BASE_DIR)
    candidate = os.path.realpath(raw if raw else os.path.join(_BASE_DIR, default_name))
    if not candidate.startswith(root + os.sep):
        raise ValueError(f"状态文件路径必须在 {root} 内（env 覆盖不受支持）: {raw}")
    return open(candidate, mode, encoding="utf-8")
