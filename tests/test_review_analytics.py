"""测试审核反馈学习系统"""

import pytest
from unittest.mock import MagicMock

from blog_autopilot.review_analytics import (
    ReviewCalibration,
    _pct,
    fetch_calibration,
    format_exemplar_context,
    format_review_calibration_context,
)


class TestPct:

    def test_normal(self):
        assert _pct(20, 30) == "67%"

    def test_zero_total(self):
        assert _pct(5, 0) == "0%"

    def test_full(self):
        assert _pct(10, 10) == "100%"

    def test_zero_part(self):
        assert _pct(0, 10) == "0%"

    def test_rounding(self):
        """1/3 = 33.3% rounds to 33%, 2/3 = 66.7% rounds to 67%"""
        assert _pct(1, 3) == "33%"
        assert _pct(2, 3) == "67%"


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
            avg_factuality=7.3,
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

    def test_new_fields_have_defaults(self):
        cal = ReviewCalibration()
        assert cal.avg_factuality == 0.0
        assert cal.std_consistency == 0.0
        assert cal.std_factuality == 0.0
        assert cal.std_readability == 0.0
        assert cal.std_ai_cliche == 0.0
        assert cal.verdict_pass == 0
        assert cal.verdict_rewrite == 0
        assert cal.verdict_draft == 0


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
            "avg_factuality": 7.3,
            "avg_readability": 6.8,
            "avg_ai_cliche": 7.2,
            "avg_overall": 7.1,
            "std_overall": 1.35,
            "std_consistency": 0.9,
            "std_factuality": 1.1,
            "std_readability": 0.8,
            "std_ai_cliche": 1.2,
            "verdict_pass": 20,
            "verdict_rewrite": 3,
            "verdict_draft": 2,
        }
        db = self._mock_db(stats=stats)
        cal = fetch_calibration(db, category_name="Articles")
        assert cal.sample_count == 25
        assert cal.avg_overall == 7.1
        assert cal.avg_factuality == 7.3
        assert cal.std_consistency == 0.9
        assert cal.std_factuality == 1.1
        assert cal.std_readability == 0.8
        assert cal.std_ai_cliche == 1.2
        assert cal.verdict_pass == 20
        assert cal.verdict_rewrite == 3
        assert cal.verdict_draft == 2
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
            "avg_factuality": 7.0,
            "avg_readability": 7.0, "avg_ai_cliche": 7.0,
            "avg_overall": 7.0, "std_overall": 1.0,
            "std_consistency": 0.8, "std_factuality": 0.9,
            "std_readability": 0.7, "std_ai_cliche": 1.0,
            "verdict_pass": 7, "verdict_rewrite": 2, "verdict_draft": 1,
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
            "avg_factuality": None,
            "avg_readability": None,
            "avg_ai_cliche": None,
            "avg_overall": None,
            "std_overall": None,
            "std_consistency": None,
            "std_factuality": None,
            "std_readability": None,
            "std_ai_cliche": None,
            "verdict_pass": None,
            "verdict_rewrite": None,
            "verdict_draft": None,
        }
        db = self._mock_db(stats=stats)
        cal = fetch_calibration(db)
        assert cal.avg_overall == 0.0
        assert cal.std_overall == 0.0
        assert cal.avg_factuality == 0.0
        assert cal.std_consistency == 0.0
        assert cal.verdict_pass == 0


