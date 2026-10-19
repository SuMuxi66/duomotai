"""
全局倒计时 WebSocket 处理器
支持从任何场景创建、管理和取消倒计时
"""

from typing import Any, Optional
from fastapi import WebSocket
import asyncio

from ..session import SessionState
from ..timer_manager import timer_manager, create_countdown, cancel_countdown, get_all_countdowns
from ..utils.logger import backend_log
from ..ws import ws_send


async def handle_timer_message(
    action: str,
    payload: dict,
    ws: WebSocket,
    state: SessionState,
    runtime: Any,
    request_id: Optional[str] = None,
) -> None:
    """
    处理倒计时相关的 WebSocket 消息
    
    Args:
        action: 动作类型
        payload: 消息数据
        ws: WebSocket 连接
        state: 会话状态
        runtime: 应用运行时
        request_id: 请求ID
    """
    if action == "create_timer":
        await handle_create_timer(ws, state, payload, request_id)
    elif action == "cancel_timer":
        await handle_cancel_timer(ws, state, payload)
    elif action == "cancel_all_timers":
        await handle_cancel_all_timers(ws, state, payload)
    elif action == "get_timers":
        await handle_get_timers(ws, state)
    else:
        backend_log(f"[Timer] 未知的 action: {action}")


async def handle_create_timer(
    ws: WebSocket,
    state: SessionState,
    payload: dict,
    request_id: Optional[str] = None,
) -> None:
    """
    创建新的倒计时
    
    Payload:
        - description: 倒计时描述
        - seconds: 倒计时秒数
        - category: 类别 (可选, 默认 "general")
        - metadata: 额外元数据 (可选)
    """
    try:
        description = payload.get("description", "倒计时")
        seconds = int(payload.get("seconds", 60))
        category = payload.get("category", "general")
        metadata = payload.get("metadata", {})
        
        if seconds <= 0:
            await ws_send(ws, "timer_error", {
                "error": "倒计时时间必须大于0秒",
                "request_id": request_id,
            })
            return
        
        # 创建倒计时（挂到本会话名下，避免跨会话串台）
        timer_id = create_countdown(
            description=description,
            seconds=seconds,
            category=category,
            metadata=metadata,
            on_complete=lambda timer: asyncio.create_task(
                notify_timer_complete(ws, timer, state)
            ),
            session_id=getattr(state, "session_id", "default"),
        )
        
        # 获取创建的倒计时信息
        timer = timer_manager.get_timer(timer_id)
        
        # 发送成功响应
        await ws_send(ws, "timer_created", {
            "timer": timer.to_dict() if timer else None,
            "request_id": request_id,
        })
        
        # 广播倒计时列表更新
        await broadcast_timer_update(ws, state)
        
        backend_log(f"[Timer] 创建成功: {timer_id} - {description} ({seconds}s)")
        
    except Exception as e:
        backend_log(f"[Timer] 创建失败: {e}")
        await ws_send(ws, "timer_error", {
            "error": f"创建倒计时失败: {str(e)}",
            "request_id": request_id,
        })


async def handle_cancel_timer(
    ws: WebSocket,
    state: SessionState,
    payload: dict,
) -> None:
    """
    取消指定倒计时
    
    Payload:
        - timer_id: 要取消的倒计时ID
    """
    timer_id = payload.get("timer_id")
    
    if not timer_id:
        await ws_send(ws, "timer_error", {
            "error": "缺少 timer_id 参数",
        })
        return
    
    success = cancel_countdown(timer_id)
    
    if success:
        await ws_send(ws, "timer_cancelled", {
            "timer_id": timer_id,
        })
        await broadcast_timer_update(ws, state)
        backend_log(f"[Timer] 取消成功: {timer_id}")
    else:
        await ws_send(ws, "timer_error", {
            "error": f"倒计时不存在或已过期: {timer_id}",
        })


async def handle_cancel_all_timers(
    ws: WebSocket,
    state: SessionState,
    payload: dict,
) -> None:
    """
    取消所有倒计时，或指定类别的倒计时
    
    Payload:
        - category: 类别筛选 (可选, 为 null 则取消所有)
    """
    category = payload.get("category")

    # 只取消本会话的定时器，避免清掉其他会话的倒计时
    count = timer_manager.cancel_all_timers(
        category,
        session_id=getattr(state, "session_id", "default"),
    )
    
    await ws_send(ws, "timers_cancelled", {
        "count": count,
        "category": category,
    })
    
    await broadcast_timer_update(ws, state)
    backend_log(f"[Timer] 取消 {count} 个倒计时 (category={category})")


