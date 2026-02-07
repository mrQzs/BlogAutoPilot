"""测试 WordPress 发布模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError
from blog_autopilot.publisher import post_to_wordpress, test_wp_connection


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
