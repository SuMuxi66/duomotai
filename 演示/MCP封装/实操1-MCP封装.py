# -*- coding: utf-8 -*-
r"""
【实操1：20 行封装 MCP Server】—— 把厨房硬件开放给一切 AI 客户端

对应真实源码：backend/mcp_server.py（生产版 11 个工具 / 119 行，已 stdio E2E 验收）
本文件是同款骨架的最小可运行版：正文 17 行有效代码，现场逐行敲。

为什么封装：
  硬件能力早就在（红外温度 / 称重 / 下料机 / 风扇），但只被我们自己的
  语音前端用私有 WS 协议调用。MCP 是把硬件开放给 Trae / Cursor / Claude
  等一切 AI 客户端的标准协议——写一次，所有客户端都能用。

三个骨架动作（整份代码就是这三步）：
  1. MCPServer("名字")     —— 建一个 server 实例
  2. @mcp.tool() 装饰函数  —— 函数变工具，docstring 就是 AI 的工具说明书
  3. mcp.run()             —— stdio 启动，客户端自动拉起子进程

架构重点：MCP server 不碰串口——COM10 由 8010 后端进程独占
（并发开串口会 PermissionError），每个工具就是一行 httpx 调用，
纯转发后端现成端点，不开串口、不 import 后端 app。
运行前提：8010 后端已启动（curl http://127.0.0.1:8010/health）。

怎么跑：backend\.venv\Scripts\python.exe 演示\MCP封装\实操1-MCP封装.py
（日常由 MCP 客户端自动拉起，mcpserver.json 已配好）
"""
import httpx
from mcp.server.mcpserver import MCPServer   # mcp 2.x：FastMCP 已改名 MCPServer

mcp = MCPServer("gs-kitchen-hardware")       # 骨架①：实例化 server

@mcp.tool()                                  # 骨架②：函数变工具
def get_temperature() -> dict:
    """读取红外测温实时数据（摄氏度）"""      # docstring = AI 看到的工具说明
    return httpx.get("http://127.0.0.1:8010/temperature", timeout=8).json()

@mcp.tool()
def dispense_start(target_g: float) -> dict:
    """自动下料 target_g 克，称重达标自动断电"""
    return httpx.post("http://127.0.0.1:8010/dispense/start",
                      json={"target_g": target_g}, timeout=25).json()

@mcp.tool()
def fan_on() -> dict:
    """打开排气扇（写操作，风扇会真实转动）"""
    return httpx.post("http://127.0.0.1:8010/fan/on", timeout=8).json()

mcp.run()                                    # 骨架③：stdio 启动，客户端自动拉起
