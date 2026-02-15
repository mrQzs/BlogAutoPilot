"""测试标签提取与验证"""

import json
import logging

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.ai_writer import (
    AIWriter,
    _parse_tagger_response,
    _log_link_coverage,
    normalize_tag,
    validate_tags,
    build_relation_context,
)
from blog_autopilot.exceptions import (
    AIResponseParseError,
    TagExtractionError,
)
from blog_autopilot.models import AssociationResult, ArticleRecord, TagSet


class TestParseTagResponse:
    """测试 JSON 解析的各种格式变体"""

    def _make_valid_json(self) -> str:
        return json.dumps({
            "title": "测试标题",
            "tag_magazine": "技术周刊",
            "tag_science": "AI应用",
            "tag_topic": "API开发",
            "tag_content": "自动化",
            "tg_promo": "这是推广文案",
        }, ensure_ascii=False)

    def test_pure_json(self):
        text = self._make_valid_json()
        result = _parse_tagger_response(text)
        assert result["title"] == "测试标题"
        assert result["tag_magazine"] == "技术周刊"

    def test_json_with_markdown_code_block(self):
        text = f"```json\n{self._make_valid_json()}\n```"
        result = _parse_tagger_response(text)
        assert result["title"] == "测试标题"

    def test_json_with_markdown_no_lang(self):
        text = f"```\n{self._make_valid_json()}\n```"
        result = _parse_tagger_response(text)
        assert result["title"] == "测试标题"

    def test_json_with_surrounding_text(self):
        text = f"以下是分析结果：\n{self._make_valid_json()}\n希望对您有帮助。"
        result = _parse_tagger_response(text)
        assert result["title"] == "测试标题"

    def test_non_json_text(self):
        with pytest.raises(AIResponseParseError, match="无法从 AI 响应中解析"):
            _parse_tagger_response("这不是 JSON 内容")

    def test_missing_required_fields(self):
        text = json.dumps({"title": "只有标题"})
        with pytest.raises(AIResponseParseError, match="缺少必需字段"):
            _parse_tagger_response(text)

    def test_missing_specific_fields(self):
        data = {
            "title": "标题",
            "tag_magazine": "周刊",
            # 缺少 tag_science, tag_topic, tag_content, tg_promo
        }
        with pytest.raises(AIResponseParseError) as exc_info:
            _parse_tagger_response(json.dumps(data))
        assert "tag_science" in str(exc_info.value)

    def test_empty_response(self):
        with pytest.raises(AIResponseParseError):
            _parse_tagger_response("")


class TestNormalizeTag:

    def test_strip_whitespace(self):
        assert normalize_tag("  技术周刊  ") == "技术周刊"

    def test_merge_spaces(self):
        assert normalize_tag("AI   应用") == "AI 应用"

    def test_fullwidth_space(self):
        assert normalize_tag("AI\u3000应用") == "AI 应用"

    def test_normal_tag(self):
        assert normalize_tag("技术周刊") == "技术周刊"


class TestValidateTags:

    def test_valid_tags(self, sample_tags):
        result = validate_tags(sample_tags)
        assert result.tag_magazine == "技术周刊"

    def test_empty_tag_rejected(self):
        tags = TagSet(
            tag_magazine="",
            tag_science="AI",
            tag_topic="测试",
            tag_content="内容",
        )
        with pytest.raises(TagExtractionError, match="不能为空"):
            validate_tags(tags)

    def test_whitespace_only_tag_rejected(self):
        tags = TagSet(
            tag_magazine="   ",
            tag_science="AI",
            tag_topic="测试",
            tag_content="内容",
        )
        with pytest.raises(TagExtractionError, match="不能为空"):
            validate_tags(tags)

    def test_overlong_tag_rejected(self):
        tags = TagSet(
            tag_magazine="A" * 51,  # 超过 TAG_MAX_LENGTH=50
            tag_science="AI",
            tag_topic="测试",
            tag_content="内容",
        )
        with pytest.raises(TagExtractionError, match="超长"):
            validate_tags(tags)

    def test_overlong_content_tag(self):
        tags = TagSet(
            tag_magazine="技术周刊",
            tag_science="AI",
            tag_topic="测试",
            tag_content="A" * 101,  # 超过 TAG_CONTENT_MAX_LENGTH=100
        )
        with pytest.raises(TagExtractionError, match="超长"):
            validate_tags(tags)

    def test_tags_get_normalized(self):
        tags = TagSet(
            tag_magazine="  技术周刊  ",
            tag_science="AI   应用",
            tag_topic="API开发",
            tag_content="Claude自动化",
        )
        result = validate_tags(tags)
        assert result.tag_magazine == "技术周刊"
        assert result.tag_science == "AI 应用"


class TestExtractTagsAndPromo:

    def test_extract_success(self, ai_settings):
        writer = AIWriter(ai_settings)

        mock_response = json.dumps({
            "title": "测试标题",
            "tag_magazine": "技术周刊",
            "tag_science": "AI应用",
            "tag_topic": "API开发",
            "tag_content": "自动化",
            "tg_promo": "A" * 180,  # 满足长度要求
        }, ensure_ascii=False)

        with patch.object(writer, "call_claude", return_value=mock_response):
            tags, promo, title = writer.extract_tags_and_promo("一些文章内容" * 100)

            assert tags.tag_magazine == "技术周刊"
            assert tags.tag_science == "AI应用"
            assert len(promo) == 180
            assert title == "测试标题"

    def test_extract_invalid_json(self, ai_settings):
        writer = AIWriter(ai_settings)

        with patch.object(writer, "call_claude", return_value="not json"):
            with pytest.raises(AIResponseParseError):
                writer.extract_tags_and_promo("文章内容")


