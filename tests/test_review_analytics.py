"""测试审核反馈学习系统"""

import pytest
from unittest.mock import MagicMock

from blog_autopilot.review_analytics import (
    ReviewCalibration,
    fetch_calibration,
    format_exemplar_context,
    format_review_calibration_context,
)


class TestReviewCalibration:

    def test_empty_calibration(self):
        cal = ReviewCalibration()
        assert cal.has_stats is False
        assert cal.has_exemplars is False
        assert cal.sample_count == 0

    def test_with_stats(self):
        cal = ReviewCalibration(
            sample_count=30,
            avg_consistency=7.5,
            avg_readability=6.8,
            avg_ai_cliche=7.2,
            avg_overall=7.1,
            std_overall=1.35,
        )
        assert cal.has_stats is True
        assert cal.has_exemplars is False

    def test_with_exemplars(self):
        exemplars = (
            {"article_title": "好文章", "overall_score": 9, "summary": "优秀"},
        )
        cal = ReviewCalibration(exemplars=exemplars)
        assert cal.has_stats is False
        assert cal.has_exemplars is True

    def test_with_both(self):
        cal = ReviewCalibration(
            sample_count=10,
            avg_overall=7.0,
            std_overall=1.0,
            exemplars=({"article_title": "A", "overall_score": 8, "summary": "好"},),
        )
        assert cal.has_stats is True
        assert cal.has_exemplars is True


class TestFetchCalibration:

    def _mock_db(self, stats=None, exemplars=None):
        db = MagicMock()
        db.fetch_review_stats.return_value = stats
        db.fetch_high_score_articles.return_value = exemplars or []
        return db

    def test_no_data_returns_empty(self):
        db = self._mock_db()
        cal = fetch_calibration(db)
        assert cal.has_stats is False
        assert cal.has_exemplars is False

    def test_with_stats_only(self):
        stats = {
            "count": 25,
            "avg_consistency": 7.5,
            "avg_readability": 6.8,
            "avg_ai_cliche": 7.2,
            "avg_overall": 7.1,
            "std_overall": 1.35,
        }
        db = self._mock_db(stats=stats)
        cal = fetch_calibration(db, category_name="Articles")
        assert cal.sample_count == 25
        assert cal.avg_overall == 7.1
        db.fetch_review_stats.assert_called_once_with(
            category_name="Articles", limit=50,
        )

    def test_with_exemplars_only(self):
        exemplars = [
            {"article_title": "好文章", "overall_score": 9, "summary": "优秀"},
        ]
        db = self._mock_db(exemplars=exemplars)
        cal = fetch_calibration(db)
        assert cal.has_stats is False
        assert cal.has_exemplars is True
        assert len(cal.exemplars) == 1

    def test_with_both(self):
        stats = {
            "count": 10, "avg_consistency": 7.0,
            "avg_readability": 7.0, "avg_ai_cliche": 7.0,
            "avg_overall": 7.0, "std_overall": 1.0,
        }
        exemplars = [
            {"article_title": "A", "overall_score": 9, "summary": "好"},
        ]
        db = self._mock_db(stats=stats, exemplars=exemplars)
        cal = fetch_calibration(db)
        assert cal.has_stats is True
        assert cal.has_exemplars is True

    def test_none_values_in_stats_default_to_zero(self):
        stats = {
            "count": 5,
            "avg_consistency": None,
            "avg_readability": None,
            "avg_ai_cliche": None,
            "avg_overall": None,
            "std_overall": None,
        }
        db = self._mock_db(stats=stats)
        cal = fetch_calibration(db)
        assert cal.avg_overall == 0.0
        assert cal.std_overall == 0.0


class TestFormatReviewCalibrationContext:

    def test_empty_calibration_returns_empty(self):
        cal = ReviewCalibration()
        result = format_review_calibration_context(cal)
        assert result == ""

    def test_with_stats_contains_averages(self):
        cal = ReviewCalibration(
            sample_count=30,
            avg_consistency=7.5,
            avg_readability=6.8,
            avg_ai_cliche=7.2,
            avg_overall=7.1,
            std_overall=1.35,
        )
        result = format_review_calibration_context(cal)
        assert "30" in result
        assert "7.5" in result
        assert "6.8" in result
        assert "7.2" in result
        assert "7.1" in result
        assert "1.35" in result
        assert "校准" in result

    def test_contains_guidance(self):
        cal = ReviewCalibration(
            sample_count=10,
            avg_overall=7.0,
            std_overall=1.0,
        )
        result = format_review_calibration_context(cal)
        assert "区分度" in result


class TestFormatExemplarContext:

    def test_empty_exemplars_returns_empty(self):
        cal = ReviewCalibration()
        result = format_exemplar_context(cal)
        assert result == ""

    def test_with_exemplars_contains_titles(self):
        cal = ReviewCalibration(
            exemplars=(
                {"article_title": "Redis缓存策略", "overall_score": 9, "summary": "结构清晰"},
                {"article_title": "K8s部署实践", "overall_score": 8, "summary": "内容准确"},
            ),
        )
        result = format_exemplar_context(cal)
        assert "Redis缓存策略" in result
        assert "K8s部署实践" in result
        assert "9" in result
        assert "结构清晰" in result

    def test_missing_summary_handled(self):
        cal = ReviewCalibration(
            exemplars=(
                {"article_title": "无摘要文章", "overall_score": 8},
            ),
        )
        result = format_exemplar_context(cal)
        assert "无摘要文章" in result
        assert "高质量" in result

    def test_article_summary_included(self):
        """article_summary 字段存在时显示内容摘要"""
        cal = ReviewCalibration(
            exemplars=(
                {
                    "article_title": "深度学习入门",
                    "overall_score": 9,
                    "summary": "结构清晰",
                    "article_summary": "本文介绍了深度学习的基本概念和常用框架",
                },
            ),
        )
        result = format_exemplar_context(cal)
        assert "内容摘要: 本文介绍了深度学习的基本概念和常用框架" in result
        assert "审核评价: 结构清晰" in result

    def test_article_summary_missing_graceful(self):
        """article_summary 缺失时不显示内容摘要行"""
        cal = ReviewCalibration(
            exemplars=(
                {"article_title": "无摘要", "overall_score": 8, "summary": "不错"},
            ),
        )
        result = format_exemplar_context(cal)
        assert "内容摘要" not in result
        assert "审核评价: 不错" in result

    def test_exemplar_numbering(self):
        cal = ReviewCalibration(
            exemplars=(
                {"article_title": "A", "overall_score": 9, "summary": "好"},
                {"article_title": "B", "overall_score": 8, "summary": "不错"},
            ),
        )
        result = format_exemplar_context(cal)
        assert "1." in result
        assert "2." in result
