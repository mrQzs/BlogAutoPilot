"""测试文本提取模块"""

import os

import pytest

from blog_autopilot.extractor import extract_text_from_file
from blog_autopilot.exceptions import ExtractionError


class TestExtractText:

    def test_extract_txt_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("A" * 100, encoding="utf-8")
        result = extract_text_from_file(str(f))
        assert len(result) == 100

    def test_extract_md_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\n" + "Content " * 20, encoding="utf-8")
        result = extract_text_from_file(str(f))
        assert "Title" in result

    def test_too_short_raises(self, tmp_path):
        f = tmp_path / "short.txt"
        f.write_text("Hi", encoding="utf-8")
        with pytest.raises(ExtractionError, match="内容过短"):
            extract_text_from_file(str(f))

    def test_unsupported_format_raises(self, tmp_path):
        f = tmp_path / "test.docx"
        f.write_bytes(b"fake docx")
        with pytest.raises(ExtractionError, match="不支持的文件格式"):
            extract_text_from_file(str(f))

    def test_nonexistent_file_raises(self):
        with pytest.raises(ExtractionError, match="读取文件失败"):
            extract_text_from_file("/nonexistent/file.txt")
