"""下料机自动投放状态机（双通道执行器）。

执行器通道由 settings.dispense_executor 选择：
  - valve（默认）：新版独立串口电磁阀控制器，正逻辑——ON=开阀下料、OFF=关阀停止。
    旧板卡继电器的 NC 反极性（'0'=下料/'1'=停止）不适用于新机。
  - legacy_relay：旧温重二合一板卡继电器，NC 触点反极性——
    '0'(断开, NC导通) = 下料机通电 = 下料，'1'(吸合, NC断开) = 下料机断电 = 停止。
    （'3' 仅 50ms 点动，量太小不可用）

流程：AI 算出调料克数 -> 记录目标(当前重量+克数)
      -> 开阀/发'0' 持续下料 -> 每 POLL_INTERVAL 秒查重量
      -> 达到目标 -> 关阀/发'1' 断电停止。
"""
import asyncio
import time
from threading import Lock

from .weight import get_weight, relay_control
from . import valve as valve_mod
from .config import settings
from .logger import backend_log


def _log(msg: str) -> None:
    """stdout + 结构化日志（logs/backend/<日期>.md）双写——下料事件此前只在 stdout。"""
    print(msg)
    backend_log(msg)

POLL_INTERVAL = 0.5        # 下料监测周期（秒）：缩短以减小过冲（板卡数据约 0.7s/行）
# 保活重发间隔（2026-09-15）：重复下发开阀指令与称重轮询解耦。阀的"自动关闭"风险
# 不需要 0.5 秒就补一次，而远程通道每条命令在 Pi 端都要等串口 ACK（约 0.3~1s），
# 按 0.5s 发会把 Pi 的命令队列压积压——实测同一秒积压 12 条，手动关阀排到队尾、
# 响应拖到数秒。称重仍保持 POLL_INTERVAL 精度，只有保活重发按此间隔。
KEEPALIVE_INTERVAL = 3.0
TIMEOUT_SECONDS = 120      # 单次下料超时（秒），超时强制停止防意外
# 断电提前量（克）：达标断电后管路余料仍会落下（实测超出目标 ~0.8g），提前断电对冲，
# 落点≈目标。换不同下料机/物料后如偏差变化，按实测调整此值。
# 0.8g 为旧机实测，换新下料机后需重新标定。
STOP_LEAD_G = 0.8

_state = {
    "active": False,
    "target": 0.0,          # 目标重量（绝对克数）
    "target_g": 0.0,        # 本次要投放的净克数
    "started_weight": 0.0,  # 开始时秤面重量
    "started_at": 0.0,
    "finished": False,
    "finished_weight": 0.0,
    "reason": "",           # 结束原因: completed / timeout / stopped / error
}
_task = None
_state_lock = Lock()

# 脉冲下料进行中标志（2026-09-15）：脉冲出料（放盐 / 定时定量）与闭环下料（start_dispense）
# 共用同一台下料机，必须互斥。旧实现是 pulse_dispense() 第一行直接 await stop_dispense()——
# 会把正在进行的闭环下料**无声掐断**（表现为"下料刚开始就断"）。现改为**后到者被拒绝**
# 并如实告知原因，调用方可转述给用户。
_pulse_active = False
_pulse_lock = Lock()


def pulse_in_progress() -> bool:
    """脉冲下料是否正在进行"""
    with _pulse_lock:
        return _pulse_active


def dispense_busy_reason() -> str:
    """下料机被占用时的可读原因（空闲返回空串）——互斥预检用。"""
    with _pulse_lock:
        if _pulse_active:
            return "下料机正在脉冲出料中，请等本次结束"
    with _state_lock:
        active = bool(_state.get("active"))
        target_g = float(_state.get("target_g") or 0.0)
        target = float(_state.get("target") or 0.0)
    if not active:
        return ""
    remain = max(0.0, target - _now_weight())
    return (f"下料机正在下料中（投放 {target_g:.1f} 克，剩余约 {remain:.1f} 克），"
            "请等它结束，或先说停止再放盐")


def _is_valve_channel() -> bool:
    """当前是否走新版电磁阀通道。"""
    return settings.dispense_executor == "valve"


