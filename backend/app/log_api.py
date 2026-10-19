import os
from datetime import datetime
from fastapi import APIRouter, Request

router = APIRouter()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

def get_logger(log_type: str):
    log_dir = os.path.join(LOG_DIR, log_type)
    os.makedirs(log_dir, exist_ok=True)
    
    class Logger:
        def __init__(self):
            self.log_dir = log_dir
        
        def _get_log_file(self) -> str:
            today = datetime.now().strftime("%Y-%m-%d")
            return os.path.join(self.log_dir, f"{today}.md")
        
        def log(self, message: str):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            content = f"[{timestamp}] {message}\n"
            try:
                with open(self._get_log_file(), "a", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                print(f"日志写入失败: {e}")
    
    return Logger()

@router.post("/api/log")
async def save_log(request: Request):
    data = await request.json()
    log_type = data.get("log_type", "frontend")
    message = data.get("message", "")
    
    logger = get_logger(log_type)
    logger.log(message)
    
    return {"status": "ok"}
