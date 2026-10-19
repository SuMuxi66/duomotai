# -*- coding: utf-8 -*-
"""pytest 共享环境：确保 backend 目录在 sys.path 前端，使 `from app import ...` 可导入"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 测试隔离（2026-09-21，防烧红线）：llm_tools 的 dispense_start/stop 默认经真实 MCP
# 链路执行（mcp_client 常驻 stdio 子进程 → mcp_server HTTP 回环 /dispense/* → 真开阀）。
# 测试必须在任何 app 模块 import 前旁路 MCP 回进程内直调——test_llm_tools 的确认门
# 测试 mock 的正是直调层（app.dispense.*）；不旁路则 mock 失效、**测试真实驱动电磁阀**
# （2026-09-21 事故：全量 pytest 期间两测试会话并发下料，前端观察到阀疯狂开关）。
os.environ.setdefault("MCP_DISPENSE_ENABLED", "0")