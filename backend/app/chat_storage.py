import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

class ChatMessage(BaseModel):
    role: str
    text: str
    timestamp: Optional[str] = None
    requestId: Optional[str] = None
    reasoning: Optional[str] = None
    reasoningExpanded: Optional[bool] = None
    reasoningDurationMs: Optional[int] = None

class ChatSession(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    title: Optional[str] = None
    messages: List[ChatMessage]

STORAGE_DIR = Path(__file__).parent.parent.parent / "data" / "chat_history"

def ensure_storage_dir():
    if not STORAGE_DIR.exists():
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)

def get_session_file(session_id: str = "default") -> Path:
    ensure_storage_dir()
    return STORAGE_DIR / f"{session_id}.json"

def _normalize_title(title: Optional[str]) -> Optional[str]:
    if title is None:
        return None
    normalized = title.strip()
    return normalized or None

def _build_preview(messages: List[dict]) -> str:
    for msg in reversed(messages):
        text = (msg.get("text") or "").strip().replace("\n", " ")
        if text:
            return text[:80]
    return ""

def save_messages(messages: List[dict], session_id: str = "default", title: Optional[str] = None) -> bool:
    try:
        file_path = get_session_file(session_id)
        now = datetime.now().isoformat()
        
        chat_messages = []
        for msg in messages:
            text = msg.get("text", "")
            if text and isinstance(text, str):
                text = text.replace('\x00', '').replace('\r\n', '\n')
            # 保留消息原有的时间戳，如果没有则使用当前时间
            timestamp = msg.get("timestamp")
            if timestamp and isinstance(timestamp, (str, int, float)):
                # 如果是数字时间戳，转换为ISO格式字符串
                if isinstance(timestamp, (int, float)):
                    timestamp = datetime.fromtimestamp(timestamp / 1000).isoformat()
            else:
                timestamp = now
            chat_messages.append(ChatMessage(
                role=msg.get("role", "unknown"),
                text=text or "",
                timestamp=timestamp,
                requestId=msg.get("requestId"),
                reasoning=msg.get("reasoning"),
                reasoningExpanded=msg.get("reasoningExpanded"),
                reasoningDurationMs=msg.get("reasoningDurationMs"),
            ))
        
        session = ChatSession(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            title=_normalize_title(title),
            messages=chat_messages
        )
        
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    session.created_at = existing.get("created_at", now)
                    if session.title is None:
                        session.title = _normalize_title(existing.get("title"))
            except (json.JSONDecodeError, Exception):
                pass
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session.model_dump(), f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        print(f"[ChatStorage] 保存聊天记录失败: {e}")
        import traceback
        print(f"[ChatStorage] 错误详情: {traceback.format_exc()}")
        return False

def load_messages(session_id: str = "default") -> List[dict]:
    try:
        file_path = get_session_file(session_id)
        if not file_path.exists():
            return []
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        messages = []
        for msg in data.get("messages", []):
            message = {
                "role": msg.get("role", "unknown"),
                "text": msg.get("text", "")
            }
            # 保留时间戳信息
            if "timestamp" in msg:
                message["timestamp"] = msg["timestamp"]
            # 保留推理过程相关字段
            if msg.get("requestId"):
                message["requestId"] = msg["requestId"]
            if msg.get("reasoning"):
                message["reasoning"] = msg["reasoning"]
            if "reasoningExpanded" in msg:
                message["reasoningExpanded"] = msg["reasoningExpanded"]
            if "reasoningDurationMs" in msg:
                message["reasoningDurationMs"] = msg["reasoningDurationMs"]
            messages.append(message)
        
        return messages
    except Exception as e:
        print(f"[ChatStorage] 加载聊天记录失败: {e}")
        return []

def clear_messages(session_id: str = "default") -> bool:
    try:
        file_path = get_session_file(session_id)
        if file_path.exists():
            file_path.unlink()
        return True
    except Exception as e:
        print(f"[ChatStorage] 清除聊天记录失败: {e}")
        return False

def rename_session(session_id: str, title: Optional[str]) -> bool:
    try:
        file_path = get_session_file(session_id)
        if not file_path.exists():
            return False

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["title"] = _normalize_title(title)
        data["updated_at"] = datetime.now().isoformat()

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return True
    except Exception as e:
        print(f"[ChatStorage] 重命名会话失败: {e}")
        return False

def list_sessions(query: Optional[str] = None) -> List[dict]:
    try:
        ensure_storage_dir()
        sessions = []
        normalized_query = (query or "").strip().lower()
        for file in STORAGE_DIR.glob("*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session_id = data.get("session_id", file.stem)
                title = _normalize_title(data.get("title"))
                messages = data.get("messages", [])
                searchable_parts = [session_id]
                if title:
                    searchable_parts.append(title)
                matched_preview = ""
                for msg in messages:
                    text = (msg.get("text") or "").strip()
                    if text:
                        searchable_parts.append(text)
                        if normalized_query and not matched_preview and normalized_query in text.lower():
                            matched_preview = text.replace("\n", " ")[:80]
                searchable_text = "\n".join(searchable_parts).lower()
                if normalized_query and normalized_query not in searchable_text:
                    continue
                sessions.append({
                    "session_id": session_id,
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "title": title,
                    "preview": matched_preview or _build_preview(messages),
                    "message_count": len(messages)
                })
            except:
                pass
        return sessions
    except Exception as e:
        print(f"[ChatStorage] 列出会话失败: {e}")
        return []
