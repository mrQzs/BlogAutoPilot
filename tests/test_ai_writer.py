"""测试 AI 写作模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import AISettings
from blog_autopilot.exceptions import AIAPIError, AIResponseParseError
from blog_autopilot.models import AssociationResult, ArticleRecord, TagSet


@pytest.fixture
def ai_settings():
    return AISettings(
        api_key="test-key",
        api_base="https://test.api/v1",
    )


@pytest.fixture
def mock_openai_response():
    """构造一个 mock 的 OpenAI API 响应"""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = (
        "测试标题\n<h2>章节一</h2>\n<p>正文内容</p>"
    )
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    return mock_resp


class TestAIWriter:

    def test_lazy_client_init(self, ai_settings):
        writer = AIWriter(ai_settings)
        assert writer._client is None

    def test_generate_blog_post_success(
        self, ai_settings, mock_openai_response
    ):
        writer = AIWriter(ai_settings)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_openai_response
        writer._client = mock_client

        result = writer.generate_blog_post("一些原始文本内容" * 100)

        assert result.title == "测试标题"
        assert "<h2>章节一</h2>" in result.html_body

    def test_generate_blog_post_empty_response(self, ai_settings):
        writer = AIWriter(ai_settings)
        mock_client = MagicMock()

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = ""
        mock_resp.usage.prompt_tokens = 10
        mock_resp.usage.completion_tokens = 0
        mock_client.chat.completions.create.return_value = mock_resp
        writer._client = mock_client

        with pytest.raises(AIResponseParseError, match="为空"):
            writer.generate_blog_post("测试文本" * 50)


class TestGenerateBlogPostWithContext:

    def test_no_associations_fallback(self, ai_settings, mock_openai_response):
        """无关联文章时回退到原有生成"""
        writer = AIWriter(ai_settings)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_openai_response
        writer._client = mock_client

        result = writer.generate_blog_post_with_context(
            "原始文本" * 100, associations=None
        )

        assert result.title == "测试标题"

    def test_empty_associations_fallback(
        self, ai_settings, mock_openai_response
    ):
        """空关联列表时回退到原有生成"""
        writer = AIWriter(ai_settings)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_openai_response
        writer._client = mock_client

        result = writer.generate_blog_post_with_context(
            "原始文本" * 100, associations=[]
        )

        assert result.title == "测试标题"

    def test_with_associations(self, ai_settings):
        """有关联文章时使用增强模板"""
        writer = AIWriter(ai_settings)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            "增强标题\n<h2>章节</h2>\n<p>引用了关联文章</p>"
        )
        mock_resp.usage.prompt_tokens = 200
        mock_resp.usage.completion_tokens = 100

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        writer._client = mock_client

        associations = [AssociationResult(
            article=ArticleRecord(
                id="rel-1",
                title="关联文章",
                tags=TagSet("周刊", "AI", "测试", "内容"),
                tg_promo="关联推广文案",
            ),
            tag_match_count=3,
            relation_level="中关联",
            similarity=0.85,
        )]

        result = writer.generate_blog_post_with_context(
            "原始文本" * 100, associations=associations
        )

        assert result.title == "增强标题"
