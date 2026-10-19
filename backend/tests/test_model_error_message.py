# -*- coding: utf-8 -*-
"""
模型错误用户侧文案回归（2026-09-05）：
DeepSeek 401 时原始 JSON 被整段送进 TTS 播报（"Your api key: 51cb is invalid
type authenticationerror param null…"）——认证/余额类错误须转为可行动的干净中文，
原始报错只进日志；其余状态码保持既有"真实报错摘要"行为不变。
"""
from app.providers.base import model_error_message


DEEPSEEK_401 = (
    '{"error":{"message":"Authentication Fails, Your api key: ****coRw is invalid",'
    '"type":"authentication_error","param":null,"code":"invalid_request_error"}}'
)


def test_auth_error_clean_message_with_key_suffix():
    """401：给干净中文提示，带掩码尾号便于用户对号，不得出现原始 JSON。"""
    msg = model_error_message("DeepSeek", 401, DEEPSEEK_401)
    assert "认证失败" in msg
    assert "coRw" in msg
    assert "{" not in msg and "}" not in msg
    assert "invalid_request_error" not in msg


def test_auth_error_without_masked_suffix():
    """ModelScope 401 无掩码尾号：尾号段整体省略，不留空括号。"""
    msg = model_error_message(
        "ModelScope", 401,
        '{"error":{"message":"Authentication failed, please make sure that a valid '
        'ModelScope token is supplied."}}',
    )
    assert "认证失败" in msg
    assert "（尾号" not in msg
    assert "{" not in msg


def test_forbidden_treated_as_auth_error():
    msg = model_error_message("DeepSeek", 403, '{"error":{"message":"Permission denied"}}')
    assert "认证失败" in msg
    assert "{" not in msg


def test_insufficient_balance_clean_message():
    msg = model_error_message("DeepSeek", 402, '{"error":{"message":"Insufficient Balance"}}')
    assert "余额不足" in msg
    assert "{" not in msg


def test_other_status_keeps_real_detail():
    """非认证/余额错误保持既有行为：真实报错摘要进用户侧（校验面板依赖它）。"""
    detail = '{"error":{"message":"The supported API model names are deepseek-v4-flash"}}'
    msg = model_error_message("DeepSeek", 400, detail)
    assert "deepseek-v4-flash" in msg
    assert "HTTP 400" in msg


def test_no_status_keeps_real_detail():
    msg = model_error_message("DeepSeek", detail="timeout")
    assert "timeout" in msg
