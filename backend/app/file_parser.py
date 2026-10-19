"""
文档解析模块

将用户上传的非图片文件提取为文本，供 LLM 理解。
目前支持：
- PDF
- Word (.docx)
- Excel (.xlsx)
- 纯文本文件（txt / md / json / csv / 代码文件等）
"""
from __future__ import annotations

import io
from typing import BinaryIO

from .logger import backend_log

# 纯文本 MIME 类型后缀白名单
TEXT_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-python-code",
    "application/x-sh",
    "application/yaml",
)

# 常见代码文件扩展名
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".r",
    ".html", ".htm", ".css", ".scss", ".less", ".sql", ".sh", ".bash",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".rst",
}


def _is_text_file(filename: str, mime_type: str) -> bool:
    lower_name = filename.lower()
    ext = lower_name[lower_name.rfind(".") :] if "." in lower_name else ""
    if ext in TEXT_EXTENSIONS or ext in CODE_EXTENSIONS:
        return True
    if mime_type.startswith(TEXT_MIME_PREFIXES):
        return True
    return False


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gbk", "gb2312", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes) -> str:
    try:
        import PyPDF2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 PDF 解析依赖 PyPDF2") from exc

    text_parts: list[str] = []
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    for page in reader.pages:
        try:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        except Exception as exc:
            backend_log(f"[PDF] 页面解析失败: {exc}")
    return "\n".join(text_parts).strip()


def _parse_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Word 解析依赖 python-docx") from exc

    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()


def _parse_xlsx(data: bytes) -> str:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Excel 解析依赖 openpyxl") from exc

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    text_parts: list[str] = []
    for sheet in workbook.worksheets:
        text_parts.append(f"[工作表: {sheet.title}]")
        rows: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            row_text = [str(cell) if cell is not None else "" for cell in row]
            if any(row_text):
                rows.append("\t".join(row_text))
        if rows:
            text_parts.append("\n".join(rows))
    return "\n\n".join(text_parts).strip()


def parse_document(name: str, mime_type: str, data: bytes) -> str:
    """解析文档为文本。不支持时返回空字符串并记录日志。"""
    if not data:
        return ""

    lower_name = name.lower()
    try:
        if mime_type == "application/pdf" or lower_name.endswith(".pdf"):
            return _parse_pdf(data)

        if mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ) or lower_name.endswith((".docx", ".doc")):
            return _parse_docx(data)

        if mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ) or lower_name.endswith((".xlsx", ".xls", ".csv")):
            return _parse_xlsx(data)

        if _is_text_file(name, mime_type):
            return _decode_text(data)

        backend_log(f"[文件] 暂不支持的文件类型: {name} ({mime_type})")
        return ""
    except Exception as exc:
        backend_log(f"[文件] 解析失败 {name}: {exc}")
        return ""


def is_image_file(mime_type: str, name: str = "") -> bool:
    """判断是否为可直接送入视觉模型的位图图片文件。SVG 按文本处理。"""
    lower_name = name.lower()
    if lower_name.endswith(".svg") or mime_type == "image/svg+xml":
        return False
    if mime_type.startswith("image/"):
        return True
    return lower_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
