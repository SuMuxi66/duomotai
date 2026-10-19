# -*- coding: utf-8 -*-
"""
★ 演示文件 - 倒计时全语音链路 + 到期打断让路

模块对应：
  - 全语音入口：自然语言 → 解析倒计时请求（真实: cooking_core.py#parse_timer_request）
  - 到期触发播报：到期“插队”语音播报（真实: ws_handlers/timer.py#notify_timer_complete）
  - 正在交互怎么办：interrupted 打断标志按块让路（真实: cooking_core.py#send_cooking_text_with_tts）

本文件保留核心逻辑、模拟一条“语音设定→异步等待→到期播报→用户插话让路”链路，
用于演示/讲解，不可直接执行。
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from timer_manager import create_countdown, get_all_countdowns


# ============================
# ① 全语音入口：一句话 → 倒计时参数
# ============================
def parse_timer_request(text: str) -> Optional[dict]:
    """
    从用户语音识别结果解析倒计时请求

    支持："倒计时5分钟" / "计时3分钟" / "设置一个2分钟的计时器" / "2分钟后提醒我"
    Returns: {"seconds": int, "description": str} 或 None
    """
    patterns = [
        r"倒计时\s*(\d+)\s*(分钟|分|秒)",
        r"计时\s*(\d+)\s*(分钟|分|秒)",
        r"设置\s*(?:一个)?\s*(\d+)\s*(分钟|分|秒)\s*(?:的)?\s*计时器",
        r"(\d+)\s*(分钟|分|秒)\s*后?\s*(?:提醒|叫我|通知)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            seconds = num * 60 if unit in ("分钟", "分") else num
            description = text.replace(match.group(0), "").strip()
            if not description:
                description = f"{num}{unit}倒计时"
            return {"seconds": seconds, "description": description}
    return None


# ============================
# ② 会话状态（只有打断标志，演示用）
# ============================
class SessionState:
    def __init__(self):
        self.interrupted: bool = False   # 用户正在交互（随时打断已触发）


# ============================
# ③ 到期播报：与交互不互扰
# ============================
async def send_cooking_text_with_tts(state: SessionState, text: str):
    """
    模拟 AI 语音播报（逐块推送）
    关键：每推一块都检查 state.interrupted ——
    用户正在交互（插话）→ 播报立刻中止让路，交互优先。
    """
    display_chunks = list(text)          # 一字符一块，模拟流式播报
    emitted: List[str] = []
    for chunk in display_chunks:
        if state.interrupted:            # ★ 倒计时播报中用户插话 → 让路
            print("\n    [打断] 用户正在交互 → 播报中止，交互优先")
            return
        emitted.append(chunk)
        print(chunk, end="", flush=True)
        await asyncio.sleep(0.05)        # 模拟 TTS 流式输出
    print()


# ============================
# ④ 到期回调：插入播报（独立异步任务，与主交互并存）
# ============================
def on_timer_complete(state: SessionState):
    """到期 → 触发语音播报（作为独立异步任务运行，不阻塞当前交互）"""
    async def _run():
        print(f"\n[{time.strftime('%H:%M:%S')}] ⏰ 倒计时到期 → 插队语音播报：")
        await send_cooking_text_with_tts(state, "时间到了，可以关火了！")
    return asyncio.create_task(_run())


# ============================
# 主入口：语音设定倒计时
# ============================
async def handle_user_text(state: SessionState, text: str):
    """语音进来一句话：先试倒计时，再试普通对话（演示版）"""
    timer_req = parse_timer_request(text)
    if timer_req:
        timer_id = create_countdown(
            description=timer_req["description"],
            seconds=timer_req["seconds"],
            category="cooking",
            on_complete=lambda timer: on_timer_complete(state),   # 到期播报
        )
        print(f"[{time.strftime('%H:%M:%S')}] 语音回执：好的，{timer_req['description']}，{timer_req['seconds']}秒后提醒您。")
        print(f"[{time.strftime('%H:%M:%S')}] 活动倒计时: {[t['description'] for t in get_all_countdowns()]}")
        return True
    return False


# ============================
# 演示用法
# ============================
if __name__ == "__main__":
    async def demo():
        state = SessionState()

        print("===== 场景1：语音设定倒计时，等待期间继续对话（互不干扰） =====")
        # 用户语音："倒计时5分钟"（演示用3秒）
        handled = await handle_user_text(state, "倒计时5分钟")
        assert handled

        # 倒计时等待期间，用户继续提问 —— 主流程完全不受影响
        await asyncio.sleep(1.5)
        print(f"\n[{time.strftime('%H:%M:%S')}] [对话] 用户：我加盐加多少克？ → 正常进入LLM问答…")

        print("\n\n===== 场景2：倒计时到期瞬间，用户正在交互 → 播报让路 =====")
        # 让倒计时先到期触发播报，同时立即模拟用户插话（回声门控触发打断）
        await asyncio.sleep(1.5)
        print(f"[{time.strftime('%H:%M:%S')}] [用户] 正在讲话…（回声门控：真人语音能量>基线 → interrupted=True）")
        state.interrupted = True
        await asyncio.sleep(2)

        print("\n===== 结论：交互与倒计时互不干扰，谁急谁先上 =====")

    asyncio.run(demo())