"""测试 WordPress 发布模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError
from blog_autopilot.publisher import (
    _get_categories_url,
    _get_tags_url,
    ensure_wp_tags,
    post_to_wordpress,
    sync_wp_taxonomy_mappings,
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
        assert link.url == "https://test.wp/post-42"
        assert link.post_id == 42

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
        assert link.url == "https://test.wp/post-99"

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
        assert link.url == "https://test.wp/post-55"

        payload = mock_post.call_args[1]["json"]
        assert payload["featured_media"] == 77


class TestGetTagsUrl:

    def test_pretty_permalink(self):
        url = _get_tags_url("https://test.wp/wp-json/wp/v2/posts")
        assert url == "https://test.wp/wp-json/wp/v2/tags"

    def test_rest_route_param(self):
        url = _get_tags_url("https://test.wp/?rest_route=/wp/v2/posts")
        assert "rest_route=%2Fwp%2Fv2%2Ftags" in url


class TestGetCategoriesUrl:

    def test_pretty_permalink(self):
        url = _get_categories_url("https://test.wp/wp-json/wp/v2/posts")
        assert url == "https://test.wp/wp-json/wp/v2/categories"

    def test_rest_route_param(self):
        url = _get_categories_url("https://test.wp/?rest_route=/wp/v2/posts")
        assert "rest_route=%2Fwp%2Fv2%2Fcategories" in url


class TestEnsureWPTags:

    @patch("blog_autopilot.publisher._create_or_get_wp_term")
    def test_all_tags_created(self, mock_create, wp_settings):
        mock_create.side_effect = [10, 20, 30]
        ids = ensure_wp_tags(("标签1", "标签2", "标签3"), wp_settings)
        assert ids == [10, 20, 30]

    @patch("blog_autopilot.publisher._create_or_get_wp_term")
    def test_existing_tag(self, mock_create, wp_settings):
        mock_create.side_effect = [10, 20, 30]
        ids = ensure_wp_tags(("新标签", "已有标签", "另一个"), wp_settings)
        assert len(ids) == 3

    @patch("blog_autopilot.publisher._create_or_get_wp_term")
    def test_partial_failure(self, mock_create, wp_settings):
        mock_create.side_effect = [10, None, 30]
        ids = ensure_wp_tags(("标签1", "失败标签", "标签3"), wp_settings)
        assert ids == [10, 30]




class TestSyncWPTaxonomyMappings:

    @patch("blog_autopilot.publisher._fetch_wp_taxonomy_map")
    @patch("blog_autopilot.publisher._create_or_get_wp_term")
    def test_resolve_existing_terms(self, mock_create_term, mock_fetch_map, wp_settings):
        mock_fetch_map.return_value = {
            "category_slug_to_id": {"tech-weekly": 101},
            "category_name_to_id": {"技术周刊": 101},
            "tag_slug_to_id": {"api-dev": 201},
            "tag_name_to_id": {"API开发": 201},
        }
        mock_create_term.return_value = None

        result = sync_wp_taxonomy_mappings(
            category_mapping={"name": "技术周刊", "category_slug": "tech-weekly", "auto_create": False},
            tag_mappings=[{"name": "API开发", "tag_slug": "api-dev", "auto_create": False}],
            settings=wp_settings,
        )

        assert result["category_id"] == 101
        assert result["tag_ids"] == [201]
        mock_create_term.assert_not_called()

    @patch("blog_autopilot.publisher._fetch_wp_taxonomy_map")
    @patch("blog_autopilot.publisher._create_or_get_wp_term")
    def test_auto_create_missing_tag(self, mock_create_term, mock_fetch_map, wp_settings):
        mock_fetch_map.return_value = {
            "category_slug_to_id": {},
            "category_name_to_id": {},
            "tag_slug_to_id": {},
            "tag_name_to_id": {},
        }
        mock_create_term.return_value = 333

        result = sync_wp_taxonomy_mappings(
            category_mapping=None,
            tag_mappings=[{"name": "新主题", "tag_slug": "new-topic", "auto_create": True}],
            settings=wp_settings,
        )

        assert result["category_id"] is None
        assert result["tag_ids"] == [333]
        mock_create_term.assert_called_once()

    @patch("blog_autopilot.publisher._fetch_wp_taxonomy_map")
    def test_sync_failure_downgrades(self, mock_fetch_map, wp_settings):
        mock_fetch_map.side_effect = Exception("network down")

        result = sync_wp_taxonomy_mappings(
            category_mapping={"name": "技术周刊", "auto_create": False},
            tag_mappings=[{"name": "API开发", "auto_create": False}],
            settings=wp_settings,
        )

        assert result["category_id"] is None
        assert result["tag_ids"] == []
        assert result["taxonomy_map"] is None



class TestPostToWordpress5xx:

    @patch("blog_autopilot.publisher.requests.post")
    def test_5xx_raises_retryable_wp_error(self, mock_post, wp_settings):
        """5xx 错误应抛出 retryable=True 的 WordPressError"""
        import requests as req

        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "Bad Gateway"
        http_err = req.exceptions.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        with pytest.raises(WordPressError) as exc_info:
            post_to_wordpress("Title", "<p>Body</p>", wp_settings)

        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 502
        # tenacity retries once (stop_after_attempt=2), so 2 calls total
        assert mock_post.call_count == 2
