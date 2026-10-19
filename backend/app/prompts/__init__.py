"""
提示词管理模块
提供统一的系统提示词和场景检测
"""
from .cooking_prompts import (
    COOKING_SYSTEM_PROMPT,
    COOKING_INTENT_PROMPT,
    COOKING_INTENT_EXAMPLES,
    INGREDIENT_COLLECTION_PROMPT,
    INGREDIENT_COLLECTION_GUIDANCE,
    RECIPE_RECOMMENDATION_PROMPT,
    RECIPE_RECOMMENDATION_EXAMPLES,
    RECIPE_LOOKUP_PROMPT,
    STEP_GUIDE_PROMPT,
    STEP_GUIDE_STATUS,
    STEP_COMPLETION_PROMPT,
    COOKING_QA_PROMPT,
    COOKING_QA_KNOWLEDGE,
    COOKING_COMPLETION_PROMPT,
    COOKING_QUIT_PROMPT,
    COOKING_ERROR_PROMPT,
)

import os
from pathlib import Path

def load_prompt_from_file(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"读取提示词文件失败: {e}")
        return ""

PROMPTS_DIR = Path(__file__).parent.parent

# 统一系统提示词
SYSTEM_PROMPT = load_prompt_from_file(PROMPTS_DIR / "prompts" / "system.md")
CHAT_SYSTEM_PROMPT = SYSTEM_PROMPT


def detect_scene(text: str) -> str | None:
    """检测用户输入是否匹配预设场景，返回场景回复或 None"""
    return None


def build_chat_prompt(
    user_text: str,
    vision_observations: list,
    temperature: dict = None,
    weight: dict = None,
    custom_prompt: str = "",
    cooking_context: bool = False,
) -> tuple[str, str]:
    """构建统一聊天 prompt，注入视觉观察、温度、重量数据作为上下文"""
    obs_texts = []
    for obs in vision_observations[-8:]:
        if isinstance(obs, dict):
            obs_texts.append(obs.get("text", ""))
        elif isinstance(obs, str):
            obs_texts.append(obs)

    obs_block = "\n".join([f"- {o}" for o in obs_texts if o]) or "- （暂无视觉观察）"

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
            temp_block = (
                "\n当前温度数据（本厨房红外传感器实时读数，回答温度相关问题时必须基于以下实测值，"
                "不要回答无法获取实时数据）：\n" + "\n".join([f"- {line}" for line in temp_lines])
            )

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

    obs_section = ""
    if obs_texts:
        obs_section = f"\n最近视觉观察记录：\n{obs_block}"

    system_prompt = CHAT_SYSTEM_PROMPT
    if custom_prompt_block:
        system_prompt += custom_prompt_block

    # 做饭场景提示：用户提到做饭/菜谱/食材但未明确要求步骤引导时，
    # 引导 AI 收集信息并在合适时机主动询问是否开始步骤式引导做饭。
    if cooking_context:
        system_prompt += (
            "\n\n【场景提示-做饭】用户正在谈论做饭/菜谱/食材相关话题。"
            "在未交接给步骤式引导系统时，请自然地与用户交流，适当询问补充必要信息"
            "（想做什么菜、有什么食材、做几人份等）；当你认为信息足够（知道了具体菜品或足够的食材）时，"
            "在回答末尾自然地问一句：“需要我一步一步带你做这道菜吗？”或类似表述。"
            "一旦你决定输出【步骤式引导工具】的工具标记交接给系统，就只输出标记本身，"
            "不要再提问或补充任何文字（引导和提问由系统完成）。"
            "除非用户明确说“开始/怎么做/一步一步”，否则不要擅自开始步骤式引导，保持对话状态。"
        )
        # 步骤式引导工具协议：让 LLM 通过工具标记进入预设状态机，而非自由编步骤
        from .. import cooking_tools
        system_prompt += cooking_tools.SYSTEM_GUIDE_EXT

    user_content = f"""{obs_section}
{temp_block}
{weight_block}

用户问题：
{user_text}

请直接回答："""

    return user_content, system_prompt


__all__ = [
    "SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "COOKING_SYSTEM_PROMPT",
    "COOKING_INTENT_PROMPT",
    "COOKING_INTENT_EXAMPLES",
    "INGREDIENT_COLLECTION_PROMPT",
    "INGREDIENT_COLLECTION_GUIDANCE",
    "RECIPE_RECOMMENDATION_PROMPT",
    "RECIPE_RECOMMENDATION_EXAMPLES",
    "RECIPE_LOOKUP_PROMPT",
    "STEP_GUIDE_PROMPT",
    "STEP_GUIDE_STATUS",
    "STEP_COMPLETION_PROMPT",
    "COOKING_QA_PROMPT",
    "COOKING_QA_KNOWLEDGE",
    "COOKING_COMPLETION_PROMPT",
    "COOKING_QUIT_PROMPT",
    "COOKING_ERROR_PROMPT",
    "detect_scene",
    "build_chat_prompt",
]