class TestBuildRelationContext:

    def test_context_grouping(self):
        articles = []
        for level, count in [("强关联", 4), ("中关联", 3), ("弱关联", 2)]:
            articles.append(AssociationResult(
                article=ArticleRecord(
                    id=f"art-{level}",
                    title=f"{level}文章",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo=f"{level}的推广文案",
                ),
                tag_match_count=count,
                relation_level=level,
                similarity=0.9,
            ))

        context = build_relation_context(articles)
        assert "强关联文章" in context["strong_relations"]
        assert "中关联文章" in context["medium_relations"]
        assert "弱关联文章" in context["weak_relations"]

    def test_context_empty(self):
        context = build_relation_context([])
        assert context["strong_relations"] == ""
        assert context["medium_relations"] == ""
        assert context["weak_relations"] == ""

    def test_context_partial(self):
        """只有中关联，其他为空"""
        articles = [AssociationResult(
            article=ArticleRecord(
                id="art-1",
                title="中关联文章",
                tags=TagSet("周刊", "AI", "测试", "内容"),
                tg_promo="推广文案",
            ),
            tag_match_count=3,
            relation_level="中关联",
            similarity=0.8,
        )]

        context = build_relation_context(articles)
        assert context["strong_relations"] == ""
        assert "中关联文章" in context["medium_relations"]
        assert context["weak_relations"] == ""

    def test_context_includes_url(self):
        """有 URL 时上下文包含「链接:」行"""
        articles = [AssociationResult(
            article=ArticleRecord(
                id="art-1",
                title="有链接的文章",
                tags=TagSet("周刊", "AI", "测试", "内容"),
                tg_promo="推广文案",
                url="https://blog.example.com/post-1",
            ),
            tag_match_count=4,
            relation_level="强关联",
            similarity=0.9,
        )]

        context = build_relation_context(articles)
        assert "链接: https://blog.example.com/post-1" in context["strong_relations"]
        assert "有链接的文章" in context["strong_relations"]

    def test_context_omits_url_when_none(self):
        """URL 为 None 时不出现「链接:」行"""
        articles = [AssociationResult(
            article=ArticleRecord(
                id="art-1",
                title="无链接文章",
                tags=TagSet("周刊", "AI", "测试", "内容"),
                tg_promo="推广文案",
                url=None,
            ),
            tag_match_count=4,
            relation_level="强关联",
            similarity=0.9,
        )]

        context = build_relation_context(articles)
        assert "链接:" not in context["strong_relations"]
        assert "无链接文章" in context["strong_relations"]

    def test_context_mixed_url(self):
        """混合场景：一篇有 URL，一篇无 URL"""
        articles = [
            AssociationResult(
                article=ArticleRecord(
                    id="art-1",
                    title="有链接",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="推广1",
                    url="https://blog.example.com/post-1",
                ),
                tag_match_count=4,
                relation_level="强关联",
                similarity=0.9,
            ),
            AssociationResult(
                article=ArticleRecord(
                    id="art-2",
                    title="无链接",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="推广2",
                    url=None,
                ),
                tag_match_count=4,
                relation_level="强关联",
                similarity=0.85,
            ),
        ]

        context = build_relation_context(articles)
        text = context["strong_relations"]
        assert "链接: https://blog.example.com/post-1" in text
        assert "有链接" in text
        assert "无链接" in text
        # 第二篇无 URL，不应出现第二个「链接:」
        assert text.count("链接:") == 1


class TestLogLinkCoverage:

    def test_logs_coverage_count(self, caplog):
        """验证有内链时记录覆盖数"""
        associations = [
            AssociationResult(
                article=ArticleRecord(
                    id="art-1",
                    title="文章A",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="推广",
                    url="https://blog.example.com/a",
                ),
                tag_match_count=4,
                relation_level="强关联",
                similarity=0.9,
            ),
            AssociationResult(
                article=ArticleRecord(
                    id="art-2",
                    title="文章B",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="推广",
                    url="https://blog.example.com/b",
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.8,
            ),
        ]
        html = '<p>参见<a href="https://blog.example.com/a">《文章A》</a></p>'

        with caplog.at_level(logging.INFO, logger="blog-autopilot"):
            _log_link_coverage(html, associations)

        assert "内链覆盖: 1/2" in caplog.text

    def test_warns_when_no_links_generated(self, caplog):
        """有 >=2 篇可链接文章但 AI 未生成任何内链时发出警告"""
        associations = [
            AssociationResult(
                article=ArticleRecord(
                    id=f"art-{i}",
                    title=f"文章{i}",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="推广",
                    url=f"https://blog.example.com/{i}",
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.8,
            )
            for i in range(3)
        ]
        html = "<p>没有任何内链的文章</p>"

        with caplog.at_level(logging.WARNING, logger="blog-autopilot"):
            _log_link_coverage(html, associations)

        assert "AI 未生成任何内链" in caplog.text

    def test_skips_when_no_linkable(self, caplog):
        """所有文章都没有 URL 时不输出日志"""
        associations = [AssociationResult(
            article=ArticleRecord(
                id="art-1",
                title="无链接",
                tags=TagSet("周刊", "AI", "测试", "内容"),
                tg_promo="推广",
                url=None,
            ),
            tag_match_count=3,
            relation_level="中关联",
            similarity=0.8,
        )]

        with caplog.at_level(logging.DEBUG, logger="blog-autopilot"):
            _log_link_coverage("<p>内容</p>", associations)

        assert "内链覆盖" not in caplog.text
