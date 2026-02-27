"""测试智能摘要增强关联上下文功能"""

from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.ai_writer import build_relation_context
from blog_autopilot.models import ArticleRecord, AssociationResult, TagSet


SAMPLE_TAGS = TagSet("科技", "AI", "NLP", "GPT")


class TestBuildRelationContextWithSummary:
    """build_relation_context 优先使用 summary"""

    def test_prefers_summary_over_tg_promo(self):
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="文章A", tags=SAMPLE_TAGS,
                    tg_promo="推广文案A",
                    summary="这是结构化摘要A",
                    url="https://example.com/a",
                ),
                tag_match_count=3,
                relation_level="强关联",
                similarity=0.9,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "这是结构化摘要A" in ctx["strong_relations"]
        assert "推广文案A" not in ctx["strong_relations"]

    def test_falls_back_to_tg_promo_when_no_summary(self):
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a2", title="文章B", tags=SAMPLE_TAGS,
                    tg_promo="推广文案B",
                    summary=None,
                ),
                tag_match_count=2,
                relation_level="弱关联",
                similarity=0.6,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "推广文案B" in ctx["weak_relations"]

    def test_mixed_summary_and_fallback(self):
        """混合场景：部分有摘要，部分回退到 tg_promo"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="有摘要", tags=SAMPLE_TAGS,
                    tg_promo="推广1", summary="摘要内容1",
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.8,
            ),
            AssociationResult(
                article=ArticleRecord(
                    id="a2", title="无摘要", tags=SAMPLE_TAGS,
                    tg_promo="推广2", summary=None,
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.7,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "摘要内容1" in ctx["medium_relations"]
        assert "推广2" in ctx["medium_relations"]


class TestArticleRecordSummary:
    """ArticleRecord 的 summary 字段"""

    def test_default_none(self):
        record = ArticleRecord(
            id="x", title="T", tags=SAMPLE_TAGS, tg_promo="promo",
        )
        assert record.summary is None

    def test_with_summary(self):
        record = ArticleRecord(
            id="x", title="T", tags=SAMPLE_TAGS, tg_promo="promo",
            summary="结构化摘要",
        )
        assert record.summary == "结构化摘要"