class TestFormatReviewCalibrationContext:

    def test_empty_calibration_returns_empty(self):
        cal = ReviewCalibration()
        result = format_review_calibration_context(cal)
        assert result == ""

    def test_with_stats_contains_all_dimensions(self):
        cal = ReviewCalibration(
            sample_count=30,
            avg_consistency=7.5,
            avg_factuality=7.3,
            avg_readability=6.8,
            avg_ai_cliche=7.2,
            avg_overall=7.1,
            std_overall=1.35,
            std_consistency=0.9,
            std_factuality=1.1,
            std_readability=0.8,
            std_ai_cliche=1.2,
            verdict_pass=20,
            verdict_rewrite=8,
            verdict_draft=2,
        )
        result = format_review_calibration_context(cal)
        assert "30" in result
        assert "7.5" in result
        assert "7.3" in result
        assert "6.8" in result
        assert "7.2" in result
        assert "7.1" in result
        assert "校准" in result
        # Per-dimension std
        assert "±0.9" in result
        assert "±1.1" in result
        assert "±0.8" in result
        assert "±1.2" in result
        # Verdict distribution
        assert "通过: 20" in result
        assert "重写: 8" in result
        assert "草稿: 2" in result

    def test_contains_guidance(self):
        cal = ReviewCalibration(
            sample_count=10,
            avg_overall=7.0,
            std_overall=1.0,
        )
        result = format_review_calibration_context(cal)
        assert "区分度" in result

    def test_inflation_warning_when_high(self):
        """avg_overall >= 8.0 时发出膨胀警告"""
        cal = ReviewCalibration(
            sample_count=20,
            avg_overall=8.5,
            std_overall=0.5,
        )
        result = format_review_calibration_context(cal)
        assert "评分膨胀警告" in result
        assert "8.5" in result

    def test_no_inflation_warning_when_normal(self):
        """avg_overall < 8.0 时无膨胀警告"""
        cal = ReviewCalibration(
            sample_count=20,
            avg_overall=7.0,
            std_overall=1.0,
        )
        result = format_review_calibration_context(cal)
        assert "评分膨胀警告" not in result

    def test_inflation_warning_with_self_review(self):
        """自审偏差 + 膨胀时措辞不重复压低指令"""
        cal = ReviewCalibration(
            sample_count=20,
            avg_overall=8.5,
            std_overall=0.5,
        )
        result = format_review_calibration_context(cal, is_self_review=True)
        assert "评分膨胀警告" in result
        assert "自审偏差补偿已在阈值层面生效" in result
        # 不应包含主动压低评分的指令（"无需额外压低评分"可以存在）
        assert "请有意识地压低评分" not in result

    def test_inflation_warning_without_self_review(self):
        """非自审时膨胀警告包含压低评分指令"""
        cal = ReviewCalibration(
            sample_count=20,
            avg_overall=8.5,
            std_overall=0.5,
        )
        result = format_review_calibration_context(cal, is_self_review=False)
        assert "评分膨胀警告" in result
        assert "压低评分" in result

    def test_verdict_distribution_percentages(self):
        """判定分布百分比正确"""
        cal = ReviewCalibration(
            sample_count=10,
            avg_overall=7.0,
            std_overall=1.0,
            verdict_pass=7,
            verdict_rewrite=2,
            verdict_draft=1,
        )
        result = format_review_calibration_context(cal)
        assert "70%" in result
        assert "20%" in result
        assert "10%" in result

    def test_verdict_total_used_not_sample_count(self):
        """百分比分母使用 verdict_total 而非 sample_count"""
        cal = ReviewCalibration(
            sample_count=100,  # 远大于 verdict 总和
            avg_overall=7.0,
            std_overall=1.0,
            verdict_pass=8,
            verdict_rewrite=2,
            verdict_draft=0,
        )
        result = format_review_calibration_context(cal)
        # verdict_total = 10, 所以 pass=80%, rewrite=20%
        # 如果错误地用 sample_count=100 作分母，会得到 8% 和 2%
        assert "80%" in result
        assert "20%" in result

    def test_verdict_all_zero_no_division_error(self):
        """所有 verdict 为 0 时不崩溃"""
        cal = ReviewCalibration(
            sample_count=10,
            avg_overall=7.0,
            std_overall=1.0,
            verdict_pass=0,
            verdict_rewrite=0,
            verdict_draft=0,
        )
        result = format_review_calibration_context(cal)
        assert "0%" in result


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
