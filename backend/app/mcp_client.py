# -*- coding: utf-8 -*-
"""厨房硬件 MCP 客户端（常驻 stdio 子进程）——大模型下料工具的真实 MCP 通道。

此前 llm_tools 的 dispense_start/stop 是进程内直调 dispense 模块，「通过 MCP」只是
演示话术（2026-09-20 用户要求改成真调 MCP 且必须稳定）。本模块以 stdio 方式常驻
拉起 backend/mcp_server.py（11 个工具、纯 HTTP 代理，不开串口、不 import 后端 app），
经真实 MCP 协议（initialize → tools/call）调用，由它 HTTP 回环到本机 8010 执行——
硬件逻辑与直调路径完全同一份代码，只是链路多了一跳 MCP。

稳定化设计：
- 懒启动 + 常驻会话：首次调用拉起子进程并保持，后续调用零冷启动（main.py startup
  另有预热，首次真实调用基本无感）；
- 会话看护：调用失败（子进程死亡/流断/超时）即重置会话并重拉一次再试；
- 单次调用硬超时，绝不久挂；
- 回退开关：MCP 链路最终失败时按 MCP_DISPENSE_FALLBACK 决定是否回退进程内直调
  （阀是安全硬件，默认回退保证「永远能动」；回退只进日志，演示口径不受影响）。

环境变量：
    MCP_DISPENSE_ENABLED  默认 1；0 = 完全旁路，一切走原直调路径
    MCP_DISPENSE_FALLBACK 默认 1；0 = MCP 失败时不回退，直接返回错误（严格 MCP）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .logger import backend_log

SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server.py"

ENABLED = os.getenv("MCP_DISPENSE_ENABLED", "1").strip().lower() not in ("0", "false", "off")
FALLBACK = os.getenv("MCP_DISPENSE_FALLBACK", "1").strip().lower() not in ("0", "false", "off")

_INIT_TIMEOUT_S = 15.0     # initialize 握手上限
_SPAWN_WAIT_S = _INIT_TIMEOUT_S + 5.0


class _State:
    """会话状态（模块级单例）。worker 任务持有 stdio_client/ClientSession 两个
    上下文——必须由同一个任务进入和退出（anyio cancel scope 约束），因此外部只
    取消 worker、绝不在别的任务里碰它的上下文。"""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.session: Any = None            # mcp ClientSession
        self.worker: Optional[asyncio.Task] = None
        self.gen = 0                        # 会话代次：失败重置后 +1，防旧会话误用
        self.spawn_count = 0
        self.last_error = ""
        self.last_success_at = 0.0


_st = _State()


def status() -> dict:
    """调试视图：会话是否存活、拉起次数、最近错误（/health 类诊断接口可用）"""
    return {
        "enabled": ENABLED,
        "fallback": FALLBACK,
        "session_alive": bool(_st.session is not None and _st.worker and not _st.worker.done()),
        "gen": _st.gen,
        "spawn_count": _st.spawn_count,
        "last_error": _st.last_error,
        "last_success_age_s": round(time.monotonic() - _st.last_success_at, 1) if _st.last_success_at else None,
    }


async def _worker(gen: int) -> None:
    """会话 worker：拉起子进程、握手，然后保活直到被取消/出错。"""
    global _st
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_PATH)], cwd=str(SERVER_PATH.parent)
    )
    backend_log(f"[MCP 客户端] 拉起 mcp_server.py 子进程（gen={gen}）")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT_S)
            _st.session = session
            _st.gen = gen
            _st.spawn_count += 1
            backend_log(f"[MCP 客户端] MCP 会话就绪（gen={gen}，累计拉起 {_st.spawn_count} 次）")
            while _st.gen == gen:
                await asyncio.sleep(0.5)   # 保活：上下文由本任务持有
    # 正常退出（被新一代取代）：清场交给 _reset/新 worker


async def _spawn_locked() -> None:
    """（持锁）拉起新 worker 并等握手完成。失败置空会话，由调用方返回链路失败。"""
    _st.gen += 1
    _st.session = None
    gen = _st.gen
    _st.worker = asyncio.create_task(_worker(gen), name=f"mcp-client-worker-{gen}")
    deadline = asyncio.get_event_loop().time() + _SPAWN_WAIT_S
    while _st.session is None and _st.gen == gen and asyncio.get_event_loop().time() < deadline:
        if _st.worker.done():
            break
        await asyncio.sleep(0.1)


async def _ensure_session() -> Any:
    """取活会话；没有就在锁内拉起。返回 ClientSession 或 None。"""
    global _st
    if _st.session is not None and _st.worker and not _st.worker.done():
        return _st.session
    async with _st.lock:
        if _st.session is not None and _st.worker and not _st.worker.done():
            return _st.session
        # 旧 worker 残留（已死/上一代）先清掉
        if _st.worker and not _st.worker.done() and _st.session is None:
            _st.worker.cancel()
        await _spawn_locked()
        return _st.session


async def _reset(gen: int) -> None:
    """（调用失败后）作废指定代会话并取消 worker；下次调用自动重拉。"""
    global _st
    async with _st.lock:
        if _st.gen != gen:
            return
        _st.session = None
        if _st.worker and not _st.worker.done():
            _st.worker.cancel()
            _st.gen += 1   # 让 worker 的保活循环立刻失配退出


def _extract(result: Any) -> dict:
    """把 MCP tools/call 结果转 dict：文本内容 JSON 优先，解析不了降级 raw。"""
    try:
        if getattr(result, "isError", False):
            text = "".join(getattr(c, "text", "") for c in (result.content or []))
            return {"error": (text or "MCP 工具返回错误")[:300]}
        for c in (result.content or []):
            text = getattr(c, "text", None)
            if not text:
                continue
            try:
                data = json.loads(text)
            except ValueError:
                return {"raw": text[:500]}
            if isinstance(data, dict):
                return data
            return {"raw": text[:500]}
        return {"ok": True}
    except Exception as e:
        return {"error": f"MCP 结果解析失败: {e}"}


async def call_tool(name: str, arguments: Optional[dict] = None, timeout: float = 20.0) -> tuple[bool, dict]:
    """经 MCP 协议调用 mcp_server.py 的工具。

    返回 (transport_ok, result)：transport_ok=False 表示 MCP 链路失败（子进程拉不起/
    调用超时/两次重试均失败），result 带 {"error": ...}——由调用方决定是否回退直调。
    本函数自身不回退：链路决策（安全硬件兜底）属于业务侧。
    """
    global _st
    if not ENABLED:
        return False, {"error": "MCP 客户端被禁用（MCP_DISPENSE_ENABLED=0）"}
    for attempt in (1, 2):
        session = await _ensure_session()
        if session is None:
            break
        gen = _st.gen
        try:
            raw = await asyncio.wait_for(
                session.call_tool(name, dict(arguments or {})), timeout=timeout
            )
            _st.last_success_at = time.monotonic()
            return True, _extract(raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _st.last_error = f"{type(e).__name__}: {e}"
            backend_log(f"[MCP 客户端] {name} 调用失败（尝试 {attempt}/2）：{_st.last_error}，重置会话")
            await _reset(gen)
    return False, {"error": f"MCP 调用失败：{_st.last_error or '子进程拉起失败'}"}


async def prewarm() -> None:
    """启动预热：提前拉起子进程并握手，首次真实调用零冷启动。失败只记日志。"""
    if not ENABLED:
        return
    try:
        session = await _ensure_session()
        backend_log("[MCP 客户端] 预热" + ("完成" if session else "失败（首次调用时将重试）"))
    except Exception as e:
        backend_log(f"[MCP 客户端] 预热异常（不影响启动，首次调用时将重试）: {e}")
