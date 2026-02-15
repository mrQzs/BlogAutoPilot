"""测试 WordPress 发布模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError
from blog_autopilot.publisher import (
    ensure_wp_tags,
    post_to_wordpress,
    _get_tags_url,
)


@pytest.fixture
def wp_settings():
    return WordPressSettings(
        url="https://test.wp/wp-json/wp/v2/posts",
        user="testuser",
        app_password="testpass",
        target_category_id=15,
    )


class TestPostToWordpress:

    @patch("blog_autopilot.publisher.requests.post")
    def test_publish_success(self, mock_post, wp_settings):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "id": 42,
            "link": "https://test.wp/post-42",
        }
        mock_post.return_value = mock_resp

        link = post_to_wordpress(
            "Test Title", "<p>Content</p>", wp_settings
        )
        assert link == "https://test.wp/post-42"

    @patch("blog_autopilot.publisher.requests.post")
    def test_publish_4xx_raises(self, mock_post, wp_settings):
        import requests as req

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        http_err = req.exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        with pytest.raises(WordPressError, match="403"):
            post_to_wordpress(
                "Title", "<p>Body</p>", wp_settings
            )

    @patch("blog_autopilot.publisher.requests.post")
    def test_publish_with_seo_fields(self, mock_post, wp_settings):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "id": 99,
            "link": "https://test.wp/post-99",
        }
        mock_post.return_value = mock_resp

        link = post_to_wordpress(
            "SEO Title", "<p>Body</p>", wp_settings,
            excerpt="Test excerpt",
            slug="test-slug",
            tag_ids=[10, 20],
        )
        assert link == "https://test.wp/post-99"

        payload = mock_post.call_args[1]["json"]
        assert payload["excerpt"] == "Test excerpt"
        assert payload["slug"] == "test-slug"
        assert payload["tags"] == [10, 20]

    @patch("blog_autopilot.publisher.requests.post")
    def test_publish_without_seo_fields(self, mock_post, wp_settings):
        """SEO 字段为 None 时不应出现在 payload 中"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"id": 1, "link": "https://test.wp/p"}
        mock_post.return_value = mock_resp

        post_to_wordpress("Title", "<p>Body</p>", wp_settings)

        payload = mock_post.call_args[1]["json"]
        assert "excerpt" not in payload
        assert "slug" not in payload
        assert "tags" not in payload
        assert "featured_media" not in payload

    @patch("blog_autopilot.publisher.requests.post")
    def test_publish_with_featured_media(self, mock_post, wp_settings):
        """featured_media 参数应正确传入 payload"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"id": 55, "link": "https://test.wp/post-55"}
        mock_post.return_value = mock_resp

        link = post_to_wordpress(
            "Cover Title", "<p>Body</p>", wp_settings,
            featured_media=77,
        )
        assert link == "https://test.wp/post-55"

        payload = mock_post.call_args[1]["json"]
        assert payload["featured_media"] == 77


class TestGetTagsUrl:

    def test_pretty_permalink(self):
        url = _get_tags_url("https://test.wp/wp-json/wp/v2/posts")
        assert url == "https://test.wp/wp-json/wp/v2/tags"

    def test_rest_route_param(self):
        url = _get_tags_url("https://test.wp/?rest_route=/wp/v2/posts")
        assert "rest_route=%2Fwp%2Fv2%2Ftags" in url


class TestEnsureWPTags:

    @patch("blog_autopilot.publisher._create_or_get_wp_tag")
    def test_all_tags_created(self, mock_create, wp_settings):
        mock_create.side_effect = [10, 20, 30]
        ids = ensure_wp_tags(("标签1", "标签2", "标签3"), wp_settings)
        assert ids == [10, 20, 30]

    @patch("blog_autopilot.publisher._create_or_get_wp_tag")
    def test_existing_tag(self, mock_create, wp_settings):
        mock_create.side_effect = [10, 20, 30]
        ids = ensure_wp_tags(("新标签", "已有标签", "另一个"), wp_settings)
        assert len(ids) == 3

    @patch("blog_autopilot.publisher._create_or_get_wp_tag")
    def test_partial_failure(self, mock_create, wp_settings):
        mock_create.side_effect = [10, None, 30]
        ids = ensure_wp_tags(("标签1", "失败标签", "标签3"), wp_settings)
        assert ids == [10, 30]
