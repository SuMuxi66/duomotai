# -*- coding: utf-8 -*-
"""演示前 checklist 检查脚本（跨平台 Python，不依赖 bash）。

用法：仓库根目录执行  python check_demo_ready.py
逐项输出 ✅/❌ 并给一行修复指引；全部通过退出码 0，任一失败退出码 1。

检查项：
1. 8010 端口只有 1 个 LISTENING（无双实例）
2. /health 返回 ok
3. /temperature 数值实时、/weight 有读数、/fan/status 可达
4. COM15/16/19 串口在系统端口列表中（best-effort，不误报占用）
5. 聊天历史已清理（data/chat_history 无非空会话文件）
6. 8010 端口 owner 进程命令行含 win_reload_safe.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "http://127.0.0.1:8010"
PORT = 8010
EXPECTED_COM_PORTS = ("COM15", "COM16", "COM19")
CHAT_HISTORY_DIR = Path(__file__).resolve().parent / "data" / "chat_history"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_results: list[tuple[bool, str, str]] = []  # (ok, name, detail)


def record(ok: bool, name: str, detail: str = "", fix: str = "") -> None:
    _results.append((ok, name, detail))
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f"：{detail}" if detail else ""))
    if not ok and fix:
        print(f"    修复指引：{fix}")


def http_get_json(path: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def netstat_listening_pids(port: int) -> list[int]:
    """netstat 解析指定端口的 LISTENING 行，返回 owner PID 列表（Windows）。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        if f":{port}" not in line or "LISTENING" not in line.upper():
            continue
        # 本机监听行形如 TCP 0.0.0.0:8010 0.0.0.0:0 LISTENING <pid>
        if re.search(rf"\s(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1):{port}\s", line):
            m = re.search(r"LISTENING\s+(\d+)", line, re.IGNORECASE)
            if m:
                pids.append(int(m.group(1)))
    return list(dict.fromkeys(pids))


