# -*- coding: utf-8 -*-
"""回滚脚本：把 Pi 端三个桥接客户端退回拆分前版本（.bak_presplit_0921），删除新增模块。

用法：RKI_PASS 未硬编码——python _rollback_split_0921.py
（账号密码与 香橙派远程.py 同款：orangepi/orangepi，2026-09-21 拆分部署的配套回滚工具）
"""
import time

import paramiko

BACKUPS = [
    ("/home/orangepi/camera/opi_sensors_client.py.bak_presplit_0921",
     "/home/orangepi/camera/opi_sensors_client.py"),
    ("/home/orangepi/camera/opi_camera_client.py.bak_presplit_0921",
     "/home/orangepi/camera/opi_camera_client.py"),
    ("/home/orangepi/camera/opi_ir_client.py.bak_presplit_0921",
     "/home/orangepi/camera/opi_ir_client.py"),
]
NEW_MODULES = [
    "/home/orangepi/camera/opi_serial_common.py",
    "/home/orangepi/camera/opi_serial_classify.py",
    "/home/orangepi/camera/opi_valve.py",
    "/home/orangepi/camera/opi_weight_reader.py",
    "/home/orangepi/camera/opi_discovery.py",
]


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.248", port=22, username="orangepi", password="orangepi", timeout=10)
    sftp = c.open_sftp()
    for bak, orig in BACKUPS:
        sftp.rename(bak, orig)
        print("restored:", orig)
    for mod in NEW_MODULES:
        try:
            sftp.remove(mod)
            print("removed:", mod)
        except IOError:
            print("already gone:", mod)
    sftp.close()
    _, stdout, _ = c.exec_command(
        "echo orangepi | sudo -S systemctl restart opi-sensors opi-camera opi-ir 2>/dev/null; "
        "sleep 8; systemctl is-active opi-sensors opi-camera opi-ir",
        timeout=60,
    )
    time.sleep(10)
    print(stdout.read().decode("utf-8", "replace"))
    c.close()
    print("回滚完成：三服务已按拆分前单文件版本重启。")


if __name__ == "__main__":
    main()
