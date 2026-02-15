"""测试 SEO 元数据提取与验证"""

import json

import pytest
from unittest.mock import patch

from blog_autopilot.ai_writer import (
    AIWriter,
    _parse_seo_response,
    _validate_seo_metadata,
)
from blog_autopilot.exceptions import (
    AIResponseParseError,
    SEOExtractionError,
)
from blog_autopilot.models import SEOMetadata


def _make_valid_seo_json(**overrides) -> str:
    data = {
        "meta_description": "这是一篇关于人工智能在医疗领域应用的深度分析文章，探讨了AI技术如何改变传统医疗诊断流程，提升诊断准确率和效率，为患者带来更好的医疗体验和治疗效果。",
        "slug": "ai-in-healthcare-diagnosis",
        "wp_tags": ["人工智能", "医疗诊断", "深度学习", "智慧医疗", "AI应用"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class TestParseSEOResponse:

    def test_pure_json(self):
        result = _parse_seo_response(_make_valid_seo_json())
        assert result["slug"] == "ai-in-healthcare-diagnosis"

    def test_markdown_code_block(self):
        text = f"```json\n{_make_valid_seo_json()}\n```"
        result = _parse_seo_response(text)
        assert result["slug"] == "ai-in-healthcare-diagnosis"

    def test_brace_extraction(self):
        text = f"以下是结果：\n{_make_valid_seo_json()}\n完成。"
        result = _parse_seo_response(text)
        assert "meta_description" in result

    def test_non_json_raises(self):
        with pytest.raises(AIResponseParseError, match="无法从 SEO 响应"):
            _parse_seo_response("这不是 JSON")

    def test_missing_fields_raises(self):
        text = json.dumps({"slug": "test"})
        with pytest.raises(AIResponseParseError, match="缺少必需字段"):
            _parse_seo_response(text)


class TestValidateSEOMetadata:

    def test_valid_data(self):
        data = json.loads(_make_valid_seo_json())
        seo = _validate_seo_metadata(data)
        assert isinstance(seo, SEOMetadata)
        assert seo.slug == "ai-in-healthcare-diagnosis"
        assert len(seo.wp_tags) == 5

    def test_empty_desc_rejected(self):
        data = json.loads(_make_valid_seo_json(meta_description=""))
        with pytest.raises(SEOExtractionError, match="不能为空"):
            _validate_seo_metadata(data)

    def test_long_desc_truncated(self):
        long_desc = "A" * 200
        data = json.loads(_make_valid_seo_json(meta_description=long_desc))
        seo = _validate_seo_metadata(data)
        assert len(seo.meta_description) == 160

    def test_slug_normalized(self):
        data = json.loads(_make_valid_seo_json(slug="Hello World! 你好"))
        seo = _validate_seo_metadata(data)
        assert seo.slug == "hello-world"

    def test_slug_empty_after_normalize_rejected(self):
        data = json.loads(_make_valid_seo_json(slug="你好世界"))
        with pytest.raises(SEOExtractionError, match="slug 规范化后为空"):
            _validate_seo_metadata(data)

    def test_tags_not_list_rejected(self):
        data = json.loads(_make_valid_seo_json(wp_tags="not a list"))
        with pytest.raises(SEOExtractionError, match="必须是数组"):
            _validate_seo_metadata(data)

    def test_tags_too_few_rejected(self):
        data = json.loads(_make_valid_seo_json(wp_tags=["标签1", "标签2"]))
        with pytest.raises(SEOExtractionError, match="数量不足"):
            _validate_seo_metadata(data)

    def test_tags_excess_trimmed(self):
        tags = [f"标签{i}" for i in range(12)]
        data = json.loads(_make_valid_seo_json(wp_tags=tags))
        seo = _validate_seo_metadata(data)
        assert len(seo.wp_tags) == 8

    def test_slug_long_truncated(self):
        data = json.loads(_make_valid_seo_json(slug="a" * 100))
        seo = _validate_seo_metadata(data)
        assert len(seo.slug) <= 75


class TestExtractSEOMetadata:

    def test_full_flow(self, ai_settings):
        writer = AIWriter(ai_settings)
        mock_response = _make_valid_seo_json()

        with patch.object(writer, "call_claude", return_value=mock_response):
            seo = writer.extract_seo_metadata("测试标题", "<p>文章内容</p>")

        assert isinstance(seo, SEOMetadata)
        assert seo.slug == "ai-in-healthcare-diagnosis"
        assert len(seo.wp_tags) >= 3

    def test_invalid_json_raises(self, ai_settings):
        writer = AIWriter(ai_settings)

        with patch.object(writer, "call_claude", return_value="not json"):
            with pytest.raises(AIResponseParseError):
                writer.extract_seo_metadata("标题", "<p>内容</p>")
