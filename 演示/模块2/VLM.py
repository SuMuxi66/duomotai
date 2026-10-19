"""
【实操动作2：VLM大模型流式智能分析】实操演示

对标《智能烹饪项目全环节技能实操讲解文档》第二模块·选手3实操手2：

运行实操2 时自动调用实操动作1 的拍摄逻辑（capture_snapshot，已封装为共享函数），
实拍当前摄像头画面后，真实调用 ModelScope Qwen 视觉大模型进行流式智能分析，
两个实操串联成一个完整演示流程：

1. 自动调用实操1 的拍摄函数：摄像头弹窗 → 3 秒倒计时自动截图 → 5 秒倒计时自动关闭
2. 截图自动保存为 _snapshot.jpg，并直接用于分析（无需重新读取）
3. 截图 Base64 编码，发送给 ModelScope VLM 模型（提示词：请分析一下当前场景内容）
4. 流式输出思考过程 + 最终分析结果（终端实时打印）
5. 弹窗展示完整分析结果（需人为关闭）
6. 异常容错：摄像头不可用 / 网络 / Key 失效自动降级提示

用法：
  python 实操2-VLM流式智能分析.py                # 自动调用实操1拍摄 + 分析
  python 实操2-VLM流式智能分析.py 图片路径.jpg    # 跳过拍摄，直接分析指定图片
"""

import io
import re
import sys
import base64
from pathlib import Path

# 解决 Windows 终端编码问题（emoji/中文输出）
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

try:
    from PIL import Image
except ImportError:
    Image = None

import tkinter as tk

from openai import OpenAI

from camera import capture_snapshot


# ==================== ModelScope VLM 配置 ====================

MODELSCOPE_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_API_KEY = "ms-ec53da2b-1f1f-406a-b26c-7bbe335351cb"
MODELSCOPE_MODEL = "Qwen/Qwen3.8-27B"   # ModelScope Model-Id

PROMPT = "请分析一下当前场景内容"

# 实操动作1 保存的截图文件（同目录，两实操串联）
SNAPSHOT_FILE = "_snapshot.jpg"


# ==================== VisionAnalyzer：VLM 接口标准化封装（真实调用） ====================

