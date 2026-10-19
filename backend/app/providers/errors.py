"""Provider 层统一错误类型。

历史问题：各家 provider 把 HTTP 非 200 / 网络异常包成 `（…）` 字符串 yield 回来，
该字符串会被 llm_handler 当作正常生成内容写进聊天流并送进 TTS 播报
（"原始 JSON 错误页被念出来"）。统一改为 provider 层 raise 异常，
由消费边界（llm_handler / /models/validate / 视觉调用方）决定上屏与播报策略。
"""

from __future__ import annotations

from .base import model_error_message


class ModelProviderError(Exception):
    """模型调用失败（HTTP 非 200 / 网络异常重试用尽）。

    - user_message: 给用户看的干净中文文案（复用 model_error_message 规则生成，
      认证/余额类错误已转为可行动提示）。消费边界应把它上屏到聊天流，但不送 TTS。
    - status / detail: 原始状态码与错误体摘要，完整细节仍进后端日志。
    """

    def __init__(
        self,
        provider: str,
        status: int | None = None,
        detail: str = "",
        user_message: str | None = None,
    ):
        self.provider = provider
        self.status = status
        self.detail = " ".join((detail or "").split())[:2000]
        self.user_message = user_message or model_error_message(provider, status, self.detail)
        super().__init__(self.user_message)
