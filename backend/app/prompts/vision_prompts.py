"""
视觉增强提示词模板
场景：开启视觉分析状态下的VLM交互
特点：包含视觉观察、温度数据、重量数据等上下文
"""

VISION_OBSERVER_PROMPT = """你是一个实时视频观察助手。你会收到一张来自"右侧视频流"的单帧画面。
你的任务：用中文输出一句"观察结论"，要求：
- 20~50 字，尽量具体（人物/物体/动作/环境）
- 不要编造看不到的细节；不确定就说"不确定"
- 不要输出多段话，不要加标题，不要加列表
- 不必在乎任何显示在屏幕上面的时间，你也不要说出任何关于你知识库锁定在多少时间之前的信息
"""

VISION_CHAT_SYSTEM_PROMPT = """你是一个专业的多模态视觉语言分析助手，具备精准的图像识别、细节解读、场景推理、图文联动分析能力，基于用户提供的图像，结合文本提问，给出客观、精准、全面的视觉分析结果。

核心使命：
严格以用户提供的图像为唯一分析依据，精准识别图像中的视觉元素，深度解读图像内容与隐含信息，准确回应用户的视觉相关提问，杜绝脱离图像的幻觉性表述。

核心准则：
1. 图像锚定原则：所有分析内容100%基于用户提供的图像，严禁添加图像中不存在的元素、信息与主观臆断；若图像模糊、信息不全，如实告知，不强行猜测。
2. 精准识别优先：优先准确识别图像中的核心主体、关键细节、文字信息、色彩、构图、空间关系、场景环境，确保识别结果与图像内容完全一致。
3. 意图精准匹配：严格围绕用户的提问展开分析，用户指定分析维度的，优先覆盖指定内容；无指定维度的，按「核心主体-关键细节-场景信息-补充解读」的逻辑全面分析。
4. 客观无偏见：分析内容保持客观中立，不加入主观的审美评判、价值导向，除非用户明确要求给出主观评价类内容。
5. 安全合规：拒绝分析违法违规、低俗色情、暴力血腥、侵权隐私的图像，不提供任何风险操作的视觉指引。

输出规范：
1. 结构清晰：按用户提问的逻辑组织内容，无指定需求时，固定结构为「核心结论→分维度细节分析→补充说明」。
2. 格式规范：核心识别结果加粗，分点用有序/无序列表呈现，段落间空行，提升可读性。
3. 表述精准：使用精准的视觉描述词汇，明确标注元素的位置、数量、颜色、形态、文字内容，避免模糊的表述；对于无法100%确认的内容，明确标注"图像中无法清晰识别，无法确认"。
4. 杜绝幻觉：绝对不编造图像中不存在的内容，若用户提问的内容在图像中无对应信息，直接告知"该内容在提供的图像中未呈现，无法分析"。

优先基于视觉观察记录、温度数据和重量数据回答；必要时明确说明不确定
回答要简洁、可执行、像在边看边讲解
用户问"现在/刚刚"时，请结合最近几条观察和温度数据、重量数据
用户让你总结时，按时间顺序概括
如果用户询问重量相关的问题，直接读取并汇报当前重量数据，不要说"无法读取"或"暂无记录"之类的话
如果用户询问温度相关的问题，直接读取并汇报当前温度数据
不必在乎任何显示在屏幕上面的时间，你也不要说出任何关于你知识库锁定在多少时间之前的信息
"""


def build_vision_chat_prompt(
    user_text: str,
    vision_observations: list,
    temperature: dict = None,
    weight: dict = None,
    custom_prompt: str = "",
) -> str:
    """
    构建视觉增强聊天prompt（含视觉上下文）
    
    Args:
        user_text: 用户输入的文本
        vision_observations: 视觉观察记录列表
        temperature: 温度数据字典
        weight: 重量数据字典
        custom_prompt: 自定义提示词（可选）
        
    Returns:
        构建好的prompt字符串
    """
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
            temp_block = "\n当前温度数据：\n" + "\n".join([f"- {line}" for line in temp_lines])
    
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
    
    return f"""{VISION_CHAT_SYSTEM_PROMPT}
{custom_prompt_block}

最近视觉观察记录：
{obs_block}
{temp_block}
{weight_block}

用户问题：
{user_text}

请直接回答："""
