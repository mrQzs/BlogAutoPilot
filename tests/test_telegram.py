"""测试 Telegram 推送模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import TelegramSettings
from blog_autopilot.exceptions import TelegramError
from blog_autopilot.telegram import send_to_telegram


@pytest.fixture
def tg_settings():
    return TelegramSettings(
        bot_token="test-token-123",
        channel_id="@test_channel",
    )


class TestSendToTelegram:

    @patch("blog_autopilot.telegram.requests.post")
    def test_send_success(self, mock_post, tg_settings):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        result = send_to_telegram(
            "推广文案", "https://example.com/post", tg_settings
        )
        assert result is True
