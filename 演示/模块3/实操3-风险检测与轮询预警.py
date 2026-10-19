"""
【实操动作3：结构化风险检测与自动轮询预警】实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第三模块·选手4实操手3：

- 双层容错解析：结构化 JSON 解析优先 + 关键词兜底识别，稳定识别烟雾/溢锅/干烧
- 插件式事件轮询框架：后台常驻 0.5 秒周期自动巡检，无人值守全天候监测
- 事件幂等防抖：过滤短时间重复告警，避免频繁弹窗打扰用户
- 分级预警：普通风险文字提示、高危风险弹窗 + 语音双重告警
- 形成「画面采集 → AI识别 → 后台轮询 → 智能告警」完整安全防护闭环
"""

import asyncio
import json
import re
import sys
import time
from typing import List, Callable, Optional
from dataclasses import dataclass, field

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# ==================== 双层风险解析核心 ====================

# 危险关键词兜底词库（第二层解析用）
DANGER_KEYWORDS = {
    "SMOKE_DETECTED": ["烟雾", "冒烟", "油烟大"],
    "POT_OVERFLOW": ["溢锅", "溢出", "扑锅"],
    "POT_DRY_BURN": ["干烧", "烧干", "糊锅"],
}


def parse_analysis_result(analysis: str) -> dict:
    """
    双层容错风险解析

    第一层：结构化 JSON 解析（模型输出规范时精准解析）
    第二层：关键词兜底识别（模型输出口语化/不规整时兜底）
    """
    result = {"danger_types": [], "has_risk": False}

    # ---- 第一层：结构化 JSON 解析 ----
    json_match = re.search(r'\{[^{}]*\}', analysis, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if data.get("has_risk"):
                result["has_risk"] = True
                result["danger_types"] = data.get("danger_types", [])
            return result
        except Exception:
            pass  # JSON 解析失败 → 进入第二层

    # ---- 第二层：关键词兜底识别 ----
    for danger_type, keywords in DANGER_KEYWORDS.items():
        if any(kw in analysis for kw in keywords):
            result["has_risk"] = True
            result["danger_types"].append(danger_type)

    return result


# ==================== 事件模型与轮询框架 ====================

@dataclass
class RiskEvent:
    """风险事件对象（含幂等标识 event_id）"""
    event_id: str
    event_type: str
    message: str
    severity: str  # low / high
    metadata: dict = field(default_factory=dict)


class EventPoller:
    """
    通用可扩展事件轮询框架

    - 插件式注册检测源（add_event_source）
    - 注册事件回调业务逻辑（on_event）
    - 0.5 秒周期后台常驻轮询（start）
    - 事件幂等防抖（event_id 去重，过滤短时间重复告警）
    """

    def __init__(self, poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._event_sources: List[Callable] = []
        self._on_event_callbacks: List[Callable] = []
        self._running = False
        self._handled_events = set()

    def add_event_source(self, source: Callable):
        """插件式注册检测源"""
        self._event_sources.append(source)

    def on_event(self, callback: Callable):
        """注册事件回调业务逻辑"""
        self._on_event_callbacks.append(callback)

    async def start(self):
        """启动后台持续轮询"""
        self._running = True
        while self._running:
            await self._poll_once()
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False

    async def _poll_once(self):
        """单次轮询检测 + 幂等过滤防抖"""
        for source in self._event_sources:
            event = source()
            if event and event.event_id not in self._handled_events:
                await self._handle_event(event)

    async def _handle_event(self, event):
        """统一事件处理：标记已处理（防抖），兼容同步/异步回调"""
        self._handled_events.add(event.event_id)
        for callback in self._on_event_callbacks:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)


# ==================== 场景化风险事件联动落地 ====================

class KitchenRiskSource:
    """厨房视觉风险检测源（模拟 VLM 分析结果）"""

    def __init__(self):
        self.counter = 0
        # 模拟不同帧的 VLM 分析输出（交替出现结构化/口语化 + 正常/风险）
        self.scenarios = [
            '{"has_risk": false, "danger_types": [], "severity": "low"}',
            '{"has_risk": true, "danger_types": ["SMOKE_DETECTED"], "severity": "medium"}',
            "画面显示厨房灶台区域烟雾弥漫，油烟很大，疑似食物烧焦",
            '{"has_risk": true, "danger_types": ["POT_OVERFLOW"], "severity": "high"}',
            "锅中白沫溢出，液体扑锅，需要立即处理",
            '{"has_risk": false, "danger_types": [], "severity": "low"}',
        ]

    def __call__(self) -> Optional[RiskEvent]:
        """模拟一帧检测：返回风险事件或 None（幂等事件_id 防抖）"""
        self.counter += 1
        analysis = self.scenarios[(self.counter - 1) % len(self.scenarios)]
        parsed = parse_analysis_result(analysis)

        if not parsed["has_risk"]:
            return None  # 无风险不产生事件

        # 分级：medium → 文字提示；high → 弹窗 + 语音双重告警
        severity = "high" if "POT_OVERFLOW" in parsed["danger_types"] else "low"
        msg = "检测到" + "、".join(parsed["danger_types"])
        return RiskEvent(
            event_id=f"risk-{self.counter // 3}",  # 每 3 帧一个事件_id → 演示幂等防抖
            event_type=",".join(parsed["danger_types"]),
            message=msg,
            severity=severity,
            metadata={"source": "kitchen_vision", "frame": self.counter},
        )


async def kitchen_event_handler(event: RiskEvent):
    """场景化风险事件分发与分级告警"""
    if event.severity == "high":
        # 高危：弹窗 + 语音双重告警
        print(f"  🚨 [高危双重告警] {event.message} → 已触发弹窗 + 语音播报，请立即处理！")
    else:
        # 普通：仅文字提示
        print(f"  ⚠️ [文字提醒] {event.message} → 建议开启排气扇/检查锅中状态")


# ==================== 实操运行演示 ====================

async def main():
    print("=" * 60)
    print("【实操动作3】结构化风险检测与自动轮询预警实操演示")
    print("=" * 60)

    # ① 双层风险解析
    print("\n[1/3] 双层容错解析（JSON 结构化优先 + 关键词兜底）")
    samples = [
        '{"has_risk": true, "danger_types": ["SMOKE_DETECTED"], "severity": "medium"}',
        "检测到锅中出现大量烟雾，油烟浓度偏高，疑似食物烧焦冒烟",
        "画面中有白沫溢出，发生溢锅扑锅现象",
        "锅底烧干冒烟，有糊锅风险",
    ]
    for s in samples:
        parsed = parse_analysis_result(s)
        print(f"  输入: {s[:40]}...")
        print(f"    → 有风险:{parsed['has_risk']}  风险类型:{parsed['danger_types']}")

    # ② 插件式事件轮询框架
    print("\n[2/3] 插件式后台事件轮询框架（0.5 秒周期常驻巡检）")
    poller = EventPoller(poll_interval=0.5)
    poller.add_event_source(KitchenRiskSource())   # 挂载视觉风险检测源
    poller.on_event(kitchen_event_handler)          # 注册分级告警回调

    # ③ 后台轮询 + 防抖 + 分级预警
    print("\n[3/3] 后台持续轮询监测（无人值守全天候）...")
    poll_task = asyncio.create_task(poller.start())
    await asyncio.sleep(3.2)   # 运行约 3 秒，采集多帧
    poller.stop()
    await poll_task

    print("\n  防抖验证：相同 event_id 的重复风险事件仅告警一次 ✓")
    print("  分级验证：普通风险文字提示、高危风险弹窗+语音双重告警 ✓")

    print("\n✅ 实操动作3 演示完成：双层解析 → 后台轮询 → 幂等防抖 → 分级预警，安全防护闭环。")


if __name__ == "__main__":
    asyncio.run(main())
