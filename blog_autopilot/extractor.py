"""文本提取模块 — 支持 PDF / Markdown / TXT"""

import logging

from pypdf import PdfReader

from blog_autopilot.constants import MIN_EXTRACTED_TEXT_LENGTH
from blog_autopilot.exceptions import ExtractionError

logger = logging.getLogger("blog-autopilot")


def extract_text_from_file(filepath: str) -> str:
    """
    提取文件文本内容。

    抛出:
        ExtractionError: 文件格式不支持、读取失败、内容过短
    """
    ext = filepath.rsplit(".", 1)[-1].lower()

    try:
        if ext in ("md", "txt"):
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

        elif ext == "pdf":
            reader = PdfReader(filepath)
            content = ""
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    content += text + "\n"

        else:
            raise ExtractionError(f"不支持的文件格式: .{ext}")

    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"读取文件失败 {filepath}: {e}") from e

    content = content.strip()
    if len(content) < MIN_EXTRACTED_TEXT_LENGTH:
        raise ExtractionError(
            f"文件内容过短 ({len(content)} 字符, 最少需要 {MIN_EXTRACTED_TEXT_LENGTH})"
        )

    logger.info(f"成功提取 {len(content)} 字符 (来自 .{ext} 文件)")
    return content
