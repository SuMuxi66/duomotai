# -*- coding: utf-8 -*-
"""一键启动主程序前后端（backend 8010 + frontend 主前端 5179）。

流程：
  1. 检查 8010 / 5179 端口占用，有占用直接 taskkill /F /T 连树强杀
     （后端额外清扫命令行含 uvicorn/spawn_main 的残留 python，含孤儿 reload worker）
  2. 启动后端：python -m uvicorn app.main:app --reload --reload-dir app（热重载，
     解释器优先 backend/.venv——本机系统 Python312 缺依赖起不来）
  3. 轮询 /health 就绪（本机固定地址，原始 socket 探活）
  4. 启动主前端：npm run dev（端口读 frontend/.env 的 VITE_FRONTEND_PORT，默认 5179）
  5. 轮询前端端口就绪

用法：python start_main.py
"""
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
MAIN_FRONTEND_DIR = ROOT / "frontend"
BACKEND_PORT = 8010

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")


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
    """强杀占用端口的进程并等待释放；杀不掉返回 False（拒绝带病启动）"""
    pids = find_listen_pids(port)
    if not pids and not backend_sweep:
        log(f"[{label}] 端口 {port} 无占用")
        return True
    if pids:
        log(f"[{label}] 端口 {port} 被占用（PID: {', '.join(sorted(pids))}），强杀")
        for pid in pids:
            kill_tree(pid)
    if backend_sweep:
        n = kill_backend_leftovers()
        if n:
            log(f"[{label}] 清扫残留后端进程 {n} 个")
    deadline = time.time() + 10
    while time.time() < deadline:
        if not find_listen_pids(port):
            log(f"[{label}] 端口 {port} 已释放")
            return True
        time.sleep(0.5)
    log(f"[{label}] ❌ 端口 {port} 无法释放（可能有进程杀不掉），中止该组件启动")
    return False


def backend_health_ok() -> bool:
    """本机 /health 探活：固定 127.0.0.1，原始 socket 发送固定 GET 后检查状态码"""
    try:
        with socket.create_connection(("127.0.0.1", BACKEND_PORT), timeout=2) as s:
            s.sendall(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            data = s.recv(64)
        return b" 200 " in data
    except OSError:
        return False


def wait_backend_health(timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if backend_health_ok():
            return True
        time.sleep(1)
    return False


def wait_port(port: int, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1)
    return False


def read_env_port(frontend_dir: Path, default: int) -> int:
    env_file = frontend_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("VITE_FRONTEND_PORT="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    break
    return default


def spawn_detached(cmd: list, cwd: Path, out_log: Path, err_log: Path):
    out_log.parent.mkdir(parents=True, exist_ok=True)
    out_f = open(out_log, "ab")
    err_f = open(err_log, "ab")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # npm.CMD 这类批处理经 CreateProcess 直启 + DETACHED_PROCESS 会静默死亡
    # （0 字节日志、端口不监听，2026-09-19 实测两轮复现）：包一层 cmd /c 拉起
    if str(cmd[0]).lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=out_f,
        stderr=err_f,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    log(f"[启动] {' '.join(cmd)}  (cwd={cwd.name}, 日志 {out_log.name})")
    return proc


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    npm = shutil.which("npm")
    if npm is None:
        log("❌ 找不到 npm，请确认 Node.js 已安装并在 PATH 中")
        return 1
    # 后端运行时优先用 backend/.venv（本机系统 Python312 缺 pydantic_settings 起不来），
    # venv 不存在才退回系统 Python312（演示机基线），再退回当前解释器
    venv_py = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    system_py = Path(r"C:\Program Files\Python312\python.exe")
    if venv_py.exists():
        py = str(venv_py)
    elif system_py.exists():
        py = str(system_py)
    else:
        py = sys.executable

    frontend_port = read_env_port(MAIN_FRONTEND_DIR, 5179)
    log(f"=== 启动主程序：backend {BACKEND_PORT} + frontend {frontend_port} ===")

    # 1) 清理端口（先杀后端再杀前端，避免前端代理还连着旧后端时序混乱）
    if not free_port(BACKEND_PORT, "backend", backend_sweep=True):
        return 1
    if not free_port(frontend_port, "frontend"):
        return 1

    # 2) 后端（--reload 热重载；--reload-dir app 必带——默认监视整个 backend/ 连 .venv 一起，
    #    任何 pip/uv 装包动作都会触发 reload 风暴）
    spawn_detached(
        [
            py, "-m", "uvicorn", "app.main:app",
            "--host", "0.0.0.0", "--port", str(BACKEND_PORT),
            "--reload", "--reload-dir", "app",
        ],
        BACKEND_DIR,
        BACKEND_DIR / "data" / f"backend_uvicorn.{ts}.log",
        BACKEND_DIR / "data" / f"backend_uvicorn.{ts}.err.log",
    )
    if wait_backend_health():
        log(f"[backend] ✅ /health 200 就绪（http://127.0.0.1:{BACKEND_PORT}）")
    else:
        log("[backend] ❌ 120s 内 /health 未就绪，查看 backend/data/backend_uvicorn.*.err.log")
        return 1

    # 3) 主前端。npm.CMD 批处理 detached 直启时输出落不进重定向日志（实测 0 字节、
    # 失败无从排障，2026-09-19）；node 直跑 npm-cli.js 同样 detached 且日志完整，
    # 找不到 npm-cli.js 再退回 cmd /c npm.CMD。
    node_exe = Path(npm).with_name("node.exe")
    npm_cli = Path(npm).with_name("node_modules") / "npm" / "bin" / "npm-cli.js"
    if node_exe.exists() and npm_cli.exists():
        frontend_cmd = [str(node_exe), str(npm_cli), "run", "dev"]
    else:
        frontend_cmd = ["cmd", "/c", npm, "run", "dev"]
    spawn_detached(
        frontend_cmd,
        MAIN_FRONTEND_DIR,
        ROOT / "logs" / "frontend" / f"vite_{frontend_port}.{ts}.log",
        ROOT / "logs" / "frontend" / f"vite_{frontend_port}.{ts}.err.log",
    )
    if wait_port(frontend_port):
        log(f"[frontend] ✅ 端口 {frontend_port} 就绪（http://localhost:{frontend_port}）")
    else:
        log("[frontend] ❌ 30s 内端口未就绪，查看 logs/frontend/ 下日志")
        return 1

    log("=== 主程序启动完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