def _dispense_start(keepalive: bool = False) -> bool:
    """启动下料（正逻辑语义）：
    - valve 通道：发 ON 开阀；keepalive=True 走"只写指令、不等应答"的保活快速路径
      （下料期间的周期重发用，避免远程通道命令积压）
    - legacy_relay 通道：发 'off'（='0'=NC导通=下料机通电=下料）"""
    if _is_valve_channel():
        return valve_mod.valve_open(keepalive=keepalive)
    return _send_relay("off")


def _dispense_start_verified() -> bool:
    """首次开阀（带状态确认与重发，2026-09-09 用户要求）：
    - valve 通道：valve_open_verified——发 'O' 后查 STATUS，非 ON 则重发，
      最多 3 次；有应答但始终 OFF 如实失败，静默固件维持写成功即成功。
    - legacy_relay 通道：继电器无状态查询，保持原行为。
    仅在 start_dispense / pulse_dispense 的开阀时刻使用；
    _monitor_loop 内的周期性保活重发仍走轻量的 _dispense_start()。"""
    if _is_valve_channel():
        return valve_mod.valve_open_verified()
    return _send_relay("off")


def _dispense_stop() -> bool:
    """停止下料（正逻辑语义）：
    - valve 通道：发 OFF 关阀
    - legacy_relay 通道：发 'on'（='1'=吸合=NC断开=下料机断电=停止）"""
    if _is_valve_channel():
        return valve_mod.valve_close()
    return _send_relay("on")


def _now_weight() -> float:
    """读取当前重量：优先即时原始值 raw——显示值是 EMA 平滑（约 2s 滞后），
    达标检测用它会导致持续过冲后才停。"""
    try:
        data = get_weight()
        raw = data.get("raw")
        return float(raw if raw is not None else data.get("weight") or 0.0)
    except Exception:
        return 0.0


def _send_relay(action: str) -> bool:
    """发送继电器指令并返回是否成功（on/off/pulse）——仅 legacy_relay 通道使用。"""
    try:
        res = relay_control(action)
        return res.get("status") == "ok"
    except Exception:
        return False


async def _monitor_loop() -> None:
    """下料主循环：开阀/发'0'持续下料，每 POLL_INTERVAL 秒查重量，达标/超时关阀/发'1'断电停止。

    保活重发按 KEEPALIVE_INTERVAL 节流（2026-09-15）：首轮与到期轮走完整开阀（带串口应答
    确认），其余轮次走"只写不等应答"的保活路径——否则 0.5 秒一条的远程命令会在 Pi 端
    积压，手动关阀要排队数秒才生效。称重轮询精度不受影响（仍每 POLL_INTERVAL 一次）。
    """
    deadline = time.time() + TIMEOUT_SECONDS
    fail_count = 0
    last_keepalive = 0.0
    try:
        while True:
            now = time.monotonic()
            due = now - last_keepalive >= KEEPALIVE_INTERVAL
            if due:
                last_keepalive = now
            if not _dispense_start(keepalive=not due):   # 开阀/发'0'：持续通电下料
                fail_count += 1
                if fail_count >= 3:               # 连续失败（串口断开等）-> 中止
                    with _state_lock:
                        _state["active"] = False
                        _state["finished"] = True
                        _state["reason"] = "error"
                    _log("[下料] 连续发送失败，串口异常，中止下料")
                    return
            else:
                fail_count = 0
            await asyncio.sleep(POLL_INTERVAL)  # 下料间隔后查一次重量
            with _state_lock:
                if not _state["active"]:
                    return
                target = _state["target"]
                started_weight = _state["started_weight"]

            weight = _now_weight()
            # 达标条件：weight >= target - STOP_LEAD_G（断电后管路余料 ~0.8g 继续落下，
            # 提前断电对冲，落点≈目标）。注意该提前量必须有实测依据——曾误写 2.0g 大容差
            # 导致设 3g 实际 1g 出头就停；0.8g 来自 2026-09-03 旧机实测（目标 3.5 实到 4.3）。
            # weight > started_weight 防空秤/未下料误判。
            if weight >= target - STOP_LEAD_G and weight > started_weight:
                _dispense_stop()  # 关阀/吸合态 = NC 断开 = 下料机断电
                with _state_lock:
                    _state["active"] = False
                    _state["finished"] = True
                    _state["finished_weight"] = weight
                    _state["reason"] = "completed"
                _log(f"[下料] 完成: 目标 {target:.1f}g, 实到 {weight:.1f}g, 下料机已断电")
                return

            if time.time() > deadline:
                _dispense_stop()
                with _state_lock:
                    _state["active"] = False
                    _state["finished"] = True
                    _state["finished_weight"] = weight
                    _state["reason"] = "timeout"
                _log(f"[下料] 超时({TIMEOUT_SECONDS}s)，强制断电停止下料机")
                return
    except asyncio.CancelledError:
        _dispense_stop()  # 被取消（手动停止）-> 确保下料机断电
        with _state_lock:
            _state["active"] = False
            _state["finished"] = True
            _state["finished_weight"] = _now_weight()
            _state["reason"] = "stopped"
        _log("[下料] 已手动停止，下料机断电")
        raise
    except Exception as exc:
        _dispense_stop()
        with _state_lock:
            _state["active"] = False
            _state["finished"] = True
            _state["reason"] = "error"
        _log(f"[下料] 异常: {exc}，下料机已断电")


