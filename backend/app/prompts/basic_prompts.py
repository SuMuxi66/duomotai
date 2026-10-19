"""
基础聊天提示词模板
场景：关闭视觉分析状态下的普通LLM交互
特点：简洁高效，不包含视觉相关内容
"""

BASIC_CHAT_SYSTEM_PROMPT = """你是一个友好的AI助手。

规则：
- 回答简洁、准确、实用
- 使用中文交流
- 直接回答问题，不要有多余内容
"""


def build_basic_chat_prompt(user_text: str, custom_prompt: str = "") -> str:
    """
    构建基础聊天prompt（无视觉）
    
    Args:
        user_text: 用户输入的文本
        custom_prompt: 自定义提示词（可选）
        
    Returns:
        构建好的prompt字符串
    """
    custom_prompt_block = ""
    normalized_custom_prompt = (custom_prompt or "").strip()
    if normalized_custom_prompt:
        custom_prompt_block = f"\n个性化提示词：\n{normalized_custom_prompt}\n"
    
    return f"""{BASIC_CHAT_SYSTEM_PROMPT}
{custom_prompt_block}

用户问题：
{user_text}

请直接回答："""
