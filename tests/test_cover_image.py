"""测试封面图生成与上传模块"""

import base64

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import AISettings, WordPressSettings
from blog_autopilot.constants import CATEGORY_COVER_STYLE, DEFAULT_COVER_STYLE
from blog_autopilot.cover_image import (
    CoverImageGenerator,
    _get_media_url,
    upload_media_to_wordpress,
)
from blog_autopilot.exceptions import CoverImageError


# ── fixtures ──

@pytest.fixture
def wp_settings():
    return WordPressSettings(
        url="https://test.wp/wp-json/wp/v2/posts",
        user="testuser",
        app_password="testpass",
        target_category_id=15,
    )


@pytest.fixture
def sample_image_bytes():
    return b"\x89PNG\r\n\x1a\nfake-image-data"


# ── _get_media_url ──

class TestGetMediaUrl:

    def test_pretty_permalink(self):
        url = _get_media_url("https://test.wp/wp-json/wp/v2/posts")
        assert url == "https://test.wp/wp-json/wp/v2/media"

    def test_rest_route_param(self):
        url = _get_media_url("https://test.wp/?rest_route=/wp/v2/posts")
        assert "rest_route=%2Fwp%2Fv2%2Fmedia" in url


# ── upload_media_to_wordpress ──

class TestUploadMedia:

    @patch("blog_autopilot.cover_image.requests.post")
    def test_upload_success(self, mock_post, wp_settings, sample_image_bytes):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": 77}
        mock_post.return_value = mock_resp

        media_id = upload_media_to_wordpress(
            sample_image_bytes, "cover.png", wp_settings
        )
        assert media_id == 77

    @patch("blog_autopilot.cover_image.requests.post")
    def test_upload_4xx_raises(self, mock_post, wp_settings, sample_image_bytes):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        with pytest.raises(CoverImageError, match="403"):
            upload_media_to_wordpress(
                sample_image_bytes, "cover.png", wp_settings
            )

    @patch("blog_autopilot.cover_image.requests.post")
    def test_upload_connection_error(self, mock_post, wp_settings, sample_image_bytes):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("timeout")

        with pytest.raises(CoverImageError, match="请求异常"):
            upload_media_to_wordpress(
                sample_image_bytes, "cover.png", wp_settings
            )


# ── CoverImageGenerator ──