async def _wait_serial_ready(timeout: float = 15.0) -> bool:
    """等待串口就绪（能成功向执行器发指令）。
    - valve 通道：发 STATUS 探测，收到 OK/ON/OFF 即就绪
    - legacy_relay 通道：用 '1'(吸合=下料机断电) 安全试探
    保留最长 timeout 秒、0.5s 轮询节奏。"""
    deadline = time.time() + timeout
    if _is_valve_channel():
        while time.time() < deadline:
            try:
                if valve_mod.valve_is_ready():
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False
    # legacy_relay 通道
    while time.time() < deadline:
        try:
            if relay_control("on").get("status") == "ok":
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def start_dispense(target_g: float) -> dict:
    """启动一次下料：目标 = 当前秤面重量 + target_g。重复启动会替换旧任务。"""
    global _task
    try:
        target_g = float(target_g)
    except (TypeError, ValueError):
        return {"success": False, "error": "target_g 必须是数字（克）"}
    if target_g <= 0:
        return {"success": False, "error": "target_g 必须大于 0"}

    # 脉冲出料进行中：拒绝（2026-09-15）——别把别人的 3 秒放盐拦腰打断
    if pulse_in_progress():
        return {"success": False, "reason": "busy",
                "error": "下料机正在脉冲出料中（放盐未结束），已拒绝本次下料请求，请稍后再试"}

    # 同目标重复启动（前端连点）：直接沿用进行中的任务，不再 cancel + 关阀 + 重开。
    # 2026-09-15：旧行为每次点击都发 2 条 close（全通道）+ 2 条 open，密集点击把
    # 香橙派串口命令队列打爆 → 板卡不应答(SILENT) → 触发全量重分类 8~10s → 越点越卡。
    if _task and not _task.done():
        with _state_lock:
            same_target = (
                bool(_state.get("active"))
                and abs(float(_state.get("target_g") or 0.0) - target_g) < 0.05
            )
            cur_target = float(_state.get("target") or 0.0)
        if same_target:
            remain = max(0.0, cur_target - _now_weight())
            _log(f"[下料] 重复启动（目标 {target_g:.1f}g 相同）→ 沿用进行中的任务，剩余约 {remain:.1f}g")
            return {
                "success": True,
                "reason": "already_running",
                "target_g": round(target_g, 1),
                "target": round(cur_target, 1),
                "remaining": round(remain, 1),
                "message": (f"下料已在进行中（目标 {round(target_g, 1)}g，剩余约 {remain:.1f}g），"
                            "无需重复启动"),
            }

    # 目标不同才替换旧任务
    if _task and not _task.done():
        _task.cancel()
        _dispense_stop()
        with _state_lock:
            _state["active"] = False
            _state["reason"] = "stopped"

    # 等待串口就绪，避免后端刚启动/串口未连时脉冲全部静默失败
    if not await _wait_serial_ready():
        return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}

    started_weight = _now_weight()
    target = started_weight + target_g
    with _state_lock:
        _state.update({
            "active": True,
            "target": round(target, 1),
            "target_g": round(target_g, 1),
            "started_weight": round(started_weight, 1),
            "started_at": time.time(),
            "finished": False,
            "finished_weight": 0.0,
            "reason": "",
        })

    # 首次开阀带状态确认与重发（瞬时丢指令自愈）；监测循环内保活重发走轻量单发
    if not _dispense_start_verified():
        with _state_lock:
            _state["active"] = False
            _state["reason"] = "error"
        return {"success": False, "error": "开阀指令未生效（多次重发仍非开启状态），已放弃下料"}

    _task = asyncio.create_task(_monitor_loop())
    _log(f"[下料] 启动: 当前 {started_weight:.1f}g, 投放 {target_g:.1f}g, 目标 {target:.1f}g (持续下料)")
    return {
        "success": True,
        "started_weight": started_weight,
        "target_g": target_g,
        "target": round(target, 1),
        "message": f"下料机已开启(持续下料)，目标 {round(target, 1)}g",
    }


