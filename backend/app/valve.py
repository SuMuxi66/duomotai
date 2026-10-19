"""电磁阀控制器（新版下料机执行器串口）——协议以实测可用的 电磁阀/valve_control.py 为准。

硬件：独立单片机 + USB-TTL 串口，9600 8N1。

协议（2026-09-07 用户现场实测）：
    发送单字节 ASCII：'O' = 开阀（开始下料），'F' = 关阀（停止下料）
    无行尾、无应答——写成功即成功（fire-and-forget，与 valve_control.py 完全一致）

⚠️ 仓库固件源码 solenoid_valve_controller.ino 是全词协议（ON/OFF/STATUS + OK 应答），
   但实测后端按全词发指令无法驱动板子、valve_control.py 单字符实测可用——
   板上烧的固件认单字符。2026-09-09 起连接时自动识别协议（发 STATUS 探测：
   有应答=全词固件，走 ON/OFF/STATUS + 应答校验；静默=单字符固件，走裸 'O'/'F'），
   重烧 .ino 全词固件后无需改代码，重启后端即自动切全词模式。

自动探测：遍历串口发 "STATUS\\r\\n"（无副作用）——收到 OK/ON/OFF/ERR 即认定（ERR 覆盖
对未知指令回 ERR 的固件）；有输出但非 token 的口（秤的 Weight 数据行）明确排除；
完全静默的口记为候选，若恰好只剩一个（另一个口通常被秤线程占用打不开）即推断为阀，
≥2 个静默口无法区分不乱认。探测彻底失败时用 VALVE_PORT env 指定端口直连。

开阀确认（2026-09-09，用户要求）：valve_open_verified()——
- 全词固件：ON 指令自带 OK 应答 + STATUS 状态查询双重确认，失败如实返回；
- 单字符固件（当前实机）：发 'O' 后按文档协议查 STATUS——应答 ON 即确认开启；
  应答 OFF 或静默则重发 'O'（幂等无副作用），最多 _OPEN_MAX_ATTEMPTS 次。
  有应答但始终 OFF → 如实失败；全程静默 → 按旧"写成功即成功"语义放行，
  重发本身已提高瞬时丢指令的命中率。STATUS 查询对单字符固件无副作用
  （S/T/A/T/U/S 均非 'O'/'F' 指令字节）。
"""
import time
from threading import Lock

from .logger import backend_log


def _log(msg: str) -> None:
    """stdout + 结构化日志（logs/backend/<日期>.md）双写。
    电磁阀事件此前只 print 到 stdout，结构化日志零记录——
    2026-09-07 下料故障排障时主排障入口完全不可见。"""
    print(msg)
    backend_log(msg)

try:
    import serial
    import serial.tools.list_ports
    _HAS_SERIAL = True
except Exception:
    _HAS_SERIAL = False

# 探测应答识别：全词固件回 OK/ON/OFF；单字符固件对未知指令可能回 ERR
_PROBE_TOKENS = {"OK", "ERR", "ON", "OFF"}
_READY_TOKEN = "READY"

# 默认总超时（秒）：探测时等待应答的最大时间
_DEFAULT_TIMEOUT = 1.5

# 全局串口连接 + 锁（参考 weight.py 的单例模式）
_ser = None
_port = None
_baudrate = 9600
_lock = Lock()
# 当前固件协议（连接时自动识别，见 _probe_protocol）：
#   "full" = 全词固件（ON/OFF/STATUS + OK 应答）；"char" = 单字符固件（裸 'O'/'F'，无应答）
_protocol: str | None = None


def _open_serial(port: str, baudrate: int):
    """打开串口并清空输入缓冲；失败返回 None。"""
    if not _HAS_SERIAL:
        return None
    try:
        s = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=0.1,  # 短读超时，探测轮询用
        )
        s.reset_input_buffer()
        return s
    except Exception:
        return None


