# 厨房硬件 MCP Server 使用说明

把项目的**下料机、红外温度、称重、排气扇、菜谱库**封装成 MCP（Model Context Protocol）工具，任何支持 MCP 的客户端（Trae / Cursor / Claude Desktop / ZCode 等）挂载后，AI 就能直接读传感器、控制下料机与风扇、查菜谱。

- Server 名称：`gs-kitchen-hardware`
- 传输方式：stdio（客户端自动拉起子进程，无需手动运行）
- 服务定义：[backend/mcp_server.py](backend/mcp_server.py)
- 客户端配置：[mcpserver.json](mcpserver.json)

---

## 一、前提条件

1. **后端已启动**（MCP server 是后端的 HTTP 代理，自己不开串口）：

   ```
   http://127.0.0.1:8010/health  →  {"ok":true}
   ```

   后端启动方式见项目 AGENTS.md（uvicorn `app.main:app`，端口 8010）。

2. **依赖已安装**（本机 backend venv 已装好，换机器时执行）：

   ```
   cd backend
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   其中与本 server 相关的是 `mcp>=2.1,<3` 与 `httpx`。注意：安装 mcp 时若 pip 自动升级了 starlette，必须钉回 `starlette==0.38.6` + `sse-starlette==3.0.1`（fastapi 0.115.0 要求 starlette<0.39，否则后端下次重启 import 失败），装完跑 `pip check` 确认。

---

## 二、挂载方法

把 [mcpserver.json](mcpserver.json) 里的 `mcpServers` 块合并进你所用客户端的 MCP 配置：

```json
{
  "mcpServers": {
    "gs-kitchen-hardware": {
      "command": "E:\\Trae project\\gs\\backend\\.venv\\Scripts\\python.exe",
      "args": ["E:\\Trae project\\gs\\backend\\mcp_server.py"],
      "env": { "GS_BACKEND_URL": "http://127.0.0.1:8010" }
    }
  }
}
```

- **Trae / Cursor**：设置 → MCP → 编辑 JSON（或 `~/.trae/mcp.json`、`mcp.json`），粘贴上述块
- **Claude Desktop**：`claude_desktop_config.json` → `mcpServers` 合并
- 路径如项目搬过家，改成实际的绝对路径（JSON 里 Windows 路径用双反斜杠 `\\`）
- 配置保存后重启/刷新客户端，看到 `gs-kitchen-hardware` 与 11 个工具即挂载成功

---

## 三、工具清单（11 个）

### 传感器（只读）

| 工具 | 功能 |
|---|---|
| `get_temperature` | 红外温度传感器实测数据（摄氏度） |
| `get_weight` | 称重传感器当前重量（克）及原始读数 |

### 下料机

| 工具 | 功能 | 说明 |
|---|---|---|
| `dispense_start` | 自动下料 target_g 克 | **写操作**。范围 0.5~100 克；以"当前重量+克数"为目标，称重达标自动断电，无需盯守 |
| `dispense_stop` | 停止下料 | **写操作**。手动/紧急停止 |
| `dispense_status` | 查询下料状态 | 进行中/目标/剩余克数 |
| `weight_tare` | 去皮 | **写操作**。以当前读数为新零点，秤面为空时才可调用 |

### 排气扇

| 工具 | 功能 | 说明 |
|---|---|---|
| `get_fan_status` | 查询风扇状态 | 只读 |
| `fan_on` / `fan_off` | 开 / 关排气扇 | **写操作** |

### 菜谱库（HowToCook 362 份）

| 工具 | 功能 |
|---|---|
| `search_recipes` | 按食材搜索菜谱，ingredients 用逗号分隔（如 `"土豆,鸡蛋"`），传空字符串返回热门 50 条 |
| `get_recipe` | 按菜名取详情（食材用量 + 分步做法），如 `"番茄炒蛋"` |

---

## 四、配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `GS_BACKEND_URL` | `http://127.0.0.1:8010` | 后端地址。后端不在本机时改此值（见 mcpserver.json 的 env 块） |

---

## 五、注意事项与故障排查

1. **写工具会驱动真实硬件**：`dispense_start` 会让下料机实际出料，`fan_on` 会让风扇实际转动——挂给 AI 后下指令前留意现场。
2. **串口独占**：下料机/称重板卡接在 COM10，由后端进程独占。MCP server 不直接碰串口，全部请求转发后端；所以**必须先起后端再用 MCP**，否则工具返回 `{"error": ..., "hint": "请确认后端已启动（http://127.0.0.1:8010）"}` 样式的结构化错误。
3. **下料无反应**：`dispense_start` 返回"串口未就绪"= 后端连不上板卡，检查 COM10 是否被其他程序占用（比如另一个后端实例、串口调试工具）。
4. **`dispense_start` 响应较慢（最长约 25 秒）属正常**：后端内部会等串口就绪（最长 15 秒）再开始下料。
5. **手动冒烟测试**（不挂客户端也能验证）：

   ```
   cd backend
   .venv\Scripts\python.exe mcp_server.py
   ```

   在 stdin 输入一行 MCP JSON-RPC（如 initialize 请求）回车，stdout 有响应即 server 本体正常；日常使用还是交给客户端管理。
