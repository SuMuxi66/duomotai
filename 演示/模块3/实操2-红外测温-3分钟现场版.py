"""
第三模块 · 实操动作2【红外测温】—— 3 分钟现场精简版

现场策略：骨架提前打开，只敲 ★ 标记的 14 行（其余靠讲解+运行展示）。
取数口径：内部走统一取数接口 max_temp()，对外同一份数据通过 MCP 工具 get_temperature 暴露。
真实工程实现见 backend/app/temperature.py / backend/app/oil_watch.py / backend/mcp_server.py。
本文件可直接 `python 实操2-红外测温-3分钟现场版.py` 跑通（无硬件时走模拟温度源）。
"""
import asyncio
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ================= 温度源（真实环境由 MLX90640 守护线程写入） =================
TEMP = {"max": 0.0, "ts": 0.0}
_mock_temp = 150.0          # 演示用模拟锅温：持续升温，模拟油在加热


def feed_mock():
    """模拟硬件推送：真实环境这一路由串口守护线程填 TEMP"""
    global _mock_temp
    _mock_temp += 1.5
    TEMP["max"] = round(_mock_temp, 1)
    TEMP["ts"] = time.time()


async def mock_feed_loop():
    """演示加速：每 0.5 秒升一次温（真实硬件是设备自己的采样率）"""
    while True:
        feed_mock()
        await asyncio.sleep(0.5)


def max_temp() -> float:
    """统一取数接口：锅面最高温。

    对外即 MCP 工具 get_temperature 的返回值（mcp_server.py 透传 /temperature），
    眼镜端、第三方 Agent 调的是同一份数据，换硬件不改业务代码。
    """
    return TEMP["max"] or 0.0


# ================= MCP 对外暴露（口播展示，不现场敲） ===================
# 现场讲：内部取数走上面这个函数；对外的同一份数据注册成标准 MCP 工具
MCP_TOOL = '''@mcp.tool()
def get_temperature() -> dict:
    """读取红外温度传感器实测数据（摄氏度）"""
    return _call("GET", "/temperature")     # 透传后端 /temperature 端点
'''


async def say(text: str):
    """语音播报（真实环境走 send_cooking_text_with_tts）"""
    print(f"  [播报] {text}")


# ================= ★1 帧同步：滑窗找帧头，切帧，余料留下轮 =============
# 现场讲：串口是字节流，粘包断包靠"找帧头 + 定长切帧"解决
def next_frame(buf: bytes):
    idx = buf.find(b"\x5A\x5A")                 # ① 找帧头
    if idx == -1 or len(buf) < idx + 1544:      # ② 不够一帧就等下一包
        return None, buf[-2:] if idx == -1 else buf
    return buf[idx:idx + 1544], buf[idx + 1544:]  # ③ 切一帧，余料留给下轮


# ================= ★2 解析 768 像素 → 锅面最高温 =======================
# 现场讲：一帧 1544 字节 = 4 帧头 + 768 像素×2 + 2 传感器自身温度
def parse(frame: bytes):
    temps = [(frame[4 + i * 2 + 1] << 8 | frame[4 + i * 2]) / 100 for i in range(768)]
    return max(temps)                            # 锅面最热点 = 我们要的油温


# ================= ★3 问句判定：查询拦截，监测请求放行给模型 ===========
# 现场讲：这一步在送大模型之前，问句直接拿实测值答，零延迟
def looks_like_oil_temp_query(t: str) -> bool:
    if any(k in t for k in ("盯", "提醒", "监测", "帮我看")):   # ① 监测请求不拦
        return False                                           #    → 交给模型出工具标记
    return ("油温" in t or "锅温" in t) and any(                # ② 查询句：油温 + 疑问词
        k in t for k in ("多少", "到了", "够", "热", "几成"))


# ================= ★4 后台监测：1 秒采样 + 连续 3 次达标 ================
# 现场讲：独立协程，跟对话主流程分开；连续 3 次是防油温抖动误报
async def watch(state: dict, target: int):
    hit = 0
    while state.get("oil_watch") and not state.get("interrupted"):
        await asyncio.sleep(1)                              # ① 每 1 秒读一次
        t = max_temp()                                      # ② 统一取数接口（对外即 MCP 工具）
        hit = hit + 1 if t >= target else 0                 # ③ 连续达标才计数
        if hit >= 3:                                        # ④ 连续 3 次 → 真到了
            await say(f"油温到了（约{t:.0f}°C），可以下食材了！")
            state["oil_watch"] = None
            return


# ================= 演示流程（3 分钟节奏） ==============================
async def main():
    print("=" * 56)
    print("【红外测温】硬件采集 → 问它答 → 它盯它报")
    print("=" * 56)

    print("\n[1/3] 硬件采集：滑窗帧同步 + 768 像素解析")
    frame = (                                   # 构造一帧 1544 字节的模拟数据
        bytes([0x5A, 0x5A, 0x06, 0x08])        # 帧头 4 字节
        + b"".join(int((150 + (i % 7) * 4) * 100).to_bytes(2, "little") for i in range(768))  # 768 像素（小端）
        + int(3200).to_bytes(2, "big")         # 传感器自身温度
        + b"\x00\x00"                          # 帧尾
    )
    buf = b"\x00\x01" + frame + b"\x5A"        # 故意加脏数据，模拟粘包
    f, rest = next_frame(buf)
    print(f"  帧同步: 切出 {len(f)} 字节，余料 {len(rest)} 字节 → 粘包已对齐 ✓")
    print(f"  解析: 768 像素 → 锅面最高温 {parse(f):.1f}°C ✓")

    print("\n[2/3] 第一条线：你问它答（确定性快答，不走大模型）")
    for q in ("油温到了吗", "帮我盯着油温"):
        flag = looks_like_oil_temp_query(q)
        print(f"  「{q}」→ 拦截={flag} {'(直接读传感器)' if flag else '(交给模型出工具标记)'}")
    print("  对外同一份数据注册为标准 MCP 工具（backend/mcp_server.py）：")
    print("    " + "\n    ".join(MCP_TOOL.strip().splitlines()))
    feed_mock()
    t = max_temp()
    print(f"  统一取数接口 max_temp() → 锅面 {t:.0f}°C →", end=" ")
    await say(f"当前锅面最高温度约 {t:.0f}°C，五六成热约 150-180°C 适合下主料，还差一点。")

    print("\n[3/3] 第二条线：它盯它报（后台异步监测，目标 160°C）")
    state = {"oil_watch": {"target": 160}, "interrupted": False}
    print("  用户: 帮我盯着油温，到了提醒我下食材")
    await say("好的，正在监测油温，达到 160°C 我会主动提醒你下食材。")
    feeder = asyncio.create_task(mock_feed_loop())   # 模拟硬件持续送温度
    await watch(state, 160)                          # 后台监测独立跑，不占对话
    feeder.cancel()
    print("  监测任务已自动结束，未占用对话主流程 ✓")

    print("\n✅ 收口：一个数据源（红外锅面最高温），两个方向——问有答、到有报，全程免触。")


if __name__ == "__main__":
    asyncio.run(main())