async def handle_get_timers(
    ws: WebSocket,
    state: SessionState,
) -> None:
    """获取当前会话的所有活跃倒计时"""
    timers = get_all_countdowns(session_id=getattr(state, "session_id", "default"))

    await ws_send(ws, "timers_list", {
        "timers": timers,
        "count": len(timers),
    })


async def broadcast_timer_update(ws: WebSocket, state: SessionState) -> None:
    """广播当前会话的倒计时列表更新"""
    timers = get_all_countdowns(session_id=getattr(state, "session_id", "default"))

    await ws_send(ws, "timers_updated", {
        "timers": timers,
        "count": len(timers),
    })


async def notify_timer_complete(
    ws: WebSocket,
    timer: Any,
    state: SessionState,
) -> None:
    """
    通知客户端倒计时已完成
    
    注意：这个函数在倒计时完成时由 TimerManager 调用
    """
    # 连接已断开：直接放弃，避免空转发送与日志刷屏
    # 注意：WebSocketState 在 starlette.websockets 里，不在 fastapi 顶层（fastapi 只 re-export WebSocket）
    from starlette.websockets import WebSocketState

    if getattr(ws, "client_state", None) != WebSocketState.CONNECTED:
        backend_log(f"[Timer] 完成通知跳过：连接已断开（timer={getattr(timer, 'id', '?')}）")
        return

    try:
        # 发送倒计时完成消息
        await ws_send(ws, "timer_completed", {
            "timer": timer.to_dict(),
        })
        
        # 广播更新后的列表
        await broadcast_timer_update(ws, state)
        
        backend_log(f"[Timer] 完成通知已发送: {timer.id}")
        
        # 播放提示音
        await ws_send(ws, "timer_beep", {
            "message": timer.description if hasattr(timer, 'description') else "倒计时结束",
            "timer_id": timer.id,
        })
        
    except Exception as e:
        backend_log(f"[Timer] 发送完成通知失败: {e}")


# 注册到全局 timer_manager 的广播回调
def register_timer_broadcast_handler(ws: WebSocket, state: SessionState):
    """
    注册倒计时广播处理器
    用于接收所有倒计时事件（创建、完成、取消）
    """
    async def broadcast_handler(event_type: str, data: dict):
        try:
            await ws_send(ws, f"timer_{event_type}", data)
        except Exception as e:
            backend_log(f"[Timer] 广播失败: {e}")
    
    timer_manager.register_broadcast_callback(broadcast_handler)
    return broadcast_handler


def unregister_timer_broadcast_handler(handler):
    """注销倒计时广播处理器"""
    timer_manager.unregister_broadcast_callback(handler)


# 便捷函数：从自然语言解析倒计时请求
def parse_timer_request(text: str) -> Optional[dict]:
    """
    从用户输入中解析倒计时请求
    
    支持的格式：
    - "倒计时5分钟"
    - "设置一个3分钟的计时器"
    - "2分钟后提醒我"
    - "计时10分钟"
    
    Returns:
        {"seconds": int, "description": str} 或 None
    """
    import re
    
    # 匹配模式
    patterns = [
        # 倒计时X分钟/秒
        r"倒计时\s*(\d+)\s*([分钟分秒])",
        r"计时\s*(\d+)\s*([分钟分秒])",
        r"设置\s*(?:一个)?\s*(\d+)\s*([分钟分秒])\s*(?:的)?\s*计时器",
        r"(\d+)\s*([分钟分秒])\s*后?\s*(?:提醒|叫我|通知)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            
            # 转换为秒
            if unit in ["分钟", "分"]:
                seconds = num * 60
            else:
                seconds = num
            
            # 提取描述（剩余文本）
            description = text.replace(match.group(0), "").strip()
            if not description:
                description = f"{num}{unit}倒计时"
            
            return {
                "seconds": seconds,
                "description": description,
            }
    
    return None