def _readline_poll(s, timeout: float):
    """探测用：轮询 in_waiting 收行；总超时内收到一行即返回（strip 后字符串）。
    读超时返回 None。READY 是固件上电主动报活，跳过继续等真正应答。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            n = s.in_waiting
        except Exception:
            return None
        if n > 0:
            try:
                raw = s.readline()
            except Exception:
                return None
            line = raw.decode("ascii", errors="ignore").strip()
            if line == "":
                continue
            if line.upper() == _READY_TOKEN:
                continue
            return line
        time.sleep(0.02)
    return None


def _parse_probe_response(line: str):
    """解析探测应答行：命中 OK/ERR/ON/OFF（精确或前缀）返回归一化 token，否则 None。
    前缀匹配应对固件应答可能带尾部字符。"""
    if line is None:
        return None
    upper = line.upper().strip()
    if upper in _PROBE_TOKENS:
        return upper
    for tok in _PROBE_TOKENS:
        if upper.startswith(tok):
            return tok
    return None


def _write_byte(s, char: str) -> bool:
    """写单字节 ASCII 指令（无行尾），写后清输入缓冲丢掉固件可能的回显。
    与实测脚本一致：valve_control.py 直接 write('O'/'F')，不读应答。"""
    try:
        s.write(char.encode("ascii"))
        s.flush()
        s.reset_input_buffer()
        return True
    except Exception:
        return False


def _close_serial():
    """关闭并置空串口连接（已在外部锁内调用）。"""
    global _ser
    if _ser is not None:
        try:
            _ser.close()
        except Exception:
            pass
        _ser = None


def _probe_protocol() -> str:
    """在已连接的串口上识别固件协议（调用前 _ser 已就绪、须持锁）。

    发 STATUS 查询：有应答（OK/ON/OFF/ERR）= 全词固件；静默 = 单字符固件。
    结果写入全局 _protocol 并返回。识别必须先于 'F' 自检——裸 'F' 会滞留在
    全词固件的行缓冲里，后续 STATUS 会被拼成 "FSTATUS" 误报 ERR。
    """
    global _protocol
    try:
        _ser.reset_input_buffer()
        _ser.write(_STATUS_QUERY)
        _ser.flush()
        line = _readline_poll(_ser, _PROTOCOL_WAIT_S)
    except Exception:
        line = None
    if line is not None and _parse_probe_response(line) is not None:
        _protocol = "full"
    else:
        _protocol = "char"
    return _protocol


def find_port() -> bool:
    """探测并连接电磁阀串口。VALVE_PORT env 非空时直连该口不搜索
    （直连后先发 STATUS 识别协议——静默的单字符固件再写 'F' 安全自检，
    'F' 不会开启任何设备，误指端口也无副作用）；
    否则遍历系统串口发 STATUS\\r\\n，收到 OK/ON/OFF/ERR 即认定。"""
    global _ser, _port, _baudrate, _protocol
    from .config import settings

    _baudrate = settings.valve_baudrate
    _protocol = None

    configured = settings.valve_port
    if configured:
        s = _open_serial(configured, _baudrate)
        if s is not None:
            _ser = s
            _port = configured
            # 先识别协议再自检：顺序不能反（见 _probe_protocol 注释）
            if _probe_protocol() == "full":
                _log(f"[电磁阀] 已连接 {configured} @ {_baudrate}（全词协议，STATUS 应答正常）")
                return True
            if _write_byte(s, "F"):
                _log(f"[电磁阀] 已连接 {configured} @ {_baudrate}（单字符协议，写 'F' 自检通过）")
                return True
            _close_serial()
        _log(f"[电磁阀] 指定端口 {configured} 无法打开或写入")
        return False

    if not _HAS_SERIAL:
        _log("[电磁阀] pyserial 未安装，无法探测串口")
        return False

    try:
        ports = serial.tools.list_ports.comports()
    except Exception as e:
        _log(f"[电磁阀] 无法列出串口: {e}")
        return False
    if not ports:
        _log("[电磁阀] 未发现任何串口")
        return False

    _log(f"[电磁阀] 候选串口: {[p.device for p in ports]}")
    silent_candidates = []  # 能打开但对 STATUS 完全静默的口（单字符固件的特征）
    for p in ports:
        device = p.device
        s = _open_serial(device, _baudrate)
        if s is None:
            # 打开失败（PermissionError/OSError，可能被秤占用）——跳过
            continue
        try:
            s.write(b"STATUS\r\n")
            s.flush()
            line = _readline_poll(s, _DEFAULT_TIMEOUT)
            resp = _parse_probe_response(line)
            if resp is not None:
                _ser = s
                _port = device
                _protocol = "full"  # STATUS 有应答 = 全词固件
                s.reset_input_buffer()
                _log(f"[电磁阀] 找到电磁阀: {device} @ {_baudrate} (STATUS -> {resp})")
                return True
            if line is not None:
                # 有输出但不是电磁阀 token（如秤的 Weight 数据行）——明确不是阀
                try:
                    s.close()
                except Exception:
                    pass
                continue
            # 完全静默：单字符固件对 STATUS 不回任何东西——暂记候选，保持打开
            silent_candidates.append((device, s))
        except Exception:
            try:
                s.close()
            except Exception:
                pass
    # 静默候选推断：现场通常阀+秤两个口，秤口被秤线程占用打不开——
    # 恰好剩一个"能打开且静默"的口即推断为阀。≥2 个静默口无法区分，不乱认。
    if len(silent_candidates) == 1:
        device, s = silent_candidates[0]
        _ser = s
        _port = device
        _protocol = "char"  # 对 STATUS 静默 = 单字符固件
        _log(f"[电磁阀] 各候选对 STATUS 静默，唯一可开候选 {device} 推断为电磁阀（单字符固件）")
        return True
    for _, s in silent_candidates:
        try:
            s.close()
        except Exception:
            pass
    _log("[电磁阀] 未找到电磁阀串口——请设置 VALVE_PORT 环境变量指定端口")
    return False


def _ensure_serial() -> bool:
    """确保串口已连接；断开时尝试重连。须在外部锁内调用。"""
    # touch-reload marker: 静默候选推断逻辑
    global _ser
    if _ser is None:
        return find_port()
    return True


def _send(char: str) -> bool:
    """底层单字节发送（带自愈）：写失败/串口丢失时关闭串口重试一次。"""
    with _lock:
        for attempt in (1, 2):
            if not _ensure_serial():
                return False
            if _write_byte(_ser, char):
                return True
            _log(f"[电磁阀] 写 '{char}' 失败，断开串口等待重连")
            _close_serial()
        return False


# ==================== 高层 API（dispense.py 调用） ====================


# ---- 开阀确认（STATUS 查询 + 重发）----
_OPEN_MAX_ATTEMPTS = 3      # 开阀指令最大发送次数（含首次）
_STATUS_WAIT_S = 0.25       # STATUS 应答等待（固件有应答时远快于此，静默即为此耗时）
_OPEN_RETRY_DELAY_S = 0.1   # 重发间隔
_STATUS_QUERY = b"STATUS\r\n"
# 连接时协议识别的应答等待（一次性开销，给足固件应答时间，避免把全词误判成单字符）
_PROTOCOL_WAIT_S = 0.4


def _current_protocol() -> str | None:
    """确保串口已连接，返回当前协议（"full"/"char"）；连不上返回 None。

    高层指令函数先调它——保证协议在连接（含识别）完成后才被读取，
    避免"未连接时 _protocol 还是 None 就按单字符裸发"的窗口。
    """
    with _lock:
        if not _ensure_serial():
            return None
        return _protocol or "char"


def _send_word(cmd: str) -> tuple[bool, str | None]:
    """全词模式发指令（cmd + CRLF）并读应答（如 ON -> OK）。

    返回 (串口写成功, 应答行 or None)。写异常时断开串口自愈重连一次
    （与 _send 的自愈策略一致）。
    """
    with _lock:
        for _attempt in (1, 2):
            if not _ensure_serial():
                return False, None
            if _protocol != "full":
                # 重连后协议被重新识别成了单字符——不再是全词通道
                return False, None
            try:
                _ser.reset_input_buffer()
                _ser.write((cmd + "\r\n").encode("ascii"))
                _ser.flush()
                line = _readline_poll(_ser, _STATUS_WAIT_S)
                return True, (line.strip().upper() if line else None)
            except Exception:
                _log(f"[电磁阀] 写 '{cmd}' 失败，断开串口等待重连")
                _close_serial()
        return False, None


# ---- 远程通道（2026-09-12 硬件迁移：电磁阀移到香橙派）----
# Pi 经 /ws/opi-sensors 连接时，开/关阀命令转发给 Pi 端客户端执行（它对本机串口
# 做 'O'/'F' 写入与 STATUS 校验、结果异步回报）；Pi 未连接自动回落本机串口路径。
# 下行命令为 fire-and-forget：调用方多在事件循环线程，阻塞等 WS 回包会与
# 接收循环死锁；可靠性由 Pi 端重发 + dispense 监测循环保活重发兜底。


def _channel_label(channel: int) -> str:
    """日志里的通道名（0/缺省 = 全部通道）"""
    return "全部通道" if not channel or channel <= 0 else f"电磁阀{int(channel)}"


def _remote_valve_command(action: str, channel: int = 0, keepalive: bool = False) -> bool | None:
    """尝试走远程通道下发阀命令。返回 True=已调度、False=调度失败、None=无远程通道。

    channel: 正整数只控该通道；0/缺省 = 全部通道（关阀广播，多阀时防漏关）。
    keepalive: 下料期间的周期保活重发标记——Pi 端收到后只写指令立即返回，
      不等串口 ACK；正常开阀不带该标记（要确认真的开到位）。
    """
    try:
        from . import remote_sensors
    except Exception:
        return None
    if not remote_sensors.is_connected():
        return None
    result = remote_sensors.send_valve_command(action, channel, keepalive=keepalive)
    if result.get("ok"):
        _log(f"[电磁阀] 远程通道下发命令: {action}（{_channel_label(channel)}）")
        return True
    _log(f"[电磁阀] 远程通道下发失败: {result.get('error')}")
    return False


def valve_query_state(channel: int = 1) -> str | None:
    """查询阀状态（文档协议 STATUS）：返回 "ON"/"OFF"；无应答或无法判定返回 None。

    None 不代表失败——单字符固件对 STATUS 静默，只说明"无法确认"。
    远程通道下状态查询为异步事件回报，此处一律返回 None（无法同步确认）。
    channel 仅作用于远程多阀场景（本机串口路径只有一个阀）。
    """
    if _remote_valve_command("status", channel) is not None:
        return None
    with _lock:
        if not _ensure_serial():
            return None
        try:
            _ser.reset_input_buffer()
            _ser.write(_STATUS_QUERY)
            _ser.flush()
            line = _readline_poll(_ser, _STATUS_WAIT_S)
        except Exception:
            return None
        if line is None:
            return None
        upper = line.upper().strip()
        if upper.startswith("ON"):
            return "ON"
        if upper.startswith("OFF"):
            return "OFF"
        return None


def valve_open_verified(max_attempts: int = _OPEN_MAX_ATTEMPTS, channel: int = 1) -> bool:
    """开阀（带状态确认与重发）——pulse_dispense / start_dispense 首次开阀用。

    channel：远程多阀场景指定通道（默认 1 = 电磁阀1，即原下料阀，行为与改造前一致）。

    - 全词固件：发 ON\r\n 校验 OK 应答，再查 STATUS 双重确认；失败如实返回 False。
    - 单字符固件（当前实机）：每次发 'O' 后查一次 STATUS——
      应答 ON → 确认开启；应答 OFF → 指令未生效，重发；
      无应答（静默固件）→ 同样重发（'O' 幂等，重复发送无副作用）。
      达 max_attempts 次后：有应答但始终 OFF → False（板卡明确没开）；
      全程静默 → True（与旧"写成功即成功"语义兼容，避免静默固件被误判失败）。
    远程通道：命令转发给 Pi（Pi 端自行做 'O' + STATUS 校验重发并异步回报结果），
    调度成功即返回 True，不做阻塞等待。
    """
    remote = _remote_valve_command("open", channel)
    if remote is not None:
        return remote

    mode = _current_protocol()
    if mode is None:
        _log("[电磁阀] 开阀失败: 串口未连接")
        return False

    if mode == "full":
        for attempt in range(1, max_attempts + 1):
            ok, resp = _send_word("ON")
            if ok and resp and resp.startswith("OK"):
                if valve_query_state() == "ON":
                    if attempt > 1:
                        _log(f"[电磁阀] 开阀确认 ON（重发第 {attempt} 次后）")
                    return True
                _log(f"[电磁阀] ON 应答 OK 但状态查询非 ON，重发（{attempt}/{max_attempts}）")
            else:
                _log(f"[电磁阀] 开阀应答异常（{resp if ok else '无应答'}），重发（{attempt}/{max_attempts}）")
            if attempt < max_attempts:
                time.sleep(_OPEN_RETRY_DELAY_S)
        # 最后兜底再查一次状态
        return valve_query_state() == "ON"

    last_state = None
    for attempt in range(1, max_attempts + 1):
        if not _send("O"):
            _log(f"[电磁阀] 开阀失败: 串口写入不成功（第 {attempt} 次发送）")
            return False
        last_state = valve_query_state()
        if last_state == "ON":
            if attempt > 1:
                _log(f"[电磁阀] 开阀确认 ON（重发第 {attempt} 次后收到应答）")
            return True
        if last_state == "OFF":
            _log(f"[电磁阀] 状态应答 OFF，开阀指令未生效，重发（{attempt}/{max_attempts}）")
        elif attempt < max_attempts:
            _log(f"[电磁阀] 无状态应答（静默固件或瞬时丢失），保险重发开阀指令（{attempt}/{max_attempts}）")
        if attempt < max_attempts:
            time.sleep(_OPEN_RETRY_DELAY_S)
    if last_state == "OFF":
        _log(f"[电磁阀] 开阀失败: 重发 {max_attempts} 次后状态应答仍为 OFF")
        return False
    return True


def valve_open(channel: int = 1, keepalive: bool = False) -> bool:
    """开阀（开始下料）。单字符固件：写裸 'O'，写成功即成功；
    全词固件：发 ON\\r\\n 并校验 OK 应答。Pi 通道连接时优先转发远程。

    channel：远程多阀通道号（默认 1 = 电磁阀1）。
    keepalive：下料期间的周期保活重发。远程通道会带该标记下发，Pi 端只写指令
    立即返回（不等串口 ACK、不做故障切换）；本机串口路径本就是"写成功即成功"，
    无差别。
    """
    remote = _remote_valve_command("open", channel, keepalive=keepalive)
    if remote is not None:
        return remote
    mode = _current_protocol()
    if mode == "full":
        ok, resp = _send_word("ON")
        if ok and resp and resp.startswith("OK"):
            return True
        _log(f"[电磁阀] 开阀失败: 应答异常（{resp if ok else '无应答'}）")
        return False
    try:
        if _send("O"):
            return True
        _log("[电磁阀] 开阀失败: 串口写入不成功")
        return False
    except Exception as e:
        _log(f"[电磁阀] 开阀异常: {e}")
        return False


def valve_close(channel: int = 0) -> bool:
    """关阀（停止下料）。单字符固件：写裸 'F'，写成功即成功；
    全词固件：发 OFF\\r\\n 并校验 OK 应答。Pi 通道连接时优先转发远程。

    ⚠️ channel 默认 0 = **关闭全部通道**（2026-09-15 双阀改造）：现场有两个阀，
    关阀是安全兜底动作，只关一个会留下另一个通电开启的阀（持续通电会烧毁）。
    要只关某个阀时显式传 channel=N。
    """
    remote = _remote_valve_command("close", channel)
    if remote is not None:
        return remote
    mode = _current_protocol()
    if mode == "full":
        ok, resp = _send_word("OFF")
        if ok and resp and resp.startswith("OK"):
            return True
        _log(f"[电磁阀] 关阀失败: 应答异常（{resp if ok else '无应答'}）")
        return False
    try:
        if _send("F"):
            return True
        _log("[电磁阀] 关阀失败: 串口写入不成功")
        return False
    except Exception as e:
        _log(f"[电磁阀] 关阀异常: {e}")
        return False


def valve_is_ready() -> bool:
    """快速判断执行通道是否可用：Pi 外设通道在线，或本机串口能连上即就绪。
    不发探测指令——'O' 会开阀、'F' 会干扰进行中的下料，都不能当探针乱发。"""
    try:
        from . import remote_sensors
        if remote_sensors.is_connected():
            return True
    except Exception:
        pass
    try:
        with _lock:
            return _ensure_serial()
    except Exception:
        return False
