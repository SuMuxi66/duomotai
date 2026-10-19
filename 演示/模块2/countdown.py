"""
【实操动作3：口语化智能定时器】交互式倒计时实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第二模块·选手3实操手2：

交互式对话使用：程序循环等待你输入口语化倒计时指令，自动解析并后台计时，
倒计时结束自动提醒「时间到了，XX好了！」。

支持指令示例：
  > 帮我倒计时5秒煮鸡蛋
  > 倒计时3分钟泡茶
  > 1小时30分熬汤
  > 查看               查看当前所有进行中的倒计时
  > 取消 泡茶          取消单个倒计时
  > 清空               清空全部倒计时
  > 帮助               查看帮助
  > 退出               退出程序

技术亮点：
- 口语化时间解析：中文数字（三十秒）、混合单位（1小时30分）、半（三分半）等
- 异步协程后台运行：创建即应答，不阻塞对话输入
- 绝对时间戳计时：规避系统调度累计误差，计时精准稳定
- 多任务并行：可同时挂多个倒计时，各自到期独立提醒
- 全生命周期管控：单删 / 清空，删除同步终止后台协程，杜绝残留提醒
- 场景化差异化提醒话术：适配焖煮 / 焯水 / 收汁等真实烹饪场景
"""

import re
import asyncio
import sys
from datetime import datetime

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')


# 中文数字映射（口语化"三分钟""五分钟"等）
CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 口语化时间解析正则：支持 "5秒" "3分钟" "1小时30分" "三分半" 等混合句式
TIME_PATTERN = re.compile(
    r"(?P<num>[零一二三四五六七八九十\d]+(?:\.\d+)?|半)?\s*(?P<unit>小时|分钟|分|秒)",
    re.S,
)
# 烹饪场景关键词
SCENE_PATTERN = re.compile(r"(焖煮|焯水|收汁|浸泡|腌制|加热)")


def cn_to_num(cn_str: str) -> int:
    """中文数字转阿拉伯数字"""
    if cn_str.isdigit():
        return int(cn_str)
    total = 0
    if "十" in cn_str:
        if len(cn_str) == 1:
            return 10
        if cn_str[0] == "十":
            total = 10 + CN_NUM.get(cn_str[1], 0)
        elif cn_str[-1] == "十":
            total = CN_NUM.get(cn_str[0], 0) * 10
        else:
            total = CN_NUM.get(cn_str[0], 0) * 10 + CN_NUM.get(cn_str[1], 0)
    else:
        total = sum(CN_NUM.get(c, 0) for c in cn_str)
    return total


def parse_time_command(text: str) -> dict:
    """
    口语化定时指令统一解析入口

    从"帮我设置一个五分钟的焖煮计时"中精准提取：
    - 计时时长（5分钟）
    - 烹饪场景（焖煮）
    """
    scene_res = SCENE_PATTERN.search(text)
    context = scene_res.group() if scene_res else "通用烹饪"

    total_seconds = 0
    for num_str, unit in TIME_PATTERN.findall(text):
        if num_str == "半":
            num = 0.5
        elif num_str:
            num = float(num_str) if "." in num_str else cn_to_num(num_str)
        else:
            num = 1
        if unit == "小时":
            total_seconds += int(num * 3600)
        elif unit in ("分", "分钟"):
            total_seconds += int(num * 60)
        elif unit == "秒":
            total_seconds += int(num)

    return {"description": f"{context}计时", "seconds": total_seconds, "context": context}


def parse_interactive_command(text: str) -> dict:
    """
    交互式倒计时指令解析：提取 秒数 + 事件名 + 场景

    支持句式：
      "帮我倒计时5秒煮鸡蛋"   → 5秒 / 煮鸡蛋
      "倒计时3分钟泡茶"       → 3分钟 / 泡茶
      "1小时30分熬汤"         → 1小时30分 / 熬汤
      "5秒后关火"             → 5秒 / 关火
    """
    time_info = parse_time_command(text)
    total_seconds = time_info["seconds"]

    # 移除所有时间片段，剩余文本用于提取事件名
    rest = text
    for m in sorted(TIME_PATTERN.finditer(text), key=lambda x: x.start(), reverse=True):
        rest = rest[:m.start()] + rest[m.end():]

    name = rest
    for w in ("帮我", "麻烦", "请", "设置一个", "设置", "一个", "倒计时", "计时", "定时",
              "倒", "数", "的", "从", "开始", "后", "好了", "吧", "啊", "呢", "半"):
        name = name.replace(w, " ")
    name = re.sub(r"\s+", " ", name).strip(" ，。、！!？?;；:：")
    if not name:
        name = "计时任务"

    return {"seconds": total_seconds, "name": name, "context": time_info["context"]}


# ==================== 定时器核心 ====================

class CookingTimer:
    """单个烹饪定时器：绝对时间戳计时"""

    def __init__(self, task_name: str, total_sec: int):
        self.task_name = task_name
        self.total_sec = total_sec
        self.start_time = datetime.now().timestamp()  # 绝对时间戳
        self.task: "asyncio.Task | None" = None

    def get_left_time(self) -> int:
        """获取剩余时间：绝对时间戳差值，规避调度累计误差"""
        pass_time = datetime.now().timestamp() - self.start_time
        return max(0, int(self.total_sec - pass_time))

    async def timer_task(self, callback):
        """异步计时主任务"""
        while self.get_left_time() > 0:
            await asyncio.sleep(1)
        # 计时结束触发场景化提醒回调
        await callback(self.task_name)


