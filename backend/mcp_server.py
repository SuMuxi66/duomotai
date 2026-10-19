# -*- coding: utf-8 -*-
"""厨房硬件 MCP server（stdio 传输）。

代理本机 8010 后端的硬件 HTTP 端点，对外暴露为 MCP 工具：
下料机 / 红外温度 / 称重。串口 COM10 由后端进程独占，本模块
**不**开串口、**不**import 后端 app，纯 HTTP 调用，避免端口争用。
"""
import os
import httpx
# 服务端 App 类双版本兼容：mcp 2.x 改名 MCPServer（mcp.server.mcpserver），
# 当前环境安装的是 1.9.4（同类 API 在 mcp.server.fastmcp 叫 FastMCP）——
# 两者 @mcp.tool() 装饰器与 mcp.run()（stdio 默认）用法一致，仅类名/路径不同。
try:
    from mcp.server.mcpserver import MCPServer as _MCPApp
except ImportError:
    from mcp.server.fastmcp import FastMCP as _MCPApp

BASE_URL = os.environ.get("GS_BACKEND_URL", "http://127.0.0.1:8010")

mcp = _MCPApp("gs-kitchen-hardware")


def _call(method: str, path: str, json_body=None, params: dict | None = None, timeout: float = 8.0) -> dict:
    """统一 HTTP 调用。出错不抛——MCP 工具需返回结构化错误给客户端。

    非 JSON 响应（例如后端返回纯文本错误）降级为 status_code+text，
    避免解析异常导致工具直接崩溃。
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=timeout) as client:
            resp = client.request(method, path, json=json_body, params=params)
    except httpx.HTTPError as e:
        # 8010 没起 / 端口被占 / 网络异常：给用户可读的提示
        return {"error": str(e), "hint": f"请确认后端已启动（{BASE_URL}）"}

    try:
        return resp.json()
    except ValueError:
        # 后端偶发返回纯文本错误页，截前 200 字便于排障
        return {"status_code": resp.status_code, "text": resp.text[:200]}


@mcp.tool()
def get_temperature() -> dict:
    """读取红外温度传感器实测数据（摄氏度等）。后端透传串口读数，无写入副作用。"""
    return _call("GET", "/temperature")


@mcp.tool()
def get_weight() -> dict:
    """读取称重传感器当前重量（克）及原始 AD 值等。仅读，不改变零点。"""
    return _call("GET", "/weight")


@mcp.tool()
def weight_tare() -> dict:
    """去皮：以当前读数为新零点。需在秤面为空时调用，否则后续读数会偏小。"""
    return _call("POST", "/weight/tare")


@mcp.tool()
def dispense_start(target_g: float = 0, pulse_seconds: float = 0) -> dict:
    """启动下料 / 打开调料出口。两种模式二选一：

    - **定时出料**：传 `pulse_seconds`（秒，0.5~30）——通电指定秒数后自动关闭。
      **放盐 / 放调料用这个**（例如 3 秒）。
    - **称重闭环**：传 `target_g`（克，0.5~100）——持续出料到称重达标后自动停止。

    ⚠️ 闭环模式**不要**紧接 `dispense_stop` 收尾（达标会自行断电）；
    定时模式则可以在本工具返回后紧接着调用 `dispense_stop` 做显式关闭
    （此时定时已走完，是幂等收尾，不会缩短出料时间）。

    后端内部可能等串口就绪最长 15s、定时模式还要等满通电时长，故超时分别设 25s / 40s。
    """
    if pulse_seconds and float(pulse_seconds) > 0:
        if not 0.5 <= float(pulse_seconds) <= 30:
            return {"error": "pulse_seconds 超出范围", "hint": "合法范围 0.5~30 秒"}
        return _call("POST", "/dispense/pulse", params={"seconds": float(pulse_seconds)}, timeout=40.0)
    if not 0.5 <= target_g <= 100:
        return {"error": "target_g 超出范围", "hint": "合法范围 0.5~100 克；放盐请用 pulse_seconds"}
    return _call("POST", "/dispense/start", json_body={"target_g": target_g}, timeout=25.0)


@mcp.tool()
def dispense_stop() -> dict:
    """关闭调料出口 / 紧急停机：立即断电。

    适用两种情况：
    ① 用户明确要求停止（"停下 / 别放了 / 够了 / 取消"）或出现危险、异常；
    ② **放盐/调料流程的显式收尾** —— `dispense_start(pulse_seconds=3)` 返回后紧接着调用一次，
       把出口明确关掉（幂等，不会缩短出料时间）。

    注意：闭环模式（`target_g`）达标会自行断电，不需要也不应用它试探性收尾；
    想确认下料是否结束请用 `dispense_status` 查。
    """
    return _call("POST", "/dispense/stop")


@mcp.tool()
def dispense_status() -> dict:
    """查询下料进行状态：是否 active、目标克数、剩余克数等。无副作用。"""
    return _call("GET", "/dispense/status")


# ===== 菜谱库（HowToCook 362 份） =====


@mcp.tool()
def search_recipes(ingredients: str) -> dict:
    """按食材搜索菜谱。ingredients 为逗号分隔的食材列表，如 "土豆,鸡蛋"；
    传空字符串则返回热门菜谱列表（最多 50 条）。返回菜名/分类/命中食材/难度。
    """
    return _call("GET", "/api/recipes/search", params={"ingredients": ingredients})


@mcp.tool()
def get_recipe(name: str) -> dict:
    """按菜名获取菜谱详情（食材用量与分步骤做法），name 如 "番茄炒蛋"。"""
    return _call("GET", f"/api/recipes/{name}")


# ===== 排气扇 =====


@mcp.tool()
def get_fan_status() -> dict:
    """查询排气扇当前开关状态。无副作用。"""
    return _call("GET", "/fan/status")


@mcp.tool()
def fan_on() -> dict:
    """打开排气扇。炒菜油烟大时可主动开启。"""
    return _call("POST", "/fan/on")


@mcp.tool()
def fan_off() -> dict:
    """关闭排气扇。"""
    return _call("POST", "/fan/off")


# ===== 语音模式（持续识别） =====


@mcp.tool()
def voice_continuous_start() -> dict:
    """开启前端持续识别模式：免唤醒词持续收音，进入通话式对话（说完即答，长静默自动挂断）。

    指令经后端 WS 下发给在线前端页面执行；没有任何页面在线时返回 warning，
    applied=false 表示未实际生效。
    """
    return _call("POST", "/voice/continuous/start", timeout=12.0)


@mcp.tool()
def voice_continuous_stop() -> dict:
    """关闭持续识别/挂断通话，前端恢复唤醒词模式。"""
    return _call("POST", "/voice/continuous/stop", timeout=12.0)


@mcp.tool()
def voice_continuous_status() -> dict:
    """查询当前语音模式：continuous 是否开启、模式名、最后指令来源、在线前端页面数。无副作用。"""
    return _call("GET", "/voice/continuous/status")


if __name__ == "__main__":
    mcp.run()
