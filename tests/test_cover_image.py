"""测试封面图生成与上传模块"""

import base64

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import AISettings, WordPressSettings
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

        mock_image = MagicMock()
        mock_image.b64_json = b64
        mock_resp = MagicMock()
        mock_resp.data = [mock_image]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_resp
        generator._client = mock_client

        result = generator.generate_image("测试标题", "<p>文章内容</p>")
        assert result == b"generated-png"

        # 验证调用参数
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs["model"] == "dall-e-3"
        assert call_kwargs["response_format"] == "b64_json"

    def test_generate_image_empty_b64_raises(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)

        mock_image = MagicMock()
        mock_image.b64_json = None
        mock_resp = MagicMock()
        mock_resp.data = [mock_image]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_resp
        generator._client = mock_client

        with pytest.raises(CoverImageError, match="未包含图片"):
            generator.generate_image("标题", "<p>内容</p>")

    def test_generate_image_api_error_raises(self, ai_settings):
        generator = CoverImageGenerator(ai_settings)

        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception("API down")
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
