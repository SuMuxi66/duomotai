# -*- coding: utf-8 -*-
"""一键启动五个副本前端（frontend-1..5 → 5180-5184，比赛分步演示机用）。

流程（每个副本独立执行）：
  1. 端口读各自 .env 的 VITE_FRONTEND_PORT（frontend-1..5 默认 5180..5184）
  2. 检查端口占用，有占用直接 taskkill /F /T 连树强杀
  3. npm run dev 后台启动（日志落 logs/frontend/vite_<端口>.<时间戳>.log）
  4. 轮询端口就绪

主程序前后端（backend 8010 + frontend 5179）请用 start_main.py。

用法：python start_copies.py
"""
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# (目录, 端口兜底值)：.env 缺失/无键时按此兜底
COPIES = [
    ("frontend-1", 5180),
    ("frontend-2", 5181),
    ("frontend-3", 5182),
    ("frontend-4", 5183),
    ("frontend-5", 5184),
]

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


def free_port(port: int, label: str) -> bool:
    """强杀占用端口的进程并等待释放；杀不掉返回 False（拒绝带病启动）"""
    pids = find_listen_pids(port)
    if not pids:
        log(f"  [{label}] 端口 {port} 无占用")
        return True
    log(f"  [{label}] 端口 {port} 被占用（PID: {', '.join(sorted(pids))}），强杀")
    for pid in pids:
        kill_tree(pid)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not find_listen_pids(port):
            log(f"  [{label}] 端口 {port} 已释放")
            return True
        time.sleep(0.5)
    log(f"  [{label}] ❌ 端口 {port} 无法释放（可能有进程杀不掉），跳过该副本")
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
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=out_f,
        stderr=err_f,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    log(f"  [启动] npm run dev  (cwd={cwd.name}, 日志 {out_log.name})")
    return proc


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    npm = shutil.which("npm")
    if npm is None:
        log("❌ 找不到 npm，请确认 Node.js 已安装并在 PATH 中")
        return 1

    log("=== 启动副本前端 frontend-1..5 ===")
    ok, failed = [], []
    for name, fallback_port in COPIES:
        fdir = ROOT / name
        if not fdir.exists():
            log(f"[{name}] ❌ 目录不存在，跳过")
            failed.append(name)
            continue
        port = read_env_port(fdir, fallback_port)
        log(f"[{name}] 端口 {port}")
        if not free_port(port, name):
            failed.append(name)
            continue
        spawn_detached(
            [npm, "run", "dev"],
            fdir,
            ROOT / "logs" / "frontend" / f"vite_{port}.{ts}.log",
            ROOT / "logs" / "frontend" / f"vite_{port}.{ts}.err.log",
        )
        if wait_port(port, timeout=120):
            log(f"  [{name}] ✅ 端口 {port} 就绪（http://localhost:{port}）")
            ok.append(name)
        else:
            log(f"  [{name}] ❌ 120s 内端口未就绪，查看 logs/frontend/ 下日志")
            failed.append(name)

    log(f"=== 副本前端完成：成功 {len(ok)} 个 {ok}；失败 {len(failed)} 个 {failed} ===")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
