"""测试数据模型"""

import pytest

from blog_autopilot.models import (
    ArticleRecord,
    ArticleResult,
    CategoryMeta,
    ContentGap,
    FileTask,
    IngestionResult,
    PipelineResult,
    QualityIssue,
    QualityReview,
    SEOMetadata,
    SeriesInfo,
    SeriesRecord,
    TagSet,
    TokenUsage,
    TokenUsageSummary,
    TopicRecommendation,
)


class TestTagSet:

    def test_creation(self):
        tags = TagSet(
            tag_magazine="技术周刊",
            tag_science="AI应用",
            tag_topic="API开发",
            tag_content="Claude",
        )
        assert tags.tag_magazine == "技术周刊"

    def test_frozen(self):
        tags = TagSet(
            tag_magazine="a", tag_science="b",
            tag_topic="c", tag_content="d",
        )
        with pytest.raises(AttributeError):
            tags.tag_magazine = "new"


class TestArticleResult:

    def test_creation(self):
        r = ArticleResult(title="Test", html_body="<p>Body</p>")
        assert r.title == "Test"
        assert r.html_body == "<p>Body</p>"


class TestSEOMetadata:

    def test_creation(self):
        seo = SEOMetadata(
            meta_description="Test description",
            slug="test-slug",
            wp_tags=("tag1", "tag2"),
        )
        assert seo.slug == "test-slug"
        assert len(seo.wp_tags) == 2


class TestQualityReview:

    def test_creation(self):
        review = QualityReview(
            consistency_score=8,
            readability_score=7,
            ai_cliche_score=6,
            overall_score=7,
            verdict="pass",
            issues=(),
            summary="Good",
        )
        assert review.verdict == "pass"

    def test_with_issues(self):
        issue = QualityIssue(
            category="ai_cliche",
            severity="medium",
            description="套话",
            suggestion="改写",
        )
        review = QualityReview(
            consistency_score=5,
            readability_score=5,
            ai_cliche_score=3,
            overall_score=4,
            verdict="draft",
            issues=(issue,),
            summary="需要重写",
        )
        assert len(review.issues) == 1
        assert review.issues[0].category == "ai_cliche"


class TestPipelineResult:

    def test_success(self):
        r = PipelineResult(
            filename="test.pdf",
            success=True,
            title="Test",
            blog_link="https://blog/1",
        )
        assert r.success is True

    def test_failure(self):
        r = PipelineResult(
            filename="test.pdf",
            success=False,
            error="Failed",
        )
        assert r.success is False
        assert r.error == "Failed"


class TestTokenUsage:

    def test_creation(self):
        u = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            model="test-model",
            task="writer",
        )
        assert u.total_tokens == 150

    def test_mutable(self):
        u = TokenUsage()
        u.prompt_tokens = 100
        assert u.prompt_tokens == 100


class TestTokenUsageSummary:

    def test_empty(self):
        s = TokenUsageSummary()
        assert s.total_tokens == 0
        assert "无 API 调用" in s.summary_str()

    def test_accumulation(self):
        s = TokenUsageSummary()
        s.add(TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        s.add(TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300))
        assert s.total_prompt_tokens == 300
        assert s.total_completion_tokens == 150
        assert s.total_tokens == 450
        assert len(s.calls) == 2

    def test_summary_str(self):
        s = TokenUsageSummary()
        s.add(TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500))
        result = s.summary_str()
        assert "1,500" in result
        assert "1 次" in result


class TestSeriesInfo:

    def test_creation(self):
        info = SeriesInfo(
            series_id="s-001",
            series_title="测试系列",
            order=2,
            total=3,
            prev_article=None,
        )
        assert info.order == 2
        assert info.total == 3


class TestIngestionResult:

    def test_success(self):
        r = IngestionResult(
            article_id="a-001",
            title="Test",
            success=True,
        )
        assert r.success is True

    def test_failure(self):
        r = IngestionResult(
            article_id="a-002",
            title="Failed",
            success=False,
            error="DB error",
        )
        assert r.error == "DB error"