async def stop_dispense() -> dict:
    """手动停止下料：取消监测任务并无条件发送关阀/断电指令。

    2026-09-09 用户要求（防烧阀）：改代码触发 reload 硬停旧进程时，进行中的
    开阀状态其关阀 finally 不会执行，电磁阀可能被遗留为通电开启态——本函数
    即使没有进行中的任务也强制发一次关阀指令（幂等），保证
    HTTP POST /dispense/stop 是可靠的"确保电磁阀已关闭"命令。
    """
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    closed = _dispense_stop()  # 无条件关阀/断电（无任务时也执行，幂等）
    if not closed:
        return {"success": False, "error": "关阀指令发送失败，请检查电磁阀板卡连接"}
    return {"success": True, "message": "下料已停止，电磁阀已关闭"}


async def pulse_dispense(duration_s: float = 3.0) -> dict:
    """定时脉冲下料：下料机通电 duration_s 秒后断电——不走称重达标闭环。

    与 start_dispense（持续下料到目标克重）**互斥**（2026-09-15 改）：
    闭环下料进行中时**直接拒绝并告知剩余克数**，不像旧实现那样先
    `await stop_dispense()` 把别人的下料无声掐断（表现为"下料刚开始就被关"）。
    反向亦然：脉冲期间 start_dispense 会被 pulse_in_progress() 挡掉。
    finally 保证任何异常（含任务被取消）都会断电。
    """
    global _pulse_active
    # 原子占位：先占住脉冲通道，再检查闭环是否占用；闭环忙则回滚占位并拒绝
    with _pulse_lock:
        if _pulse_active:
            return {"success": False, "reason": "busy",
                    "error": "下料机正在脉冲出料中，请等本次结束"}
        _pulse_active = True
    with _state_lock:
        closed_busy = bool(_state.get("active"))
        target_g = float(_state.get("target_g") or 0.0)
        target = float(_state.get("target") or 0.0)
    if closed_busy:
        with _pulse_lock:
            _pulse_active = False
        remain = max(0.0, target - _now_weight())
        return {
            "success": False, "reason": "busy",
            "error": (f"下料机正在下料中（投放 {target_g:.1f} 克，剩余约 {remain:.1f} 克），"
                      "已拒绝本次脉冲——请等它结束，或先说停止再放盐"),
        }
    try:
        if not await _wait_serial_ready():
            return {"success": False, "error": "串口未就绪（未连接到板卡），无法下料"}
        if not _dispense_start_verified():    # 开阀/发'0'：通电开始出料（带状态确认与重发）
            return {"success": False, "error": "开阀指令未生效（多次重发仍非开启状态），请检查板卡连接"}
        await asyncio.sleep(duration_s)
        return {"success": True, "duration_s": duration_s,
                "message": f"脉冲下料完成（通电 {duration_s:g} 秒）"}
    finally:
        _dispense_stop()                    # 关阀/发'1'：断电停止——异常/取消路径也必须执行
        with _pulse_lock:
            _pulse_active = False
        _log(f"[下料] 脉冲出料 {duration_s:g}s 结束，已断电")


def get_dispense_status() -> dict:
    """查询当前下料状态。"""
    with _state_lock:
        s = dict(_state)
    s["weight"] = _now_weight()
    s["remaining"] = round(max(0.0, s["target"] - s["weight"]), 1) if s["active"] else 0.0
    return s
