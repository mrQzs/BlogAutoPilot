"""测试套话动态检测库"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.cliche_library import (
    ClicheEntry,
    ClicheReport,
    ClicheUpdater,
    build_cliche_entries,
    extract_phrases,
    format_cliche_context,
    load_cliche_library,
    save_cliche_library,
)
from blog_autopilot.exceptions import ClicheLibraryError


@pytest.fixture
def pipeline_settings(tmp_path):
    """构造测试用 Settings"""
    from blog_autopilot.config import (
        AISettings, DatabaseSettings, EmbeddingSettings,
        PathSettings, Settings, TelegramSettings, WordPressSettings,
    )
    input_dir = tmp_path / "input"
    processed_dir = tmp_path / "processed"
    drafts_dir = tmp_path / "drafts"
    input_dir.mkdir()
    processed_dir.mkdir()
    drafts_dir.mkdir()
    return Settings(
        wp=WordPressSettings(
            url="https://test.wp/api", user="testuser", app_password="testpass",
        ),
        tg=TelegramSettings(bot_token="test-token", channel_id="@test"),
        ai=AISettings(api_key="test-key", api_base="https://test.api/v1"),
        paths=PathSettings(
            input_folder=str(input_dir),
            processed_folder=str(processed_dir),
            drafts_folder=str(drafts_dir),
        ),
        database=DatabaseSettings(),
        embedding=EmbeddingSettings(),
    )


# ── extract_phrases ──


class TestExtractPhrases:

    def test_chinese_quotes(self):
        desc = "第三段使用了「值得注意的是」和「综上所述」等套话"
        result = extract_phrases(desc)
        assert "值得注意的是" in result
        assert "综上所述" in result

    def test_western_quotes(self):
        desc = "文章中出现了\u201c不可否认\u201d和\u201c毋庸置疑\u201d"
        result = extract_phrases(desc)
        assert "不可否认" in result
        assert "毋庸置疑" in result

    def test_mixed_quotes(self):
        desc = "使用了「随着…的发展」和『众所周知』"
        result = extract_phrases(desc)
        assert "随着…的发展" in result
        assert "众所周知" in result

    def test_filters_too_short(self):
        desc = "出现了「啊」这样的语气词"
        result = extract_phrases(desc)
        assert result == []

    def test_filters_too_long(self):
        desc = "「" + "很长" * 20 + "」"
        result = extract_phrases(desc)
        assert result == []

    def test_no_quotes_returns_empty(self):
        desc = "文章整体质量不错，没有明显套话"
        result = extract_phrases(desc)
        assert result == []


# ── build_cliche_entries ──


class TestBuildClicheEntries:

    def test_frequency_counting(self):
        """同一套话多次出现时频率正确累加"""
        issues = [
            {"description": "使用了「值得注意的是」", "severity": "high"},
            {"description": "出现「值得注意的是」和「综上所述」", "severity": "medium"},
            {"description": "再次使用「值得注意的是」", "severity": "high"},
        ]
        entries = build_cliche_entries(issues)
        top = entries[0]
        assert top.phrase == "值得注意的是"
        assert top.frequency == 3

    def test_severity_uses_most_common(self):
        """severity 取出现最多的级别"""
        issues = [
            {"description": "「套话A」", "severity": "high"},
            {"description": "「套话A」", "severity": "high"},
            {"description": "「套话A」", "severity": "low"},
        ]
        entries = build_cliche_entries(issues)
        assert entries[0].severity == "high"

    def test_min_frequency_filter(self):
        """低于最低频率阈值的套话被过滤"""
        issues = [
            {"description": "「只出现一次」", "severity": "medium"},
        ]
        entries = build_cliche_entries(issues)
        assert len(entries) == 0

    def test_empty_issues(self):
        entries = build_cliche_entries([])
        assert entries == []


# ── save / load cliche library ──


class TestSaveLoadClicheLibrary:

    def test_save_and_load_roundtrip(self, tmp_path):
        """保存后加载，数据一致"""
        path = tmp_path / "cliches.json"
        entries = [
            ClicheEntry(phrase="值得注意的是", frequency=5, severity="high"),
            ClicheEntry(phrase="综上所述", frequency=3, severity="medium"),
        ]
        save_cliche_library(entries, path=path)
        loaded = load_cliche_library(path=path)
        assert len(loaded) == 2
        assert loaded[0].phrase == "值得注意的是"
        assert loaded[1].frequency == 3

    def test_load_missing_file(self, tmp_path):
        """文件不存在时返回空列表"""
        path = tmp_path / "nonexistent.json"
        result = load_cliche_library(path=path)
        assert result == []

    def test_load_corrupt_json(self, tmp_path):
        """JSON 损坏时返回空列表"""
        path = tmp_path / "bad.json"
        path.write_text("{broken", encoding="utf-8")
        result = load_cliche_library(path=path)
        assert result == []


# ── format_cliche_context ──


class TestFormatClicheContext:

    def test_with_entries(self):
        entries = [
            ClicheEntry(phrase="值得注意的是", frequency=5, severity="high"),
            ClicheEntry(phrase="综上所述", frequency=3, severity="medium"),
        ]
        ctx = format_cliche_context(entries)
        assert "值得注意的是" in ctx
        assert "综上所述" in ctx
        assert "动态套话检测库" in ctx

    def test_empty_entries(self):
        ctx = format_cliche_context([])
        assert ctx == ""


# ── ClicheUpdater ──


class TestClicheUpdater:

    @patch("blog_autopilot.cliche_library.save_cliche_library")
    @patch("blog_autopilot.db.Database")
    def test_update_success(self, mock_db_cls, mock_save, pipeline_settings):
        mock_db = MagicMock()
        mock_db.test_connection.return_value = True
        mock_db.fetch_cliche_issues.return_value = [
            {"description": "使用了「值得注意的是」", "severity": "high"},
            {"description": "出现「值得注意的是」和「综上所述」", "severity": "medium"},
            {"description": "再次「值得注意的是」", "severity": "high"},
            {"description": "又见「综上所述」", "severity": "medium"},
            {"description": "还有「值得注意的是」和「综上所述」", "severity": "high"},
        ]
        mock_db_cls.return_value = mock_db

        updater = ClicheUpdater(pipeline_settings)
        report = updater.update()

        assert report.issue_count == 5
        assert report.unique_phrases > 0
        mock_save.assert_called_once()

    @patch("blog_autopilot.db.Database")
    def test_update_insufficient_reviews(self, mock_db_cls, pipeline_settings):
        """审核记录不足时抛出异常"""
        mock_db = MagicMock()
        mock_db.test_connection.return_value = True
        mock_db.fetch_cliche_issues.return_value = [
            {"description": "「套话」", "severity": "medium"},
        ]
        mock_db_cls.return_value = mock_db

        updater = ClicheUpdater(pipeline_settings)
        with pytest.raises(ClicheLibraryError, match="审核记录不足"):
            updater.update()

    @patch("blog_autopilot.db.Database")
    def test_update_db_connection_failure(self, mock_db_cls, pipeline_settings):
        """数据库连接失败时抛出异常"""
        mock_db = MagicMock()
        mock_db.test_connection.return_value = False
        mock_db_cls.return_value = mock_db

        updater = ClicheUpdater(pipeline_settings)
        with pytest.raises(ClicheLibraryError, match="数据库连接失败"):
            updater.update()

    @patch("blog_autopilot.cliche_library.save_cliche_library")
    @patch("blog_autopilot.db.Database")
    def test_format_output(self, mock_db_cls, mock_save, pipeline_settings):
        """format_output 包含关键信息"""
        mock_db = MagicMock()
        mock_db.test_connection.return_value = True
        mock_db.fetch_cliche_issues.return_value = [
            {"description": "「值得注意的是」", "severity": "high"},
            {"description": "「值得注意的是」", "severity": "high"},
            {"description": "「值得注意的是」", "severity": "high"},
            {"description": "「值得注意的是」", "severity": "high"},
            {"description": "「值得注意的是」", "severity": "high"},
        ]
        mock_db_cls.return_value = mock_db

        updater = ClicheUpdater(pipeline_settings)
        report = updater.update()
        output = updater.format_output(report)

        assert "套话库更新完成" in output
        assert "值得注意的是" in output


# ── DB 层: fetch_cliche_issues ──


class TestFetchClicheIssues:

    def test_extracts_ai_cliche_issues(self, db_settings):
        """从 issues_json 中正确提取 ai_cliche 类别"""
        import json
        from blog_autopilot.db import Database

        db = Database(db_settings)
        rows = [
            {
                "issues_json": json.dumps([
                    {"category": "ai_cliche", "severity": "high",
                     "description": "使用了「值得注意的是」", "suggestion": "改"},
                    {"category": "readability", "severity": "low",
                     "description": "段落过长", "suggestion": "拆分"},
                ]),
                "category_name": "News",
            },
        ]
        with patch.object(db, "fetch_all", return_value=rows):
            result = db.fetch_cliche_issues()
            assert len(result) == 1
            assert result[0]["description"] == "使用了「值得注意的是」"
            assert result[0]["severity"] == "high"

    def test_db_error_returns_empty(self, db_settings):
        from blog_autopilot.db import Database

        db = Database(db_settings)
        with patch.object(db, "fetch_all", side_effect=Exception("连接超时")):
            result = db.fetch_cliche_issues()
            assert result == []
