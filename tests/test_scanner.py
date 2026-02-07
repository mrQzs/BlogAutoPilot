"""测试目录扫描和路径解析"""

import os

import pytest

from blog_autopilot.scanner import parse_directory_structure, scan_input_directory


class TestParseDirectoryStructure:
    """测试 parse_directory_structure()"""

    def test_valid_magazine_path(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Magazine", "Science_28", "article.pdf")
        result = parse_directory_structure(filepath, input_dir)

        assert result is not None
        assert result.category_name == "Magazine"
        assert result.subcategory_name == "Science"
        assert result.category_id == 28
        assert result.hashtag == "#Magazine_Science"

    def test_valid_articles_path(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Articles", "Tech_10", "test.md")
        result = parse_directory_structure(filepath, input_dir)

        assert result is not None
        assert result.category_name == "Articles"
        assert result.subcategory_name == "Tech"
        assert result.category_id == 10

    def test_valid_books_path(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Books", "Fiction_15", "book.txt")
        result = parse_directory_structure(filepath, input_dir)

        assert result is not None
        assert result.category_name == "Books"
        assert result.category_id == 15

    def test_root_file_returns_none(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "root_file.pdf")
        assert parse_directory_structure(filepath, input_dir) is None

    def test_missing_number_returns_none(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Magazine", "Science", "article.pdf")
        assert parse_directory_structure(filepath, input_dir) is None

    def test_unknown_category_returns_none(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Unknown", "Tech_10", "file.pdf")
        assert parse_directory_structure(filepath, input_dir) is None

    def test_zero_category_id_returns_none(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(input_dir, "Magazine", "Science_0", "file.pdf")
        assert parse_directory_structure(filepath, input_dir) is None

    def test_too_deep_path_returns_none(self, tmp_dirs):
        input_dir = tmp_dirs["input"]
        filepath = os.path.join(
            input_dir, "Magazine", "Science_28", "Sub", "file.pdf"
        )
        assert parse_directory_structure(filepath, input_dir) is None


class TestScanInputDirectory:
    """测试 scan_input_directory()"""

    def test_finds_valid_files(self, tmp_dirs):
        input_dir = tmp_dirs["input"]

        path1 = os.path.join(input_dir, "Magazine", "Science_28")
        os.makedirs(path1, exist_ok=True)
        with open(os.path.join(path1, "test1.txt"), "w") as f:
            f.write("content")

        path2 = os.path.join(input_dir, "Articles", "Tech_10")
        os.makedirs(path2, exist_ok=True)
        with open(os.path.join(path2, "test2.txt"), "w") as f:
            f.write("content")

        result = scan_input_directory(input_dir)
        assert len(result) == 2

    def test_skips_hidden_files(self, tmp_dirs):
        input_dir = tmp_dirs["input"]

        path = os.path.join(input_dir, "Magazine", "Science_28")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, ".hidden"), "w") as f:
            f.write("content")

        result = scan_input_directory(input_dir)
        assert len(result) == 0

    def test_empty_directory(self, tmp_dirs):
        result = scan_input_directory(tmp_dirs["input"])
        assert result == []
