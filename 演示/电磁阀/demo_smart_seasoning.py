# -*- coding: utf-8 -*-
"""
智能调料系统 · 答辩演示脚本
================================

闭环链路：
    语音输入 → 意图拦截 → MCP 读秤 → 大模型计算 → 解析钳位
             → 确认门（锅铲就位）→ MCP 控制电磁阀下料 → 称重达标自动断电

运行方式（推荐用 backend 虚拟环境，httpx 已装好）：
    backend\\.venv\\Scripts\\python.exe 演示\\电磁阀\\demo_smart_seasoning.py            # 自动演示（无硬件也能跑）
    backend\\.venv\\Scripts\\python.exe 演示\\电磁阀\\demo_smart_seasoning.py --i        # 交互模式（现场手敲语音文本）
    backend\\.venv\\Scripts\\python.exe 演示\\电磁阀\\demo_smart_seasoning.py --real    # 接真实硬件（需后端 8010 已启动）
    backend\\.venv\\Scripts\\python.exe 演示\\电磁阀\\demo_smart_seasoning.py --real --i # 真实硬件 + 现场输入

参数说明：
    默认（不带 --real）: 硬件层走 MOCK，秤固定 330g，下料用动画模拟，断网/无串口也能完整演示
    --real           : get_weight / dispense_start 走 MCP 同源的后端 HTTP 端点（8010）
    --i              : 交互模式，自己敲"语音识别结果"
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------- 运行开关 ----------------
REAL = "--real" in sys.argv                       # True = 接真实下料机 / 称重板卡
INTERACTIVE = "--i" in sys.argv or "--interactive" in sys.argv
BASE_URL = os.environ.get("GS_BACKEND_URL", "http://127.0.0.1:8010")

# 真实大模型开关（可选）：配了这俩环境变量就走 OpenAI 兼容接口，否则本地按 0.4% 秒回
LLM_BASE_URL = os.environ.get("DEMO_LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("DEMO_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("DEMO_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")

# ================= 展示辅助 =================
def hr(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 6} {title} {'─' * max(0, 52 - len(title))}")
    else:
        print("─" * 60)

def step(no: int, msg: str) -> None:
    print(f"  [{no}] {msg}")

def info(key: str, val) -> None:
    print(f"      · {key:<12} {val}")

def say(role: str, text: str) -> None:
    """模拟前端气泡 / 语音播报"""
    who = {"user": "🎙 用户", "ai": "🔊 助手", "sys": "⚙  系统"}[role]
    print(f"\n  {who}：{text}")

# ================= 硬件层（MCP 工具封装）=================
# 真实工具名与 backend/mcp_server.py 完全一致：
#   get_weight() / weight_tare() / dispense_start(target_g) / dispense_stop() / dispense_status()
class MockHardware:
    """无硬件时的替身：秤面 330g，下料用动画推进，保证演示不翻车。"""

    def __init__(self):
        self.weight = 330.0          # 演示用：锅里食材 330g
        self.active = False
        self.target = 0.0

    def get_weight(self):
        return {"weight": round(self.weight, 1), "unit": "g", "stable": True, "source": "mock"}

    def dispense_start(self, target_g: float):
        self.active = True
        self.target = round(self.weight + target_g, 1)
        return {"success": True, "target_g": round(target_g, 1),
                "target": self.target, "message": f"下料机已开启，目标 {self.target}g"}

    def dispense_status(self):
        return {"active": self.active, "target": self.target,
                "current": round(self.weight, 1),
                "remaining": round(max(0.0, self.target - self.weight), 1)}

    def dispense_stop(self):
        self.active = False
        return {"success": True, "message": "已断电"}

    def tick(self, delta: float) -> None:
        self.weight = round(self.weight + delta, 1)

class RealHardware:
    """真实硬件：走后端 HTTP 端点（MCP server 也是转发这几个端点）。"""

    def _call(self, method, path, json_body=None, timeout=8.0):
        import httpx
        try:
            with httpx.Client(base_url=BASE_URL, timeout=timeout) as c:
                r = c.request(method, path, json=json_body)
            try:
                return r.json()
            except ValueError:
                return {"status_code": r.status_code, "text": r.text[:200]}
        except Exception as e:
            return {"error": str(e), "hint": f"请确认后端已启动（{BASE_URL}）"}

    def get_weight(self):
        return self._call("GET", "/weight")

    def dispense_start(self, target_g: float):
        if not 0.5 <= target_g <= 100:
            return {"error": "target_g 超出范围", "hint": "合法范围 0.5~100 克"}
        return self._call("POST", "/dispense/start", json_body={"target_g": target_g}, timeout=25.0)

    def dispense_status(self):
        return self._call("GET", "/dispense/status")

    def dispense_stop(self):
        return self._call("POST", "/dispense/stop")

HW = RealHardware() if REAL else MockHardware()

# ---- 与 MCP 工具同名的薄封装，讲解时可直接指着这几行说"这就是 MCP 接口" ----
def get_weight() -> dict:
    """MCP 工具：读取称重传感器当前重量（克）。"""
    return HW.get_weight()

def dispense_start(target_g: float) -> dict:
    """MCP 工具：启动自动下料 target_g 克，称重达标自动断电。"""
    return HW.dispense_start(target_g)

def dispense_status() -> dict:
    """MCP 工具：查询下料状态（进行中 / 目标 / 剩余克数）。"""
    return HW.dispense_status()

def dispense_stop() -> dict:
    """MCP 工具：停止下料。"""
    return HW.dispense_stop()

# ================= 大模型调用 =================
async def llm_chat(prompt: str) -> str:
    """把提示词发给大模型，拿到纯文本回复。"""
    if LLM_BASE_URL and LLM_API_KEY:
        import httpx
        payload = {"model": LLM_MODEL,
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.2}
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
                             json=payload, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    await asyncio.sleep(0.6)                       # 模拟一次网络往返
    m = re.search(r"食材总重\s*([\d.]+)\s*克", prompt)
    weight = float(m.group(1)) if m else 330.0
    return f"{weight * 0.004:.1f}"                 # 取 0.3%~0.5% 中位 0.4%

def extract_grams(text: str) -> float:
    """从大模型回复里抠出第一个数字（回复可能带'约 1.3 克'之类废话）。"""
    m = re.search(r"-?\d+(\.\d+)?", text or "")
    if not m:
        raise ValueError(f"大模型未返回数字：{text!r}")
    return float(m.group(0))

# ================= 会话状态 =================
@dataclass
class SeasoningState:
    awaiting_confirmation: bool = False            # "好了"确认门是否打开
    salt_grams: float = 0.0                        # 待投放克数
    expires_at: float = 0.0                        # 确认门过期时间（10 分钟）

    def gate_open(self) -> bool:
        return self.awaiting_confirmation and time.time() < self.expires_at

    def close(self) -> None:
        self.awaiting_confirmation = False
        self.salt_grams = 0.0
        self.expires_at = 0.0

# ================= ① 意图拦截 =================
# 把"要算调料"的话拦下来，常见的就是：调料 / 准备 / 帮我 / 放调料，或者"盐 + 多少/几克/计算"
def looks_like_salt_calc(t):
    if "调料" in t and any(k in t for k in ("准备", "帮我", "放调料")): return True
    return "盐" in t and any(k in t for k in ("多少", "几克", "计算"))

# 确认词分级（与项目 cooking_core 一致）：整句相等 + 子串两级，避免闲聊误触发
CONFIRM_EXACT = {"好", "行", "ok", "可以", "下一步", "来", "放吧"}
CONFIRM_SUB = ("好了", "完成了", "可以了", "搞定了", "继续", "放好了", "就位", "开始吧")

def matches_confirmation(t: str) -> bool:
    t = (t or "").strip().lower()
    return t in CONFIRM_EXACT or any(k in t for k in CONFIRM_SUB)

# ================= ② 核心流程 =================
async def handle_user_text(text: str, state: SeasoningState) -> None:
    """一条语音文本进来后的完整处理链路。"""

    # ---- 分支 A：确认门已开，等的是"锅铲就位"这句话 ----
    if state.gate_open() and matches_confirmation(text):
        grams = state.salt_grams
        state.close()
        hr("锅铲就位 → MCP 调用电磁阀下料")
        step(1, f"调用 MCP 工具 dispense_start({grams:g})")
        res = dispense_start(grams)
        info("返回", res.get("message") or res)
        if not res.get("success", True):
            say("ai", f"下料机没响应：{res.get('error') or res.get('hint')}")
            return
        await watch_dispense(grams)
        say("ai", f"已为您投放 {grams:g} 克盐，可以翻炒了。")
        return

    if state.gate_open() and not matches_confirmation(text):
        say("ai", "锅铲放到出料口下面了吗？放好说一声「好了」，我就开始下料。")
        return

    # ---- 分支 B：意图拦截命中 → 走调料计算闭环 ----
    if looks_like_salt_calc(text):
        hr("意图拦截命中 → 进入智能调料闭环")

        w = get_weight()                                      # MCP 读秤
        if w.get("error") or w.get("status_code"):
            say("ai", f"称重读取失败：{w.get('error') or w.get('hint') or w}")
            return

        weight = w["weight"]                                  # 秤上食材 330g
        step(1, "MCP 工具 get_weight() 读取称重传感器")
        if not weight or weight <= 0:
            say("ai", "秤面读数是 0，先把锅放上去再来一次。")
            return
        info("秤面重量", f"{weight:g} g")

        prompt = f"食材总重 {weight:g} 克，盐量按食材的 0.3%~0.5%，只返回数字"
        step(2, "把称重数据拼进提示词，发给大模型")
        info("prompt", prompt)

        reply = await llm_chat(prompt)                        # 发给大模型 → 返回 "1.3"
        info("LLM 返回", repr(reply))

        grams = min(100.0, max(0.5, float(extract_grams(reply))))   # 解析 + 安全钳位 0.5~100g
        step(3, "解析返回值并做安全钳位（0.5 ~ 100 g）")
        info("计算用量", f"{grams:g} g")

        state.awaiting_confirmation = True                    # 打开"好了"确认门（10 分钟过期）
        state.salt_grams = grams
        state.expires_at = time.time() + 600
        step(4, "打开确认门，等待锅铲就位")

        say("ai", f"锅里有 {weight:g} 克，按 0.3%~0.5% 计算，放 {grams:g} 克盐比较合适。"
                  f"请把锅铲放到出料口下面，放好说「好了」。")
        return

    # ---- 分支 C：普通问答，放行给大模型 ----
    say("ai", "（未命中调料意图，走普通问答）" + (await llm_chat(text))[:40])

# ================= ③ 下料过程可视化 =================
async def watch_dispense(target_g: float, timeout: float = 20.0) -> None:
    """轮询下料状态，把"重量一点点涨上去"的过程打出来。"""
    start = time.time()
    last = -1.0
    base = float(dispense_status().get("current") or 0.0)     # 下料前秤面重量
    tty = sys.stdout.isatty()                                 # 非终端（投屏/重定向）时降频换行输出
    n = 0
    while time.time() - start < timeout:
        st = dispense_status()
        cur = float(st.get("current") or st.get("weight") or 0.0)
        if abs(cur - last) >= 0.05:
            done = float(st.get("target") or 0.0) - cur
            ratio = min(1.0, max(0.0, (cur - base) / target_g if target_g else 0))
            bar = "█" * int(24 * ratio) + "·" * (24 - int(24 * ratio))
            line = (f"下料中 |{bar}| 已出 {max(0.0, cur - base):.1f}g"
                    f" / {target_g:g}g   剩余 {max(0.0, done):.1f}g")
            if tty:
                print(f"\r      {line}   ", end="", flush=True)
            else:
                n += 1
                if n % 4 == 1 or ratio >= 1.0:
                    print(f"      {line}")
            last = cur
        if not st.get("active", False):
            break
        if isinstance(HW, MockHardware) and HW.active:
            HW.tick(min(0.12, max(0.03, target_g / 18)))      # mock 推进秤面
            if HW.weight >= HW.target:
                HW.active = False
        await asyncio.sleep(0.25)
    if tty:
        print("\r      下料完成，" + " " * 46)

    if REAL:
        st = dispense_status()
        say("sys", f"最终状态：{st}")

# ================= ④ 演示主流程 =================
SCRIPT = [
    "我准备放调料了，请帮我准备一下调料",
    "好了",
    "今天这道菜要放多少盐",
]

async def auto_demo() -> None:
    state = SeasoningState()
    hr("智能调料系统 · 自动演示")
    print(f"      模式：{'真实硬件（' + BASE_URL + '）' if REAL else 'MOCK 演示'}")

    for line in SCRIPT:
        say("user", line)
        await asyncio.sleep(0.8)
        await handle_user_text(line, state)
        await asyncio.sleep(0.6)

    hr("演示结束")

async def interactive_demo() -> None:
    state = SeasoningState()
    hr("智能调料系统 · 交互模式")
    print(f"      模式：{'真实硬件（' + BASE_URL + '）' if REAL else 'MOCK 演示'}")
    print("      直接输入语音识别结果（空行回车退出）")
    print("      试试：我准备放调料了请帮我准备一下调料 / 放多少盐 / 好了\n")
    while True:
        try:
            line = input("  🎙 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        await handle_user_text(line, state)

async def main() -> None:
    print("\n" + "=" * 60)
    print("   智能调料系统 —— 语音 → 读秤 → 大模型 → 电磁阀 闭环演示")
    print("=" * 60)
    if INTERACTIVE:
        await interactive_demo()
    else:
        await auto_demo()
    print("\n  " + "报告项目经理，智能调料系统核心代码开发完成。".center(46) + "\n")

if __name__ == "__main__":
    asyncio.run(main())
