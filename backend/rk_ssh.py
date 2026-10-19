"""rk_ssh.py — 远程 RK3588 操作辅助（paramiko，密码走环境变量 RKI_PASS）。

用法（在 backend 目录下）:
    RKI_PASS=xxx python rk_ssh.py recon
    RKI_PASS=xxx python rk_ssh.py run "命令"
    RKI_PASS=xxx python rk_ssh.py put local_path remote_path
"""
import os
import sys
import paramiko

HOST = os.environ.get("RKI_HOST", "192.168.1.248")
PORT = int(os.environ.get("RKI_PORT", "22"))
USER = os.environ.get("RKI_USER", "root")
PASS = os.environ.get("RKI_PASS", "")

_client = None


def client():
    global _client
    if _client is None:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(HOST, port=PORT, username=USER, password=PASS, timeout=10)
        _client = c
    return _client


def run(cmd, timeout=180):
    c = client()
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def put(local_path, remote_path):
    c = client()
    sftp = c.open_sftp()
    sftp.put(local_path, remote_path)
    sftp.close()
    return f"put {local_path} -> {remote_path}"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "recon"
    if mode == "recon":
        cmds = [
            "echo '== uname =='; uname -a",
            "echo '== python =='; (python3 --version; which python3)",
            "echo '== pip pkgs =='; python3 -m pip list 2>/dev/null | grep -iE 'websockets|numpy|httpx|sounddevice|sherpa|dotenv|uvicorn|fastapi|funasr|modelscope' || echo none",
            "echo '== listening ports =='; (ss -ltnp 2>/dev/null | grep -E '8767|8768|8010' || echo 'no 8767/8768/8010')",
            "echo '== procs =='; (ps aux 2>/dev/null | grep -iE '8767|8768|kws|asr|uvicorn|funasr' | grep -v grep || echo 'no kws/asr proc')",
            "echo '== home =='; ls -la /root 2>/dev/null | head -30",
            "echo '== find services =='; (find / -maxdepth 4 -iname '*asr*' -o -iname '*kws*' 2>/dev/null | grep -v proc | head -20)",
        ]
        for cmd in cmds:
            rc, out, err = run(cmd)
            print(out)
            if err.strip():
                print("ERR:", err.strip()[:500])
    elif mode == "run":
        cmd = " ".join(sys.argv[2:])
        rc, out, err = run(cmd)
        print(out)
        if err.strip():
            print("ERR:", err.strip()[:800])
        print(f"[exit={rc}]")
    elif mode == "put":
        local_path, remote_path = sys.argv[2], sys.argv[3]
        print(put(local_path, remote_path))
    else:
        print("unknown mode:", mode)


if __name__ == "__main__":
    main()
