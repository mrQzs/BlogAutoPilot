"""测试标签提取与验证"""

import json

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.ai_writer import (
    AIWriter,
    _parse_tagger_response,
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
