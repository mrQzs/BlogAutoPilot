"""测试套话动态检测库"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.cliche_library import (
    BASELINE_FILE,
    ClicheEntry,
    ClicheReport,
    ClicheUpdater,
    auto_refresh_cliches,
    build_cliche_entries,
    extract_phrases,
    format_cliche_context,
    is_cliche_stale,
    load_baseline_cliches,
    load_cliche_library,
    load_merged_cliches,
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

    def test_load_skips_bad_entries_keeps_good(self, tmp_path):
        """格式错误的条目被跳过，有效条目仍被加载"""
        path = tmp_path / "mixed.json"
        data = [
            {"phrase": "好条目", "frequency": 3, "severity": "high"},
            "not-a-dict",
            {"no_phrase_key": True},
            {"phrase": "另一个好条目", "frequency": "not-a-number"},
            {"phrase": "第三个好条目"},
        ]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = load_cliche_library(path=path)
        # "好条目" loaded, "not-a-dict" skipped, "no_phrase_key" skipped,
        # "另一个好条目" has non-int frequency → skipped due to ValueError? No, int("not-a-number") raises ValueError
        # Wait, looking at the code: frequency=int(item.get("frequency", 0))
        # int("not-a-number") raises ValueError, so it should be skipped
        # "第三个好条目" should be loaded with frequency=0 and severity="medium"
        phrases = [e.phrase for e in result]
        assert "好条目" in phrases
        assert "第三个好条目" in phrases
        assert len(result) == 2  # "好条目" and "第三个好条目"

    def test_load_missing_frequency_defaults_to_zero(self, tmp_path):
        """frequency 缺失时默认 0"""
        path = tmp_path / "no_freq.json"
        data = [{"phrase": "测试", "severity": "low"}]
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_cliche_library(path=path)
        assert result[0].frequency == 0
        assert result[0].severity == "low"

    def test_load_missing_severity_defaults_to_medium(self, tmp_path):
        """severity 缺失时默认 medium"""
        path = tmp_path / "no_sev.json"
        data = [{"phrase": "测试", "frequency": 2}]
        path.write_text(json.dumps(data), encoding="utf-8")
        result = load_cliche_library(path=path)
        assert result[0].severity == "medium"


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


# ── Part D: 基础套话库、合并、过期、自动刷新 ──


class TestLoadBaselineCliches:

    def test_load_existing_baseline(self, tmp_path):
        """加载存在的基础套话库"""
        path = tmp_path / "baseline.json"
        data = [
            {"phrase": "值得注意的是", "severity": "high"},
            {"phrase": "综上所述", "severity": "medium"},
        ]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        entries = load_baseline_cliches(path=path)
        assert len(entries) == 2
        assert entries[0].phrase == "值得注意的是"
        assert entries[0].frequency == 0  # baseline 固定 frequency=0
        assert entries[0].severity == "high"

    def test_load_missing_file_returns_empty(self, tmp_path):
        """文件不存在时返回空列表"""
        path = tmp_path / "nonexistent.json"
        entries = load_baseline_cliches(path=path)
        assert entries == []

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        """JSON 损坏时返回空列表"""
        path = tmp_path / "bad_baseline.json"
        path.write_text("{broken", encoding="utf-8")
        entries = load_baseline_cliches(path=path)
        assert entries == []

    def test_default_severity(self, tmp_path):
        """severity 缺失时默认 medium"""
        path = tmp_path / "baseline.json"
        data = [{"phrase": "测试套话"}]
        path.write_text(json.dumps(data), encoding="utf-8")
        entries = load_baseline_cliches(path=path)
        assert entries[0].severity == "medium"

    def test_skips_bad_entries_keeps_good(self, tmp_path):
        """格式错误的条目被跳过，有效条目仍被加载"""
        path = tmp_path / "mixed_baseline.json"
        data = [
            {"phrase": "好条目", "severity": "high"},
            "not-a-dict",
            {"no_phrase_key": True},
            {"phrase": "另一个好条目"},
        ]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        entries = load_baseline_cliches(path=path)
        phrases = [e.phrase for e in entries]
        assert "好条目" in phrases
        assert "另一个好条目" in phrases
        assert len(entries) == 2

    def test_phrase_coerced_to_string(self, tmp_path):
        """非字符串 phrase 被强制转为字符串"""
        path = tmp_path / "int_phrase.json"
        data = [{"phrase": 12345, "severity": "low"}]
        path.write_text(json.dumps(data), encoding="utf-8")
        entries = load_baseline_cliches(path=path)
        assert entries[0].phrase == "12345"

    def test_real_baseline_file(self):
        """项目根目录的 cliche_baseline.json 能正确加载"""
        entries = load_baseline_cliches(path=BASELINE_FILE)
        assert len(entries) >= 15
        assert any(e.phrase == "值得注意的是" for e in entries)


class TestLoadMergedCliches:

    def test_merge_dynamic_and_baseline(self, tmp_path):
        """动态库和基础库合并，动态条目优先"""
        dynamic_path = tmp_path / "dynamic.json"
        baseline_path = tmp_path / "baseline.json"

        dynamic = [
            {"phrase": "值得注意的是", "frequency": 5, "severity": "high"},
            {"phrase": "动态独有", "frequency": 3, "severity": "medium"},
        ]
        baseline = [
            {"phrase": "值得注意的是", "severity": "high"},
            {"phrase": "基础独有", "severity": "low"},
        ]
        dynamic_path.write_text(json.dumps(dynamic, ensure_ascii=False), encoding="utf-8")
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")

        merged = load_merged_cliches(dynamic_path=dynamic_path, baseline_path=baseline_path)
        phrases = [e.phrase for e in merged]

        # 动态库的"值得注意的是"优先（frequency=5），基础库的被去重
        assert phrases.count("值得注意的是") == 1
        assert "动态独有" in phrases
        assert "基础独有" in phrases

        # 动态库条目保持原始 frequency
        match = [e for e in merged if e.phrase == "值得注意的是"][0]
        assert match.frequency == 5

    def test_only_baseline_when_no_dynamic(self, tmp_path):
        """动态库不存在时只返回基础库"""
        dynamic_path = tmp_path / "nonexistent.json"
        baseline_path = tmp_path / "baseline.json"
        baseline = [{"phrase": "基础套话", "severity": "high"}]
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")

        merged = load_merged_cliches(dynamic_path=dynamic_path, baseline_path=baseline_path)
        assert len(merged) == 1
        assert merged[0].phrase == "基础套话"

    def test_only_dynamic_when_no_baseline(self, tmp_path):
        """基础库不存在时只返回动态库"""
        dynamic_path = tmp_path / "dynamic.json"
        baseline_path = tmp_path / "nonexistent.json"
        dynamic = [{"phrase": "动态套话", "frequency": 3, "severity": "medium"}]
        dynamic_path.write_text(json.dumps(dynamic, ensure_ascii=False), encoding="utf-8")

        merged = load_merged_cliches(dynamic_path=dynamic_path, baseline_path=baseline_path)
        assert len(merged) == 1
        assert merged[0].phrase == "动态套话"

    def test_both_missing_returns_empty(self, tmp_path):
        """两个库都不存在时返回空列表"""
        merged = load_merged_cliches(
            dynamic_path=tmp_path / "no1.json",
            baseline_path=tmp_path / "no2.json",
        )
        assert merged == []

    def test_merged_sorted_by_frequency_desc(self, tmp_path):
        """合并后按频率降序排列"""
        dynamic_path = tmp_path / "dynamic.json"
        baseline_path = tmp_path / "baseline.json"

        dynamic = [
            {"phrase": "低频", "frequency": 2, "severity": "medium"},
            {"phrase": "高频", "frequency": 10, "severity": "high"},
            {"phrase": "中频", "frequency": 5, "severity": "medium"},
        ]
        baseline = [
            {"phrase": "基础", "severity": "low"},
        ]
        dynamic_path.write_text(json.dumps(dynamic, ensure_ascii=False), encoding="utf-8")
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")

        merged = load_merged_cliches(dynamic_path=dynamic_path, baseline_path=baseline_path)
        frequencies = [e.frequency for e in merged]
        # 应该是 [10, 5, 2, 0]（降序）
        assert frequencies == sorted(frequencies, reverse=True)
        assert merged[0].phrase == "高频"
        assert merged[-1].phrase == "基础"  # baseline frequency=0 在末尾


class TestIsClicheStale:

    def test_missing_file_is_stale(self, tmp_path):
        """文件不存在视为过期"""
        assert is_cliche_stale(path=tmp_path / "nonexistent.json") is True

    def test_fresh_file_not_stale(self, tmp_path):
        """刚创建的文件不过期"""
        path = tmp_path / "fresh.json"
        path.write_text("[]", encoding="utf-8")
        assert is_cliche_stale(path=path, max_age_hours=168) is False

    def test_old_file_is_stale(self, tmp_path):
        """超过 max_age_hours 的文件过期"""
        path = tmp_path / "old.json"
        path.write_text("[]", encoding="utf-8")
        # 设置文件修改时间为 200 小时前
        import os
        old_time = time.time() - 200 * 3600
        os.utime(str(path), (old_time, old_time))
        assert is_cliche_stale(path=path, max_age_hours=168) is True

    def test_custom_max_age(self, tmp_path):
        """自定义 max_age_hours"""
        path = tmp_path / "test.json"
        path.write_text("[]", encoding="utf-8")
        # 设置文件修改时间为 2 小时前
        import os
        old_time = time.time() - 2 * 3600
        os.utime(str(path), (old_time, old_time))
        assert is_cliche_stale(path=path, max_age_hours=1) is True
        assert is_cliche_stale(path=path, max_age_hours=5) is False


class TestAutoRefreshCliches:

    @patch("blog_autopilot.cliche_library.is_cliche_stale", return_value=False)
    def test_skip_when_not_stale(self, mock_stale, pipeline_settings):
        """不过期时不刷新"""
        with patch("blog_autopilot.cliche_library.ClicheUpdater") as mock_cls:
            auto_refresh_cliches(pipeline_settings)
            mock_cls.assert_not_called()

    @patch("blog_autopilot.cliche_library.is_cliche_stale", return_value=True)
    @patch("blog_autopilot.cliche_library.ClicheUpdater")
    def test_refresh_when_stale(self, mock_updater_cls, mock_stale, pipeline_settings):
        """过期时自动刷新"""
        mock_updater = MagicMock()
        mock_updater.update.return_value = ClicheReport(
            review_count=10, issue_count=10, unique_phrases=5, entries=(),
        )
        mock_updater_cls.return_value = mock_updater

        auto_refresh_cliches(pipeline_settings)
        mock_updater.update.assert_called_once()

    @patch("blog_autopilot.cliche_library.is_cliche_stale", return_value=True)
    @patch("blog_autopilot.cliche_library.ClicheUpdater")
    def test_silent_failure_on_error(self, mock_updater_cls, mock_stale, pipeline_settings):
        """刷新失败时静默"""
        mock_updater = MagicMock()
        mock_updater.update.side_effect = ClicheLibraryError("审核记录不足")
        mock_updater_cls.return_value = mock_updater

        # Should not raise
        auto_refresh_cliches(pipeline_settings)

    @patch("blog_autopilot.cliche_library.is_cliche_stale", return_value=True)
    @patch("blog_autopilot.cliche_library.ClicheUpdater")
    def test_silent_failure_on_unexpected_error(self, mock_updater_cls, mock_stale, pipeline_settings):
        """意外错误也静默"""
        mock_updater_cls.side_effect = Exception("连接失败")

        # Should not raise
        auto_refresh_cliches(pipeline_settings)
