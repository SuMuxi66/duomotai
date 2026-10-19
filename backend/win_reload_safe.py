# -*- coding: utf-8 -*-
"""uvicorn --reload 的 Windows 安全入口（修复重载即崩 WinError 6）。

根因：uvicorn reloader 在 Windows 上用 os.kill(pid, CTRL_C_EVENT) 停旧 server
子进程，该 Win32 调用依赖控制台；start_main.py 以 DETACHED_PROCESS（无控制台）
拉起后端，GenerateConsoleCtrlEvent 必报 WinError 6（句柄无效），CPython 3.12.3
还会把它包装成 SystemError，restart() 无捕获 → reloader 崩溃、整个后端消失。

补丁：把 BaseReload 的 Windows 分支改成 process.terminate()（与 uvicorn 官方
非 Windows 分支同款）。代价是重载时旧子进程被硬停：WS 连接断开由前端自动重连
（既有正常现象），串口由各 reader 的重试/周期重扫逻辑重新接上。

用法与 `python -m uvicorn` 完全一致：
    python win_reload_safe.py app.main:app --host 0.0.0.0 --port 8010 --reload --reload-dir app
"""
from __future__ import annotations

import sys


def _patch_windows_reload() -> None:
    if sys.platform != "win32":
        return
    from uvicorn.supervisors import basereload

    def restart(self) -> None:
        self.process.terminate()
        self.process.join()
        self.process = basereload.get_subprocess(
            config=self.config, target=self.target, sockets=self.sockets
        )
        self.process.start()

    def shutdown(self) -> None:
        # 无控制台时子进程收不到任何控制台事件，join 前必须显式终止，否则挂死
        self.process.terminate()
        self.process.join()
        for sock in self.sockets:
            sock.close()
        basereload.logger.info("Stopping reloader process [%s]", self.pid)

    basereload.BaseReload.restart = restart
    basereload.BaseReload.shutdown = shutdown


if __name__ == "__main__":
    _patch_windows_reload()
    import runpy

    runpy.run_module("uvicorn", run_name="__main__")