class VisionAnalyzer:
    """VLM 多模态接口标准化封装：真实调用 ModelScope，流式输出 reasoning + answer"""

    def __init__(self, api_key: str = MODELSCOPE_API_KEY, model: str = MODELSCOPE_MODEL):
        self.client = OpenAI(base_url=MODELSCOPE_BASE_URL, api_key=api_key)
        self.model = model

    def analyze_image_stream(self, image_src: str, prompt: str = PROMPT, on_answer=None):
        """
        真实调用 VLM 流式返回：先输出思考过程（reasoning_content），
        再输出最终答案（content）。on_answer 可选回调收集答案文本。
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    self._build_image_content(image_src),
                ],
            }],
            stream=True,
        )

        done_reasoning = False
        answer_parts = []
        for chunk in response:
            if not chunk.choices:
                continue
            reasoning_chunk = chunk.choices[0].delta.reasoning_content
            answer_chunk = chunk.choices[0].delta.content

            if reasoning_chunk:
                print(reasoning_chunk, end="", flush=True)
            elif answer_chunk:
                if not done_reasoning:
                    print("\n")
                    done_reasoning = True
                answer_parts.append(answer_chunk)

        if answer_parts:
            full_answer = "".join(answer_parts)
            print("\n" + "─" * 60)
            print("【最终分析结果 · Markdown 渲染】")
            print("─" * 60)
            _print_markdown(full_answer)
            print()
            if on_answer:
                on_answer(full_answer)
        else:
            print()

    @staticmethod
    def _build_image_content(image_src: str) -> dict:
        """构建图片内容块：支持 URL / 本地路径 / data URL"""
        if image_src.startswith(("http://", "https://", "data:")):
            return {"type": "image_url", "image_url": {"url": image_src}}
        p = Path(image_src)
        if not p.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_src}")
        suffix = p.suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "bmp": "image/bmp"}.get(suffix, "image/jpeg")
        b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


# ==================== 终端 Markdown 渲染 ====================

def _print_markdown(text: str):
    """终端 Markdown 渲染（使用 rich 库），失败则退化为纯文本"""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console(soft_wrap=True).print(Markdown(text))
    except Exception:
        print(text)


# ==================== 分析结果弹窗（Markdown 渲染） ====================

class MarkdownTextBox(tk.Text):
    """将 Markdown 文本渲染到 tk.Text（支持标题 / 粗体 / 列表 / 代码块 / 引用 / 分隔线）。
    轻量自研实现，避免引入 tkhtmlview 等额外依赖。
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.config(wrap="word", font=("Microsoft YaHei", 12), bg="#ffffff",
                    padx=16, pady=12, relief="flat", borderwidth=0,
                    highlightthickness=0, spacing1=2, spacing3=4)
        self.tag_configure("h1", font=("Microsoft YaHei", 17, "bold"),
                           foreground="#1e293b", spacing3=8)
        self.tag_configure("h2", font=("Microsoft YaHei", 15, "bold"),
                           foreground="#1e293b", spacing3=7)
        self.tag_configure("h3", font=("Microsoft YaHei", 13, "bold"),
                           foreground="#1e293b", spacing3=6)
        self.tag_configure("bold", font=("Microsoft YaHei", 12, "bold"),
                           foreground="#0f172a")
        self.tag_configure("bullet", lmargin1=18, lmargin2=36, foreground="#334155")
        self.tag_configure("code", font=("Consolas", 11),
                           background="#f1f5f9", foreground="#be123c")
        self.tag_configure("codeblock", font=("Consolas", 10),
                           background="#f8fafc", foreground="#0f172a",
                           lmargin1=10, lmargin2=10, spacing1=5, spacing3=5)
        self.tag_configure("quote", foreground="#64748b", lmargin1=16, lmargin2=16)
        self.tag_configure("hr", foreground="#cbd5e1")

    def render(self, md: str):
        """渲染 Markdown 文本，渲染后锁定为只读"""
        self.config(state="normal")
        self.delete("1.0", "end")
        in_code = False
        for raw in md.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                self.insert("end", "\n")
                continue
            if stripped.startswith("```"):
                in_code = not in_code
                if not in_code:
                    self.insert("end", "\n")
                continue
            if in_code:
                self.insert("end", line + "\n", "codeblock")
                continue
            m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
            if m:
                self._inline(m.group(2).strip(), f"h{len(m.group(1))}")
                self.insert("end", "\n")
                continue
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                self.insert("end", "─" * 46, "hr")
                self.insert("end", "\n")
                continue
            if stripped.startswith(">"):
                self._inline(stripped.lstrip("> ").strip(), "quote")
                self.insert("end", "\n")
                continue
            m = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
            if m:
                marker = m.group(1)
                prefix = ("•  " if marker in ("-", "*", "+") else marker + "  ")
                self.insert("end", prefix, "bullet")
                self._inline(m.group(2).strip(), "bullet")
                self.insert("end", "\n")
                continue
            self._inline(stripped)
            self.insert("end", "\n")
        self.config(state="disabled")

    def _inline(self, text: str, base: str = None):
        """行内渲染：**加粗** 与 `行内代码`"""
        base_tag = (base,) if base else ()
        bold_re = re.compile(r"\*\*(.+?)\*\*")
        code_re = re.compile(r"`([^`]+?)`")
        matches = sorted(list(bold_re.finditer(text)) + list(code_re.finditer(text)),
                         key=lambda x: x.start())
        last = 0
        for m in matches:
            if m.start() > last:
                self.insert("end", text[last:m.start()], base_tag)
            if m.re is bold_re:
                self.insert("end", m.group(1), base_tag + ("bold",))
            else:
                self.insert("end", m.group(1), base_tag + ("code",))
            last = m.end()
        if last < len(text):
            self.insert("end", text[last:], base_tag)


