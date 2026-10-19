"""
【模块二：步骤式引导系统】完整实操演示

对标《智能烹饪项目全环节技能实操讲解文档》环节二：
- StepGuide 游标状态机：实时锁定当前步骤、自动归档已完成步骤、驱动步骤递进
- 边界校验：避免步骤越界、流程卡死
- 三色进度条可视化：绿色=已完成、蓝色=当前进行、灰色=未执行
- 交互式实操：用户确认完成/跳过后才进入下一步
"""

import sys
from typing import Optional, List
from dataclasses import dataclass

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# ==================== 数据结构定义 ====================

@dataclass
class RecipeStep:
    """【数据结构】菜谱步骤 - 遵循领域驱动设计(DDD)中的值对象模式"""
    order: int
    title: Optional[str] = None
    content: str = ""
    tip: Optional[str] = None


# ==================== StepGuide 游标状态机 ====================

class StepGuide:
    """
    【演示点】StepGuide 游标状态机

    维护两个关键状态：
    - current_step: 当前步骤索引（游标）
    - completed_steps: 已完成步骤列表

    支持两种操作：
    - confirm_step: 确认完成（记录 + 推进）
    - skip_step: 跳过（仅推进，不记录）
    """

    def __init__(self, steps: List[RecipeStep]):
        self.steps = steps
        self.current_step = 0          # 游标：当前步骤索引
        self.completed_steps = []      # 已完成步骤索引列表

    def get_current_step(self) -> Optional[RecipeStep]:
        """获取当前步骤 - 带边界校验，确保索引不会越界"""
        if 0 <= self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def confirm_step(self) -> bool:
        """确认完成 - 将当前步骤加入已完成列表，驱动游标递进"""
        if self.current_step >= len(self.steps):
            return False  # 边界校验：防止越界
        self.completed_steps.append(self.current_step)
        self.current_step += 1
        return True

    def skip_step(self) -> bool:
        """跳过步骤 - 只推进游标，不记录完成状态（适用于可选步骤）"""
        if self.current_step >= len(self.steps):
            return False
        self.current_step += 1
        return True

    def get_progress(self) -> dict:
        """进度计算算法 - 实时反馈完成百分比"""
        return {
            "current": self.current_step + 1,   # 当前步骤（从1开始）
            "total": len(self.steps),           # 总步骤数
            "completed": len(self.completed_steps),  # 已完成数
            "progress": (self.current_step + 1) / len(self.steps) if self.steps else 0
        }

    def get_progress_bar_config(self) -> List[str]:
        """
        【演示点】三色进度条配置

        前端可视化进度UI的核心数据源：
        - 已完成步骤 → 绿色（🟩）
        - 当前进行步骤 → 蓝色（🟦）
        - 未执行步骤 → 灰色（⬜）
        """
        colors = []
        for i in range(len(self.steps)):
            if i < self.current_step:
                colors.append("🟩")       # 已完成 → 绿色
            elif i == self.current_step:
                colors.append("🟦")       # 当前进行 → 蓝色
            else:
                colors.append("⬜")       # 未执行 → 灰色
        return colors

    def is_completed(self) -> bool:
        """检查是否所有步骤都已完成"""
        return self.current_step >= len(self.steps)


# ==================== 演示运行 ====================

def main():
    print("=" * 56)
    print("【模块二：步骤式引导系统】实操演示")
    print("=" * 56)

    # 定义五个步骤
    steps = [
        RecipeStep(1, "准备食材", "番茄切块，鸡蛋打散备用", "鸡蛋打散时可以加少许盐"),
        RecipeStep(2, "炒鸡蛋", "热锅下油，倒入蛋液翻炒至半熟盛出", "火候不要太大"),
        RecipeStep(3, "加入番茄", "锅中加油，放入番茄翻炒出汁", "可以用锅铲按压番茄帮助出汁"),
        RecipeStep(4, "调味", "加入盐、糖调味，倒入炒好的鸡蛋翻炒均匀", "糖可以中和番茄的酸味"),
        RecipeStep(5, "完成", "装盘，撒上葱花即可享用", "可以淋少许香油增香"),
    ]

    guide = StepGuide(steps)

    # 交互式步骤引导流程：用户确认后才进入下一步
    print("\n操作说明：直接回车 = 确认完成；输入 s = 跳过；输入 q = 退出\n")

    while not guide.is_completed():
        step = guide.get_current_step()
        progress = guide.get_progress()

        # 三色进度条可视化展示
        bar = " ".join(guide.get_progress_bar_config())
        print(f"  进度条: {bar}")
        print(f"--- 步骤 {progress['current']}/{progress['total']} ---")
        print(f"  标题: {step.title}")
        print(f"  内容: {step.content}")
        if step.tip:
            print(f"  提示: {step.tip}")
        print(f"  进度: {progress['progress'] * 100:.0f}%")

        # 等待用户操作
        action = input("  [操作] 回车确认完成 / s 跳过 / q 退出 > ").strip().lower()
        if action == "q":
            print("\n🚪 已退出演示")
            return
        elif action == "s":
            print("  [操作] 跳过本步骤 ↦\n")
            guide.skip_step()
        else:
            print("  [操作] 确认完成 ✓\n")
            guide.confirm_step()

    # 展示最终结果
    final_progress = guide.get_progress()
    print("✅ 所有步骤已完成!")
    print(f"  总步骤: {final_progress['total']}")
    print(f"  已完成: {final_progress['completed']}")
    print(f"  最终进度条: {' '.join(guide.get_progress_bar_config())}")


if __name__ == "__main__":
    main()
