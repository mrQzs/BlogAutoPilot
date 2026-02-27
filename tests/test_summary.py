"""测试智能摘要增强关联上下文功能（含三级回退）"""

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
        assert "[摘要] 这是结构化摘要A" in ctx["strong_relations"]
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
        assert "[推广] 推广文案B" in ctx["weak_relations"]

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
        assert "[摘要] 摘要内容1" in ctx["medium_relations"]
        assert "[推广] 推广2" in ctx["medium_relations"]


class TestThreeLevelFallback:
    """三级回退：summary → content_excerpt → tg_promo"""

    def test_content_excerpt_used_when_no_summary(self):
        """无 summary 但有 content_excerpt → 使用 [摘录]"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="文章", tags=SAMPLE_TAGS,
                    tg_promo="推广文案",
                    summary=None,
                    content_excerpt="这是正文的前500字摘录",
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.8,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[摘录] 这是正文的前500字摘录" in ctx["medium_relations"]
        assert "[推广]" not in ctx["medium_relations"]
        assert "[摘要]" not in ctx["medium_relations"]

    def test_summary_preferred_over_content_excerpt(self):
        """三者都有 → 优先 [摘要]"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="文章", tags=SAMPLE_TAGS,
                    tg_promo="推广文案",
                    summary="结构化摘要",
                    content_excerpt="正文摘录",
                ),
                tag_match_count=4,
                relation_level="强关联",
                similarity=0.95,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[摘要] 结构化摘要" in ctx["strong_relations"]
        assert "正文摘录" not in ctx["strong_relations"]
        assert "[推广]" not in ctx["strong_relations"]

    def test_tg_promo_fallback_when_both_missing(self):
        """summary 和 content_excerpt 都为 None → 使用 [推广]"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="文章", tags=SAMPLE_TAGS,
                    tg_promo="推广兜底内容",
                    summary=None,
                    content_excerpt=None,
                ),
                tag_match_count=2,
                relation_level="弱关联",
                similarity=0.6,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[推广] 推广兜底内容" in ctx["weak_relations"]

    def test_empty_string_content_excerpt_falls_through(self):
        """content_excerpt 为空字符串 → 视为 falsy，回退到 tg_promo"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="文章", tags=SAMPLE_TAGS,
                    tg_promo="推广文案",
                    summary=None,
                    content_excerpt="",
                ),
                tag_match_count=2,
                relation_level="弱关联",
                similarity=0.6,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[推广] 推广文案" in ctx["weak_relations"]

    def test_tg_promo_none_falls_to_title(self):
        """tg_promo 为 None 时回退到标题，不崩溃"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="兜底标题", tags=SAMPLE_TAGS,
                    tg_promo=None,
                    summary=None,
                    content_excerpt=None,
                ),
                tag_match_count=2,
                relation_level="弱关联",
                similarity=0.6,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[标题] 兜底标题" in ctx["weak_relations"]

    def test_all_fields_none_except_tg_promo_empty_string(self):
        """tg_promo 为空字符串（falsy）时回退到标题"""
        assocs = [
            AssociationResult(
                article=ArticleRecord(
                    id="a1", title="兜底标题2", tags=SAMPLE_TAGS,
                    tg_promo="",
                    summary=None,
                    content_excerpt=None,
                ),
                tag_match_count=2,
                relation_level="弱关联",
                similarity=0.6,
            ),
        ]
        ctx = build_relation_context(assocs)
        assert "[标题] 兜底标题2" in ctx["weak_relations"]


class TestArticleRecordSummary:
    """ArticleRecord 的 summary / content_excerpt 字段"""

    def test_default_none(self):
        record = ArticleRecord(
            id="x", title="T", tags=SAMPLE_TAGS, tg_promo="promo",
        )
        assert record.summary is None
        assert record.content_excerpt is None

    def test_with_summary(self):
        record = ArticleRecord(
            id="x", title="T", tags=SAMPLE_TAGS, tg_promo="promo",
            summary="结构化摘要",
        )
        assert record.summary == "结构化摘要"

    def test_with_content_excerpt(self):
        record = ArticleRecord(
            id="x", title="T", tags=SAMPLE_TAGS, tg_promo="promo",
            content_excerpt="正文摘录",
        )
        assert record.content_excerpt == "正文摘录"
