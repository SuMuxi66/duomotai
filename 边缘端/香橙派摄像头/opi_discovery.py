"""opi_discovery.py — 子网自动发现兜底（2026-09-21 上线，三个桥接客户端共享本模块）。

配置的 SERVERS 全部掉线时，自动扫描 192.168.1.3-254 的 8010 端口寻找后端：
  · TCP 连不上的 IP（无响应）直接跳过；
  · /health 不含 "ok":true 的跳过；
  · WS 握手失败（含旧版后端缺路由回 403）跳过；
  · 整轮没有可用后端则记一条日志继续等，不加任何目标。
任一目标在线时不扫描。OPI_DISCOVERY=0 关闭；
OPI_DISCOVERY_SUBNET / OPI_DISCOVERY_PORT / OPI_DISCOVERY_INTERVAL 可覆盖默认值。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable

import websockets

DISCOVERY_ENABLED = os.getenv("OPI_DISCOVERY", "1") != "0"
DISCOVERY_SUBNET = os.getenv("OPI_DISCOVERY_SUBNET", "192.168.1")
DISCOVERY_PORT = int(os.getenv("OPI_DISCOVERY_PORT", "8010") or 8010)
DISCOVERY_INTERVAL_S = float(os.getenv("OPI_DISCOVERY_INTERVAL", "90") or 90)
_EXTRA_TASKS: list = []


async def _tcp_open(ip: str, timeout: float) -> bool:
    """TCP 能否连上端口：连不上（超时/拒绝）= 无响应，直接跳过。"""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, DISCOVERY_PORT), timeout=timeout
        )
        writer.close()
        return True
    except Exception:
        return False


async def _health_ok(ip: str) -> bool:
    """/health 探测：响应体含 "ok": true 才认定是本项目后端。"""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, DISCOVERY_PORT), timeout=1.0
        )
        writer.write(f"GET /health HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode())
        await writer.drain()
        # 必须读到 EOF：read(512) 会在第一个包（可能只有响应头）就返回，漏掉 body 里的 "ok":true
        data = await asyncio.wait_for(reader.read(-1), timeout=2.0)
        writer.close()
        text = data.decode("utf-8", "replace")
        return '"ok"' in text and "true" in text
    except Exception:
        return False


async def _ws_ok(url: str) -> bool:
    """WS 握手探测：旧版后端缺路由回 403、超时等一律视为不可用。"""
    try:
        ws = await asyncio.wait_for(
            websockets.connect(url, max_size=None, ping_interval=None), timeout=4.0
        )
    except Exception:
        return False
    try:
        await ws.close()
    except Exception:
        pass
    return True


async def discover_backends(ws_path: str) -> list:
    """扫描 DISCOVERY_SUBNET.3-254，返回确认可用的后端 ws:// 地址列表（可为空）。"""
    ips = [f"{DISCOVERY_SUBNET}.{i}" for i in range(3, 255)]
    results = await asyncio.gather(*[_tcp_open(ip, 0.4) for ip in ips])
    open_ips = [ip for ip, ok in zip(ips, results) if ok]
    if not open_ips:
        return []
    alive = []
    for ip in open_ips:
        if await _health_ok(ip):
            alive.append(f"ws://{ip}:{DISCOVERY_PORT}{ws_path}")
    return [url for url in alive if await _ws_ok(url)]


async def discovery_loop(
    targets: list,
    ws_path: str,
    log: logging.Logger,
    make_target: Callable[[str], object],
    spawn: Callable[[object], "asyncio.Task"],
    hello_json: str = "",
) -> None:
    """周期兜底：所有目标都掉线时扫描子网，把新发现的后端动态加为推送目标。

    make_target(url) 由调用方给（各客户端 Target 构造不同）；
    spawn(target) 负责起该目标的重连任务（camera/ir 是 .loop()，sensors 是 .connection_loop()）。
    hello_json：非空时赋给新目标的 hello（sensors/ir 需要；camera 的 hello 由采集循环统一补发）。
    """
    if not DISCOVERY_ENABLED:
        return
    await asyncio.sleep(15)   # 先给配置清单一轮连接机会（开机网络未就绪时扫描必空）
    while True:
        try:
            if targets and all(t.ws is None for t in targets):
                found = await discover_backends(ws_path)
                known = {t.url for t in targets}
                new_urls = [u for u in found if u not in known]
                for url in new_urls:
                    t = make_target(url)
                    if hello_json:
                        t.hello_json = hello_json
                    targets.append(t)
                    _EXTRA_TASKS.append(spawn(t))
                if new_urls:
                    log.info(f"[发现] 子网扫描新增 {len(new_urls)} 个后端: {', '.join(new_urls)}")
                else:
                    log.info("[发现] 子网扫描完成：未发现可用后端，跳过")
        except Exception as e:
            log.warning(f"[发现] 子网扫描异常: {type(e).__name__}: {e}")
        await asyncio.sleep(DISCOVERY_INTERVAL_S)
