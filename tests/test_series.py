"""文章系列检测模块单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.constants import SERIES_NAV_CSS_CLASS
from blog_autopilot.models import ArticleRecord, SeriesInfo, TagSet
from blog_autopilot.series import (
    _cosine_similarity,
    build_backfill_navigation,
    build_series_navigation,
    has_series_title_pattern,
    inject_series_navigation,
    replace_series_navigation,
)


# ── 标题模式检测 ──


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-9

    def test_opposite_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0
        assert _cosine_similarity(b, a) == 0.0

    def test_near_zero_vector_returns_zero(self):
        a = [1e-12, 1e-12, 1e-12]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_result_clamped_to_valid_range(self):
        """结果应始终在 [-1, 1] 范围内"""
        a = [1.0] * 3072
        b = [1.0] * 3072
        sim = _cosine_similarity(a, b)
        assert -1.0 <= sim <= 1.0

    def test_high_dimensional_stability(self):
        """3072 维向量的数值稳定性"""
        import math
        a = [math.sin(i) for i in range(3072)]
        b = [math.cos(i) for i in range(3072)]
        sim = _cosine_similarity(a, b)
        assert -1.0 <= sim <= 1.0


class TestHasSeriesTitlePattern:
    @pytest.mark.parametrize("title", [
        "深度学习 Part 3",
        "深度学习 Part3",
        "深度学习 part 10",
        "第一篇：入门指南",
        "第三章：高级技巧",
        "量子计算（上）",
        "量子计算(下)",
        "NLP 系列总结",
        "连载：AI 前沿",
        "Machine Learning Series",
        "技术周刊(3)",
        "技术周刊（12）",
    ])
    def test_matches_series_patterns(self, title):
        assert has_series_title_pattern(title) is True

    @pytest.mark.parametrize("title", [
        "普通文章标题",
        "如何学习 Python",
        "2024 年度总结",
        "PostgreSQL 性能优化指南",
    ])
    def test_no_match_for_regular_titles(self, title):
        assert has_series_title_pattern(title) is False


# ── 导航 HTML 生成 ──


@pytest.fixture
def sample_prev_article():
    return ArticleRecord(
        id="prev-001",
        title="系列第一篇",
        tags=TagSet("M", "S", "T", "C"),
        tg_promo="promo",
        url="https://blog.example.com/series-1",
    )


class TestBuildSeriesNavigation:
    def test_with_prev_article(self, sample_prev_article):
        info = SeriesInfo(
            series_id="s-001",
            series_title="AI 入门系列",
            order=2,
            total=2,
            prev_article=sample_prev_article,
        )
        html = build_series_navigation(info)

        assert SERIES_NAV_CSS_CLASS in html
        assert "AI 入门系列" in html
        assert "第 2/2 篇" in html
        assert "系列第一篇" in html
        assert "https://blog.example.com/series-1" in html
        assert "上一篇" in html

    def test_without_prev_article(self):
        info = SeriesInfo(
            series_id="s-001",
            series_title="AI 入门系列",
            order=1,
            total=1,
            prev_article=None,
        )
        html = build_series_navigation(info)

        assert SERIES_NAV_CSS_CLASS in html
        assert "AI 入门系列" in html
        assert "第 1/1 篇" in html
        assert "上一篇" not in html

    def test_prev_article_without_url(self):
        prev = ArticleRecord(
            id="prev-001", title="无链接文章",
            tags=TagSet("M", "S", "T", "C"),
            tg_promo="promo", url=None,
        )
        info = SeriesInfo(
            series_id="s-001", series_title="测试系列",
            order=2, total=2, prev_article=prev,
        )
        html = build_series_navigation(info)
        assert "上一篇" not in html


class TestInjectSeriesNavigation:
    def test_appends_to_body(self, sample_prev_article):
        body = "<p>文章内容</p>"
        info = SeriesInfo(
            series_id="s-001", series_title="测试系列",
            order=2, total=2, prev_article=sample_prev_article,
        )
        result = inject_series_navigation(body, info)

        assert result.startswith("<p>文章内容</p>")
        assert SERIES_NAV_CSS_CLASS in result


# ── 导航替换 ──


class TestReplaceSeriesNavigation:
    def test_replaces_existing_nav(self):
        old_nav = (
            f'<div class="{SERIES_NAV_CSS_CLASS}" style="margin:2em 0;">'
            '<p>旧导航</p><div>旧链接</div></div>'
        )
        content = f"<p>正文</p>\n\n{old_nav}"
        new_nav = f'<div class="{SERIES_NAV_CSS_CLASS}" style="margin:2em 0;"><p>新导航</p><div>新链接</div></div>'

        result = replace_series_navigation(content, new_nav)

        assert "旧导航" not in result
        assert "新导航" in result
        assert "<p>正文</p>" in result

    def test_appends_when_no_existing_nav(self):
        content = "<p>正文内容</p>"
        new_nav = f'<div class="{SERIES_NAV_CSS_CLASS}"><p>导航</p><div></div></div>'

        result = replace_series_navigation(content, new_nav)

        assert "<p>正文内容</p>" in result
        assert SERIES_NAV_CSS_CLASS in result


# ── 回溯导航生成 ──


class TestBuildBackfillNavigation:
    def test_with_both_links(self, sample_prev_article):
        html = build_backfill_navigation(
            series_title="AI 系列",
            order=2,
            total=3,
            prev_article=sample_prev_article,
            next_article_title="系列第三篇",
            next_article_url="https://blog.example.com/series-3",
        )

        assert "AI 系列" in html
        assert "第 2/3 篇" in html
        assert "上一篇" in html
        assert "系列第一篇" in html
        assert "下一篇" in html
        assert "系列第三篇" in html

    def test_without_prev(self):
        html = build_backfill_navigation(
            series_title="AI 系列",
            order=1,
            total=2,
            prev_article=None,
            next_article_title="第二篇",
            next_article_url="https://blog.example.com/2",
        )

        assert "上一篇" not in html
        assert "下一篇" in html
        assert "第二篇" in html
