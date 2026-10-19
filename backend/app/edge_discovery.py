"""端侧语音服务（KWS / ASR）局域网自动发现。

香橙派的 IP 由 DHCP 分配会漂移，前端写死的地址（ws://192.168.1.x:8767/kws）
一旦漂移就连不上。本模块在后端侧并发探测本机 /24 网段的 8767/8768，并用
`GET /health` 确认确属我们的服务，结果经 `GET /edge/voice` 下发给前端。

设计要点：
- 快路径优先：先重试上次成功的 IP（几乎零耗时），不通才全网段扫描；
- 扫描是并发 TCP 探测，254 个地址实测约 1~2 秒，结果缓存 5 分钟不重复扫；
- 判定条件严格：8767 与 8768 同时命中 **且** /health 返回 status=ok，
  避免把局域网里恰好开着同名端口的其它设备（比如另一台开发机）当成香橙派；
- 应用启动即后台预热，前端首开就能拿到结果，不必等第一次扫描。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time

from .logger import backend_log

KWS_PORT = 8767          # 唤醒词服务端口（容器内 rokid-kws）
ASR_PORT = 8768          # 流式识别服务端口（容器内 rokid-asr）
PROBE_TIMEOUT = 0.4      # 单次端口探测超时（秒）
HEALTH_TIMEOUT = 1.5     # /health 校验超时（秒）
SWEEP_CONCURRENCY = 128  # 并发探测上限
CACHE_TTL = 300.0        # 结果缓存有效期（秒）
REFRESH_INTERVAL = 300.0  # 后台刷新间隔（秒）

# 发现结果缓存（进程内单例）
_state: dict = {"ip": None, "checked_at": 0.0, "detail": ""}


def _local_ip() -> str | None:
    """取本机在局域网中的 IP（借 UDP 连接拿出口网卡地址，不发真实数据包）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _subnet_hosts(local_ip: str) -> list[str]:
    """本机 IP 推 /24 网段，返回全部主机地址"""
    try:
        net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        return [str(h) for h in net.hosts()]
    except Exception:
        return []


async def _port_open(ip: str, port: int, sem: asyncio.Semaphore, timeout: float = PROBE_TIMEOUT) -> bool:
    """TCP 端口连通性探测（探测完立即关闭，不握手、不发数据）"""
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False


async def _health_ok(ip: str) -> bool:
    """确认 8767 上确实是我们的 KWS 服务（而非恰好占用端口的其它程序）"""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            resp = await client.get(f"http://{ip}:{KWS_PORT}/health")
            if resp.status_code != 200:
                return False
            return str(resp.json().get("status", "")).lower() == "ok"
    except Exception:
        return False


def _build(ip: str | None, detail: str, cached: bool = False) -> dict:
    return {
        "found": bool(ip),
        "ip": ip,
        "kws_url": f"ws://{ip}:{KWS_PORT}/kws" if ip else None,
        "asr_url": f"ws://{ip}:{ASR_PORT}/asr" if ip else None,
        "detail": detail,
        "cached": cached,
        "checked_at": _state.get("checked_at") or time.time(),
        "cache_ttl": CACHE_TTL,
    }


async def _sweep() -> tuple[str | None, str]:
    """全网段扫描：先扫 8767 得候选，再逐个确认 8768 + /health"""
    started = time.time()
    local = _local_ip()
    if not local:
        return None, "无法获取本机局域网 IP，跳过扫描"

    hosts = _subnet_hosts(local)
    sem = asyncio.Semaphore(SWEEP_CONCURRENCY)
    hits = await asyncio.gather(*[_port_open(h, KWS_PORT, sem) for h in hosts])
    candidates = [h for h, ok in zip(hosts, hits) if ok]

    for ip in candidates:
        if await _port_open(ip, ASR_PORT, sem, timeout=0.8) and await _health_ok(ip):
            cost = int((time.time() - started) * 1000)
            return ip, f"扫描 {len(hosts)} 个地址，命中 {len(candidates)} 个，确认 {ip}（{cost}ms）"

    cost = int((time.time() - started) * 1000)
    return None, (
        f"扫描 {len(hosts)} 个地址，8767 命中 {len(candidates)} 个，"
        f"但无一同时提供 8768 且 /health 正常（{cost}ms）"
    )


async def discover(force: bool = False) -> dict:
    """返回端侧语音服务地址；命中缓存直接返回，否则先快路径重试再扫描"""
    now = time.time()

    if not force and _state["ip"] and now - _state["checked_at"] < CACHE_TTL:
        return _build(_state["ip"], _state["detail"], cached=True)

    # 快路径：上次成功的 IP 还在用就直接复用，避免每次都全网段扫
    if _state["ip"]:
        sem = asyncio.Semaphore(4)
        if await _port_open(_state["ip"], KWS_PORT, sem, timeout=0.6) and await _health_ok(_state["ip"]):
            _state["checked_at"] = now
            _state["detail"] = f"复用上次地址 {_state['ip']}"
            return _build(_state["ip"], _state["detail"])

    ip, detail = await _sweep()
    _state["ip"] = ip
    _state["checked_at"] = now
    _state["detail"] = detail
    if ip:
        backend_log(f"[端侧发现] 找到香橙派语音服务: {ip}（{detail}）")
    return _build(ip, detail)


async def background_refresh() -> None:
    """后台常驻：应用启动时预热一次，之后按 REFRESH_INTERVAL 周期刷新"""
    try:
        result = await discover(force=True)
        backend_log(
            f"[端侧发现] 启动预热: {'命中 ' + str(result['ip']) if result['found'] else '未找到'} | {result['detail']}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        backend_log(f"[端侧发现] 启动预热异常: {exc}")

    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL)
            await discover(force=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            backend_log(f"[端侧发现] 周期刷新异常: {exc}")