def pid_commandline(pid: int) -> str:
    """Windows 下查 PID 的完整命令行（PowerShell CIM）。失败返回空串。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        return out or ""
    except Exception:
        return ""


def check_port_single_listener() -> list[int]:
    pids = netstat_listening_pids(PORT)
    if len(pids) == 1:
        record(True, f"8010 端口唯一 LISTENING", f"PID={pids[0]}")
    elif len(pids) == 0:
        record(False, "8010 端口唯一 LISTENING", "无监听", "后端未启动：仓库根执行 python start_main.py")
    else:
        record(False, "8010 端口唯一 LISTENING", f"双实例 PID={pids}",
               "双实例互抢连接：逐个 taskkill /F /T /PID <pid> 杀干净后，用仓库根 start_main.py 重启")
    return pids


def check_health() -> bool:
    try:
        data = http_get_json("/health")
        ok = bool(data.get("ok"))
        record(ok, "后端 /health", "" if ok else str(data))
        return ok
    except Exception as exc:
        record(False, "后端 /health", f"{type(exc).__name__}: {exc}",
               "用仓库根 start_main.py 启动后端（勿用裸 python -m uvicorn，Windows 下重载必崩）")
        return False


def check_temperature() -> None:
    try:
        data = http_get_json("/temperature", timeout=8)
    except Exception as exc:
        record(False, "温度 /temperature 实时", f"{type(exc).__name__}: {exc}", "检查后端 /health 与红外温度串口（CH340/COM15）接线")
        return
    vals = [data.get(k) for k in ("average", "maximum", "minimum", "target") if data.get(k) is not None]
    if not vals:
        record(False, "温度 /temperature 实时", "无温度数值", "检查 COM15 红外探头接线与后端温度扫描日志")
        return
    ts = data.get("timestamp")
    age = None
    if ts:
        try:
            age = (datetime.now() - datetime.fromisoformat(str(ts))).total_seconds()
        except Exception:
            pass
    if age is None or age > 30:
        record(False, "温度 /temperature 实时", f"avg={data.get('average')} 但数据过期 age={age}s",
               "温度读数超过 30 秒未刷新：检查 COM15 接线，或重启后端触发重扫")
    else:
        record(True, "温度 /temperature 实时", f"avg={data.get('average')}°C age={age:.0f}s")


def check_weight() -> None:
    try:
        data = http_get_json("/weight", timeout=8)
        w = data.get("weight")
        if w is None:
            record(False, "重量 /weight 有读数", "weight=None", "检查 COM19 称重串口（HX711）接线与后端日志")
        else:
            unit = data.get("unit", "g")
            record(True, "重量 /weight 有读数", f"{w}{unit} stable={data.get('stable')}")
    except Exception as exc:
        record(False, "重量 /weight 有读数", f"{type(exc).__name__}: {exc}", "检查 COM19 称重串口接线与后端 /health")


def check_fan() -> None:
    try:
        data = http_get_json("/fan/status", timeout=10)
        record(True, "风扇 /fan/status 可达", str(data)[:80])
    except Exception as exc:
        record(False, "风扇 /fan/status 可达", f"{type(exc).__name__}: {exc}",
               "python-miio 缺失见 backend/requirements.txt 注释（--no-deps 安装法），或智能插座网络不通")


def check_serial_ports() -> None:
    """COM15/16/19 是否在系统端口列表（best-effort：不区分占用者是后端还是串口助手）。"""
    try:
        import serial.tools.list_ports  # type: ignore

        present = {p.device for p in serial.tools.list_ports.comports()}
    except Exception as exc:
        record(False, "串口 COM15/16/19 检测", f"pyserial 不可用: {exc}", "pip install pyserial==3.5 后重试（best-effort 项）")
        return
    missing = [c for c in EXPECTED_COM_PORTS if c not in present]
    detail = f"系统可见 {sorted(present & set(EXPECTED_COM_PORTS))}"
    if missing:
        record(False, "串口 COM15/16/19 检测", f"{detail}；缺失 {missing}",
               "关闭串口助手等占用程序，重插 USB 转串口线，设备管理器确认端口出现后重启后端")
    else:
        record(True, "串口 COM15/16/19 检测", detail)


def check_chat_history_clean() -> None:
    if not CHAT_HISTORY_DIR.exists():
        record(True, "聊天历史已清理", "data/chat_history 不存在")
        return
    dirty = []
    for f in sorted(CHAT_HISTORY_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                if json.load(fh).get("messages"):
                    dirty.append(f.name)
        except Exception:
            dirty.append(f.name)
    if dirty:
        record(False, "聊天历史已清理", f"残留会话 {dirty}", "演示前清空：删除 data/chat_history/*.json 或前端会话面板清空")
    else:
        record(True, "聊天历史已清理", f"{len(list(CHAT_HISTORY_DIR.glob('*.json')))} 个空文件/无内容")


def check_owner_commandline(pids: list[int]) -> None:
    if not pids:
        record(False, "8010 owner 为 win_reload_safe.py 拉起", "无监听 PID", "用仓库根 start_main.py 启动后端")
        return
    cmd = pid_commandline(pids[0])
    if "win_reload_safe.py" in cmd:
        record(True, "8010 owner 为 win_reload_safe.py 拉起", cmd[:100])
    elif not cmd:
        record(False, "8010 owner 为 win_reload_safe.py 拉起", "命令行查询失败（PowerShell 不可用？）",
               "手动核对：powershell \"(Get-CimInstance Win32_Process -Filter 'ProcessId=<pid>').CommandLine\"")
    else:
        record(False, "8010 owner 为 win_reload_safe.py 拉起", cmd[:100],
               "后端非 win_reload_safe.py 启动，重载会 WinError 6 崩溃：用仓库根 start_main.py 重启")


def main() -> int:
    print("=" * 60)
    print("演示前检查（智能烹饪助手）")
    print("=" * 60)
    pids = check_port_single_listener()
    if check_health():
        check_temperature()
        check_weight()
        check_fan()
    else:
        record(False, "温度 /temperature 实时", "跳过（后端未就绪）", "")
        record(False, "重量 /weight 有读数", "跳过（后端未就绪）", "")
        record(False, "风扇 /fan/status 可达", "跳过（后端未就绪）", "")
    check_serial_ports()
    check_chat_history_clean()
    check_owner_commandline(pids)
    failed = [name for ok, name, _ in _results if not ok]
    print("-" * 60)
    if failed:
        print(f"共 {len(_results)} 项，失败 {len(failed)} 项：{failed}")
        return 1
    print(f"共 {len(_results)} 项，全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
