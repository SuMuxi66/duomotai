# -*- coding: utf-8 -*-
"""一键强杀前后端端口占用（不动启动，只负责杀）。

默认清理：
  - backend 8010（含清扫命令行含 uvicorn/spawn_main/win_reload_safe 的残留 python）
  - frontend 主前端 5179 + 副本 5180-5184

用法：
  python kill_servers.py                 # 杀全部（8010 + 5179-5184）
  python kill_servers.py --list          # 只查看各端口占用，不杀
  python kill_servers.py --ports 8010    # 只杀指定端口（逗号分隔）
"""
import argparse
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

DEFAULT_PORTS = {
    8010: "backend",
    5179: "frontend(主)",
    5180: "frontend-1",
    5181: "frontend-2",
    5182: "frontend-3",
    5183: "frontend-4",
    5184: "frontend-5",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def find_listen_pids(port: int) -> set:
    """netstat 找出监听该端口的所有 PID（中文 Windows 输出为 GBK，按 ignore 解码，
    解析只依赖 ASCII 的 PID 与状态字）"""
    r = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True)
    out = (r.stdout or b"").decode("utf-8", errors="ignore")
    pids = set()
    suffix = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        # IPv4 0.0.0.0:8010 与 IPv6 [::]:8010 均兼容：Local Address 以 :port 结尾且状态 LISTENING
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(suffix):
            pids.add(parts[4])
    return pids


def process_name(pid: str) -> str:
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True
    )
    out = (r.stdout or b"").decode("utf-8", errors="ignore").strip()
    if out and "INFO:" not in out.upper():
        return out.splitlines()[0].split('","')[0].strip('"')
    return "?"


def kill_tree(pid: str) -> None:
    # taskkill 输出为 GBK，取字节流即可（输出不参与逻辑，避免解码崩溃）
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def kill_backend_leftovers() -> int:
    """清扫残留后端进程：命令行含 uvicorn / spawn_main / win_reload_safe 的 python（孤儿 reload worker 等）"""
    ps_sweep = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'uvicorn|spawn_main|win_reload_safe' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_sweep], capture_output=True
    )
    out = (r.stdout or b"").decode("utf-8", errors="ignore")
    killed = 0
    for token in out.split():
        if token.strip().isdigit():
            kill_tree(token.strip())
            killed += 1
    return killed


def free_port(port: int, label: str, backend_sweep: bool = False) -> bool:
    """强杀占用端口的进程并等待释放；杀不掉返回 False"""
    pids = find_listen_pids(port)
    if not pids and not backend_sweep:
        log(f"[{label}] 端口 {port} 无占用")
        return True
    if pids:
        for pid in sorted(pids):
            log(f"[{label}] 端口 {port} 被占用：PID {pid}（{process_name(pid)}），taskkill /F /T 强杀")
            kill_tree(pid)
    if backend_sweep:
        n = kill_backend_leftovers()
        if n:
            log(f"[{label}] 清扫残留后端进程 {n} 个")
    deadline = time.time() + 10
    while time.time() < deadline:
        if not find_listen_pids(port):
            log(f"[{label}] ✅ 端口 {port} 已释放")
            return True
        time.sleep(0.5)
    left = ", ".join(sorted(find_listen_pids(port)))
    log(f"[{label}] ❌ 端口 {port} 10s 内未释放（残留 PID: {left}，可能有进程杀不掉）")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="强杀前后端端口占用")
    ap.add_argument("--list", action="store_true", help="只查看各端口占用，不杀")
    ap.add_argument("--ports", help="逗号分隔的端口列表，覆盖默认（如 --ports 8010,5179）")
    args = ap.parse_args()

    if args.ports:
        try:
            ports = {int(p): "?" for p in args.ports.split(",") if p.strip()}
        except ValueError:
            log("❌ --ports 格式错误，应为逗号分隔数字，如 --ports 8010,5179")
            return 2
        if not ports:
            log("❌ --ports 为空")
            return 2
    else:
        ports = dict(DEFAULT_PORTS)

    if args.list:
        log("=== 端口占用一览（只读，不杀） ===")
        occupied = 0
        for port, label in ports.items():
            pids = find_listen_pids(port)
            if pids:
                occupied += 1
                names = ", ".join(f"{p}({process_name(p)})" for p in sorted(pids))
                log(f"[{label}] 端口 {port}：占用中 → {names}")
            else:
                log(f"[{label}] 端口 {port}：空闲")
        log(f"=== 共 {occupied} 个端口被占用 ===")
        return 0

    log(f"=== 强杀端口占用：{', '.join(str(p) for p in ports)} ===")
    # 先杀后端再杀前端，避免前端代理还连着旧后端时序混乱
    ordered = sorted(ports, key=lambda p: 0 if p == 8010 else 1)
    failed = []
    for port in ordered:
        if not free_port(port, ports[port], backend_sweep=(port == 8010)):
            failed.append(port)

    # 收尾核对一遍
    still = [p for p in ordered if find_listen_pids(p)]
    if failed or still:
        log(f"=== ❌ 未全部释放：{', '.join(str(p) for p in dict.fromkeys(failed + still))} ===")
        return 1
    log("=== ✅ 全部端口已释放 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
