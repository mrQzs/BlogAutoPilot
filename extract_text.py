"""
📄 文件提取模块
支持 PDF / Markdown / TXT 文件的文本提取
可独立运行测试: python extract_text.py ./test.pdf
"""

from pypdf import PdfReader
from config import logger


def extract_text_from_file(filepath: str) -> str | None:
    """提取文件文本内容"""
    ext = filepath.rsplit('.', 1)[-1].lower()
    content = ""

    try:
        if ext in ('md', 'txt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

        elif ext == 'pdf':
            reader = PdfReader(filepath)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    content += text + "\n"

        else:
            logger.warning(f"不支持的文件格式: .{ext}")
            return None

        content = content.strip()
        if len(content) < 50:
            logger.warning(f"文件内容过短 ({len(content)} 字符), 跳过")
            return None

        logger.info(f"✅ 成功提取 {len(content)} 字符 (来自 .{ext} 文件)")
        return content

    except Exception as e:
        logger.error(f"读取文件失败 {filepath}: {e}")
        return None


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python extract_text.py <文件路径>")
        print("示例: python extract_text.py ./input/test.pdf")
        sys.exit(1)

    filepath = sys.argv[1]
    text = extract_text_from_file(filepath)

    if text:
        print(f"\n{'='*60}")
        print(f"提取成功! 共 {len(text)} 字符")
        print(f"{'='*60}")
        print(text[:2000])
        if len(text) > 2000:
            print(f"\n... (省略剩余 {len(text)-2000} 字符)")
    else:
        print("❌ 提取失败")