class TimerManager:
    """定时器全局管理器：创建 / 删除 / 清空全生命周期管控"""

    def __init__(self):
        self.timer_dict: dict = {}

    async def create_timer(self, name: str, sec: int, callback) -> str:
        """创建新计时任务（异步后台运行，即刻应答不阻塞）"""
        timer = CookingTimer(name, sec)
        timer.task = asyncio.create_task(timer.timer_task(callback))
        self.timer_dict[name] = timer
        return f"好的，已为您创建 **【{name}】** {sec} 秒倒计时，时间到会自动提醒您。"

    async def del_timer(self, name: str) -> str:
        """删除单个计时任务：同步终止后台协程，杜绝残留提醒"""
        if name in self.timer_dict:
            if not self.timer_dict[name].task.done():
                self.timer_dict[name].task.cancel()
            del self.timer_dict[name]
            return f"已取消 **【{name}】** 倒计时"
        return f"未找到名为 **【{name}】** 的倒计时"

    async def clear_all(self) -> str:
        """全局批量归零清空所有计时任务"""
        for timer in self.timer_dict.values():
            if not timer.task.done():
                timer.task.cancel()
        self.timer_dict.clear()
        return "已清空所有倒计时"


# ==================== 终端 Markdown 渲染 ====================

def _print_markdown(text: str):
    """终端 Markdown 渲染（使用 rich 库），失败则退化为纯文本"""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console(soft_wrap=True).print(Markdown(text))
    except Exception:
        print(text)


# ==================== 场景化提醒 ====================

async def scene_reminder(task_name: str):
    """差异化场景提醒话术：统一「时间到了，XX好了！」，场景关键词附加烹饪建议"""
    if "焖煮" in task_name:
        extra = "请检查锅中状态，是否需要翻动或调整火候？"
    elif "焯水" in task_name:
        extra = "焯水完毕，请捞出食材备用。"
    elif "收汁" in task_name:
        extra = "注意火候，避免糊锅！"
    else:
        extra = ""
    msg = f"## ⏰ 时间到了，**{task_name}**好了！"
    if extra:
        msg += f"\n\n{extra}"
    print()
    _print_markdown(msg)
    print()


# ==================== 交互辅助 ====================

def print_help():
    """打印交互帮助信息（Markdown 渲染）"""
    _print_markdown(
        "## 交互式倒计时使用帮助\n\n"
        "- **帮我倒计时5秒煮鸡蛋**：创建 5 秒倒计时（事件：煮鸡蛋）\n"
        "- **倒计时3分钟泡茶**：创建 3 分钟倒计时（事件：泡茶）\n"
        "- **1小时30分熬汤**：混合单位解析，创建 1.5 小时倒计时\n"
        "- **查看 / 列表**：查看当前所有进行中的倒计时\n"
        "- **取消 泡茶**：取消名为「泡茶」的倒计时\n"
        "- **清空**：清空全部倒计时\n"
        "- **帮助 / ？**：查看本帮助\n"
        "- **退出 / quit**：退出程序"
    )


def print_timers(manager):
    """打印当前所有进行中的倒计时（Markdown 渲染）"""
    if not manager.timer_dict:
        print("当前没有进行中的倒计时。")
        return
    lines = ["## 进行中的倒计时", ""]
    for name, timer in manager.timer_dict.items():
        lines.append(f"- **{name}**：剩余 {timer.get_left_time()} 秒")
    _print_markdown("\n".join(lines))


# ==================== 交互式主循环 ====================

async def main():
    _print_markdown(
        "# 实操动作3 · 口语化智能定时器\n\n"
        "交互式倒计时演示：输入 `帮我倒计时5秒煮鸡蛋` 开始，"
        "辅助命令：`查看` / `取消 名称` / `清空` / `帮助` / `退出`"
    )

    manager = TimerManager()

    while True:
        # 输入放到线程池执行，不阻塞后台倒计时任务
        try:
            raw = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        text = raw.strip()
        if not text:
            continue
        low = text.lower()

        if low in ("退出", "quit", "exit", "q"):
            break
        if low in ("帮助", "help", "?"):
            print_help()
            continue
        if low in ("查看", "列表", "list", "ls"):
            print_timers(manager)
            continue
        if low in ("清空", "clear", "重置"):
            _print_markdown(await manager.clear_all())
            continue

        m = re.match(r"^(取消|删除|remove|del)\s+(.+)$", text)
        if m:
            _print_markdown(await manager.del_timer(m.group(2).strip()))
            continue

        # 默认按倒计时指令解析
        info = parse_interactive_command(text)
        if info["seconds"] <= 0:
            print("未识别到有效时长，请输入如「帮我倒计时5秒煮鸡蛋」。")
            continue
        reply = await manager.create_timer(info["name"], info["seconds"], scene_reminder)
        _print_markdown(reply)

    # 退出前清理所有后台计时任务
    if manager.timer_dict:
        await manager.clear_all()
        print("已清理所有进行中的倒计时。")
    print("再见！")


if __name__ == "__main__":
    asyncio.run(main())