class ResultWindow:
    """展示 VLM 分析结果的弹窗（Markdown 渲染，需人为关闭）"""

    def __init__(self, answer: str):
        self.root = tk.Tk()
        self.root.title("② VLM 分析结果（Markdown 渲染，请手动关闭本窗口）")
        self.root.geometry("780x600")

        header = tk.Frame(self.root, bg="#2563eb")
        header.pack(fill="x")
        tk.Label(header, text="ModelScope Qwen VLM 流式分析结果",
                 font=("Microsoft YaHei", 15, "bold"), fg="white", bg="#2563eb",
                 pady=10).pack()

        text_box = MarkdownTextBox(self.root)
        text_box.pack(padx=14, pady=10, fill="both", expand=True)
        text_box.render(answer)

        tk.Label(self.root, text="关闭窗口结束演示",
                 font=("Microsoft YaHei", 10), fg="#94a3b8").pack(pady=6)

        self.root.bind("<Key>", lambda e: self.quit())

    def show(self):
        self.root.mainloop()

    def quit(self):
        self.root.destroy()


# ==================== 实操运行演示 ====================

def main():
    print("=" * 60)
    print("【实操动作2】VLM大模型流式智能分析实操演示")
    print("=" * 60)

    # ① 自动调用实操1 的拍摄函数（capture_snapshot）实拍当前画面
    print("\n[1/4] 自动调用实操动作1 的拍摄逻辑（capture_snapshot）实拍当前画面")
    if len(sys.argv) > 1:
        # 指定图片模式：跳过拍摄，直接分析指定图片
        image_path = sys.argv[1]
        print(f"  [指定图片] 跳过拍摄，直接分析: {image_path}")
        if not Path(image_path).exists():
            print(f"  ⚠️ 未找到图片: {image_path}")
            return
        raw_img = Image.open(image_path).convert("RGB")
    else:
        # 自动拍摄模式：摄像头弹窗 → 倒计时截图 → 自动保存并返回
        raw_img = capture_snapshot()
        if raw_img is None:
            print("[取消] 未完成拍摄，演示结束。")
            return
        image_path = str(Path(__file__).parent / SNAPSHOT_FILE)

    print(f"  → 已获取画面（{raw_img.size[0]}×{raw_img.size[1]}），截图已保存为 _snapshot.jpg")

    print("\n[2/4] 截图自动 Base64 编码，发送给 ModelScope VLM 模型")
    print(f"  → 模型: {MODELSCOPE_MODEL}")
    print(f"  → 提示词: 「{PROMPT}」")
    print("  → 流式输出（先思考过程，后最终答案）：\n")

    # 拍摄的 PIL 图 → JPEG → base64 data URL（轻量化传参）
    buf = io.BytesIO()
    raw_img.save(buf, format="JPEG", quality=90)
    data_url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

    analyzer = VisionAnalyzer()
    result_holder = {}

    def on_answer(answer: str):
        result_holder["answer"] = answer

    try:
        analyzer.analyze_image_stream(data_url, PROMPT, on_answer=on_answer)
        print("\n✅ 真实 VLM 调用成功，流式分析完成。")
    except Exception as e:
        print(f"\n⚠️ 调用失败: {e}")
        result_holder["answer"] = f"[调用失败] {e}\n\n请检查：网络连通、ModelScope API Key 是否有效。"

    print("\n[3/4] 双业务场景通用性（替换提示词即切换）")
    print("    - 用户自由视觉问答：如「请分析一下当前场景内容」")
    print("    - 后台自动风险检测：如「判断画面中是否有烟雾、溢锅等安全隐患？请输出JSON」")

    print("\n[4/4] 弹窗展示分析结果（需人为关闭）")
    win = ResultWindow(result_holder.get("answer", "（无分析结果）"))
    win.show()

    print("\n✅ 实操动作2 演示完成：自动拍摄（复用实操1）→ 真实 VLM 流式分析 → 结果弹窗，全程闭环。")


if __name__ == "__main__":
    main()
