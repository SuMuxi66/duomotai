import os
import sys
from datetime import datetime

LOG_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")

def safe_print(message: str):
    """安全打印消息，处理 Unicode 字符"""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        # 如果遇到编码问题，替换无法显示的字符
        safe_message = message.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        print(safe_message, flush=True)

def get_logger(log_type: str):
    log_dir = os.path.join(LOG_BASE, log_type)
    os.makedirs(log_dir, exist_ok=True)
    safe_print(f"[LOGGER] Log directory: {log_dir}")
    
    def log(message: str):
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(log_dir, f"{today}.md")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"[{timestamp}] {message}\n"
        safe_print(f"[LOG] {content.strip()}")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(content)
            safe_print(f"[LOGGER] Log written to {log_file}")
        except Exception as e:
            safe_print(f"[LOG ERROR] {e}")
    
    return log

backend_log = get_logger("backend")
frontend_log = get_logger("frontend")
