"""香橙派一件启动.py — 香橙派端侧服务一键拉起（Windows 侧入口，用法对齐 start_main.py）。

作用：把仓库根的 start_edge_services.sh 上传到香橙派 /home/orangepi/ 并执行，
幂等拉起 systemd 四服务（opi-camera / opi-ir / opi-sensors / tts-sambert）+
Docker 两容器（rokid-kws :8767 / rokid-asr :8768），最后做端口与服务体检。

用法（仓库根目录）:
    python 香橙派一件启动.py            # 上传脚本并执行（幂等，可重复跑）
    python 香橙派一件启动.py status     # 只看当前状态，不拉起

依赖：backend/香橙派远程.py（paramiko）；凭据走 RKI_USER/RKI_PASS，缺省 orangepi/orangepi。
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

os.environ.setdefault("RKI_HOST", "192.168.1.248")
os.environ.setdefault("RKI_USER", "orangepi")
os.environ.setdefault("RKI_PASS", "orangepi")

import rk_ssh  # noqa: E402  （依赖上面的 env 与 sys.path）

LOCAL_SH = os.path.join(ROOT, "start_edge_services.sh")
REMOTE_SH = "/home/orangepi/start_edge_services.sh"


def main() -> int:
    if not os.path.exists(LOCAL_SH):
        print(f"[FAIL] 找不到 {LOCAL_SH}")
        return 2

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        rc, out, err = rk_ssh.run(
            "systemctl is-enabled docker opi-camera opi-ir opi-sensors tts-sambert 2>&1; "
            "echo ---; systemctl is-active docker opi-camera opi-ir opi-sensors tts-sambert 2>&1; "
            "echo ---; docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep rokid; "
            "echo ---; ss -tln 2>/dev/null | grep -E ':8767 |:8768 |:8769 ' || echo 端口未监听"
        )
        print(out)
        if err.strip():
            print("ERR:", err.strip()[:800])
        return rc

    print(f"[上传] {LOCAL_SH} -> {REMOTE_SH}")
    rk_ssh.put(LOCAL_SH, REMOTE_SH)
    rk_ssh.run(f"chmod +x {REMOTE_SH}")

    print("[执行] 远程一键拉起（幂等）...\n")
    rc, out, err = rk_ssh.run(f"bash {REMOTE_SH}", timeout=180)
    print(out)
    if err.strip():
        print("ERR:", err.strip()[:800])
    print(f"[exit={rc}]")
    return rc


if __name__ == "__main__":
    sys.exit(main())
