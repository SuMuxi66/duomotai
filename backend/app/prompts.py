import os
from pathlib import Path

def detect_scene(text: str) -> str | None:
    """
    检测用户输入是否匹配预设场景，返回场景回复或 None
    """
    return None

def load_prompt_from_file(file_path: str) -> str:
    """
    从markdown文件加载提示词
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"读取提示词文件失败: {e}")
        return ""

# 加载markdown提示词文件
PROMPTS_DIR = Path(__file__).parent
LLM_PROMPT = load_prompt_from_file(PROMPTS_DIR / "prompts" / "LLM.md")
COOKING_PROMPT = load_prompt_from_file(PROMPTS_DIR / "prompts" / "cooking.md")
VLM_PROMPT = load_prompt_from_file(PROMPTS_DIR / "prompts" / "VLM.md")

VISION_OBSERVER_PROMPT = """你是一个实时视频观察助手。你会收到一张来自"右侧视频流"的单帧画面。
你的任务：用中文输出一句"观察结论"，要求：
- 20~50 字，尽量具体（人物/物体/动作/环境）
- 不要编造看不到的细节；不确定就说"不确定"
- 不要输出多段话，不要加标题，不要加列表
- 不必在乎任何显示在屏幕上面的时间，你也不要说出任何关于你知识库锁定在多少时间之前的信息
- 特别注意：如果看到烟雾、火焰或异常高温冒烟情况，请明确提及"检测到烟雾"或"检测到火情"
"""

# 把"持续视觉观察"作为上下文，供对话模型回答用户问题
CHAT_SYSTEM_PROMPT = LLM_PROMPT

def build_chat_prompt(user_text: str, vision_observations: list, temperature: dict = None, weight: dict = None, custom_prompt: str = "") -> tuple[str, str]:
    """
    构建 LLM prompt，注入视觉观察记录和温度数据作为上下文

    Args:
        user_text: 用户输入的文本
        vision_observations: 视觉观察记录列表，可以是字符串列表或字典列表
        temperature: 温度数据字典，包含目标温度、平均温度、最高温度、最低温度等

    Returns:
        (user_content, system_prompt) 元组
        user_content: 发送给 LLM 的用户消息内容
        system_prompt: 发送给 LLM 的系统提示词
    """
    # 处理不同类型的观察记录
    obs_texts = []
    for obs in vision_observations[-8:]:  # 只取最近 8 条
        if isinstance(obs, dict):
            obs_texts.append(obs.get("text", ""))
        elif isinstance(obs, str):
            obs_texts.append(obs)
    
    obs_block = "\n".join([f"- {o}" for o in obs_texts if o]) or "- （暂无视觉观察）"
    
    # 处理温度数据
    temp_block = ""
    if temperature:
        temp_lines = []
        if "target" in temperature:
            temp_lines.append(f"目标温度: {int(round(temperature['target']))}°C")
        if "average" in temperature:
            temp_lines.append(f"平均温度: {int(round(temperature['average']))}°C")
        if "maximum" in temperature:
            temp_lines.append(f"最高温度: {int(round(temperature['maximum']))}°C")
        if "minimum" in temperature:
            temp_lines.append(f"最低温度: {int(round(temperature['minimum']))}°C")
        if "sensor" in temperature:
            temp_lines.append(f"传感器温度: {int(round(temperature['sensor']))}°C")
        if temp_lines:
            temp_block = "\n当前温度数据：\n" + "\n".join([f"- {line}" for line in temp_lines])
    
    # 处理重量数据
    weight_block = ""
    if weight:
        weight_lines = []
        if "weight" in weight:
            weight_unit = weight.get("unit", "g")
            weight_lines.append(f"当前重量: {weight['weight']}{weight_unit}")
        if "stable" in weight:
            stable_text = "稳定" if weight["stable"] else "波动中"
            weight_lines.append(f"重量状态: {stable_text}")
        if weight_lines:
            weight_block = "\n当前重量数据：\n" + "\n".join([f"- {line}" for line in weight_lines])

    custom_prompt_block = ""
    normalized_custom_prompt = (custom_prompt or "").strip()
    if normalized_custom_prompt:
        custom_prompt_block = f"\n个性化提示词：\n{normalized_custom_prompt}\n"

    # 只有存在观察记录时才注入视觉上下文，普通聊天减少 prompt token
    obs_section = ""
    if obs_texts:
        obs_section = f"\n最近视觉观察记录：\n{obs_block}"

    # 构建系统提示词
    system_prompt = CHAT_SYSTEM_PROMPT
    if custom_prompt_block:
        system_prompt += custom_prompt_block

    # 构建用户消息内容（上下文 + 问题）
    user_content = f"""{obs_section}
{temp_block}
{weight_block}

用户问题：
{user_text}

请直接回答："""

    return user_content, system_prompt