class TestCoverImageGenerator:

    def test_generate_image_success(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)
        b64 = base64.b64encode(b"generated-png").decode()

        mock_msg = MagicMock()
        mock_msg.content = f"![image](data:image/png;base64,{b64})"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        generator._client = mock_client

        result = generator.generate_image("测试标题", "<p>文章内容</p>")
        assert result == b"generated-png"

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "dall-e-3"

    def test_generate_image_empty_b64_raises(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)

        mock_msg = MagicMock()
        mock_msg.content = "No image here, just text."
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        generator._client = mock_client

        with pytest.raises(CoverImageError, match="未包含图片"):
            generator.generate_image("标题", "<p>内容</p>")

    def test_generate_image_api_error_raises(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        generator._client = mock_client

        with pytest.raises(CoverImageError, match="封面图生成失败"):
            generator.generate_image("标题", "<p>内容</p>")

    def test_client_uses_cover_image_api_key(self):
        """专属 API key 优先于通用 key"""
        settings = AISettings(
            api_key="general-key",
            api_base="https://general.api/v1",
            cover_image_api_key="cover-key",
            cover_image_api_base="https://cover.api/v1",
        )
        generator = CoverImageGenerator(settings)

        with patch("blog_autopilot.cover_image.OpenAI") as mock_cls:
            generator._get_client()
            mock_cls.assert_called_once()
            assert mock_cls.call_args[1]["api_key"] == "cover-key"
            assert mock_cls.call_args[1]["base_url"] == "https://cover.api/v1"

    def test_client_falls_back_to_general_key(self):
        """无专属 key 时回退到通用 key"""
        settings = AISettings(
            api_key="general-key",
            api_base="https://general.api/v1",
        )
        generator = CoverImageGenerator(settings)

        with patch("blog_autopilot.cover_image.OpenAI") as mock_cls:
            generator._get_client()
            assert mock_cls.call_args[1]["api_key"] == "general-key"


# ── Category Style Differentiation ──


class TestCoverImageCategoryStyle:

    def _make_generator(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)
        b64 = base64.b64encode(b"png-data").decode()
        mock_msg = MagicMock()
        mock_msg.content = f"![image](data:image/png;base64,{b64})"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        generator._client = mock_client
        return generator, mock_client

    def test_news_style_in_prompt(self, ai_settings):
        """News 分类使用 journalistic 风格"""
        gen, client = self._make_generator(ai_settings)
        gen.generate_image("标题", "<p>内容</p>", category_name="News")
        prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "journalistic" in prompt

    def test_paper_style_in_prompt(self, ai_settings):
        """Paper 分类使用 scientific 风格"""
        gen, client = self._make_generator(ai_settings)
        gen.generate_image("标题", "<p>内容</p>", category_name="Paper")
        prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "scientific" in prompt

    def test_books_style_in_prompt(self, ai_settings):
        """Books 分类使用 literary 风格"""
        gen, client = self._make_generator(ai_settings)
        gen.generate_image("标题", "<p>内容</p>", category_name="Books")
        prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "literary" in prompt

    def test_no_category_uses_default(self, ai_settings):
        """无分类时使用默认风格"""
        gen, client = self._make_generator(ai_settings)
        gen.generate_image("标题", "<p>内容</p>")
        prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "minimalist" in prompt

    def test_unknown_category_uses_default(self, ai_settings):
        """未知分类回退到默认风格"""
        gen, client = self._make_generator(ai_settings)
        gen.generate_image("标题", "<p>内容</p>", category_name="Unknown")
        prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
        assert "minimalist" in prompt

    def test_all_categories_have_styles(self):
        """所有允许的大类都有对应风格"""
        from blog_autopilot.constants import ALLOWED_CATEGORIES
        for cat in ALLOWED_CATEGORIES:
            assert cat in CATEGORY_COVER_STYLE, f"{cat} missing from CATEGORY_COVER_STYLE"


# ── Fallback API ──


class TestCoverImageFallback:

    def _make_fallback_settings(self):
        return AISettings(
            api_key="main-key",
            api_base="https://main.api/v1",
            cover_image_api_key="cover-key",
            cover_image_api_base="https://cover.api/v1",
            cover_image_fallback_api_key="fallback-key",
            cover_image_fallback_api_base="https://fallback.api/v1",
            model_cover_image_fallback="dall-e-fallback",
        )

    def test_primary_fails_fallback_succeeds(self):
        """主 API 失败后备用 API 成功返回图片"""
        settings = self._make_fallback_settings()
        generator = CoverImageGenerator(settings)

        # 主 client 始终失败
        mock_primary = MagicMock()
        mock_primary.chat.completions.create.side_effect = Exception("primary down")
        generator._client = mock_primary

        # 备用 client 成功
        b64 = base64.b64encode(b"fallback-png").decode()
        mock_image = MagicMock()
        mock_image.b64_json = b64
        mock_resp = MagicMock()
        mock_resp.data = [mock_image]
        mock_fallback = MagicMock()
        mock_fallback.images.generate.return_value = mock_resp
        generator._fallback_client = mock_fallback

        result = generator.generate_image("标题", "<p>内容</p>")
        assert result == b"fallback-png"
        # 验证备用 client 使用了 fallback 模型
        call_kwargs = mock_fallback.images.generate.call_args[1]
        assert call_kwargs["model"] == "dall-e-fallback"

    def test_primary_fails_fallback_also_fails(self):
        """主 API 和备用 API 都失败时抛出异常"""
        settings = self._make_fallback_settings()
        generator = CoverImageGenerator(settings)

        mock_primary = MagicMock()
        mock_primary.chat.completions.create.side_effect = Exception("primary down")
        generator._client = mock_primary

        mock_fallback = MagicMock()
        mock_fallback.images.generate.side_effect = Exception("fallback down")
        generator._fallback_client = mock_fallback

        with pytest.raises(CoverImageError, match="备用封面图 API 也失败"):
            generator.generate_image("标题", "<p>内容</p>")

    def test_no_fallback_configured_raises_directly(self, ai_settings):
        """未配置 fallback 时主 API 失败直接抛异常"""
        generator = CoverImageGenerator(ai_settings)

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        generator._client = mock_client

        with pytest.raises(CoverImageError, match="封面图生成失败"):
            generator.generate_image("标题", "<p>内容</p>")

    def test_fallback_uses_primary_model_when_no_fallback_model(self):
        """未配置 fallback 模型时沿用主模型"""
        settings = AISettings(
            api_key="main-key",
            api_base="https://main.api/v1",
            cover_image_fallback_api_key="fb-key",
            cover_image_fallback_api_base="https://fb.api/v1",
            model_cover_image_fallback="",
        )
        generator = CoverImageGenerator(settings)

        mock_primary = MagicMock()
        mock_primary.chat.completions.create.side_effect = Exception("down")
        generator._client = mock_primary

        b64 = base64.b64encode(b"fb-png").decode()
        mock_image = MagicMock()
        mock_image.b64_json = b64
        mock_resp = MagicMock()
        mock_resp.data = [mock_image]
        mock_fb = MagicMock()
        mock_fb.images.generate.return_value = mock_resp
        generator._fallback_client = mock_fb

        generator.generate_image("标题", "<p>内容</p>")
        call_kwargs = mock_fb.images.generate.call_args[1]
        assert call_kwargs["model"] == "dall-e-3"

    def test_has_fallback_property(self):
        """_has_fallback 正确反映配置状态"""
        no_fb = AISettings(
            api_key="k", api_base="https://a.com/v1",
        )
        assert CoverImageGenerator(no_fb)._has_fallback is False

        with_fb = AISettings(
            api_key="k", api_base="https://a.com/v1",
            cover_image_fallback_api_key="fb",
            cover_image_fallback_api_base="https://fb.com/v1",
        )
        assert CoverImageGenerator(with_fb)._has_fallback is True
