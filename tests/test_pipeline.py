"""测试主流水线模块"""

import os

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import (
    AISettings,
    PathSettings,
    Settings,
    TelegramSettings,
    WordPressSettings,
)
from blog_autopilot.models import ArticleResult, CategoryMeta, FileTask
from blog_autopilot.pipeline import Pipeline


@pytest.fixture
def test_settings(tmp_path):
    """构造测试用 Settings，使用临时目录"""
    input_dir = tmp_path / "input"
    processed_dir = tmp_path / "processed"
    drafts_dir = tmp_path / "drafts"
    input_dir.mkdir()
    processed_dir.mkdir()
    drafts_dir.mkdir()

    return Settings(
        wp=WordPressSettings(
            url="https://test.wp/api",
            user="testuser",
            app_password="testpass",
        ),
        tg=TelegramSettings(
            bot_token="test-token",
            channel_id="@test",
        ),
        ai=AISettings(
            api_key="test-key",
            api_base="https://test.api/v1",
        ),
        paths=PathSettings(
            input_folder=str(input_dir),
            processed_folder=str(processed_dir),
            drafts_folder=str(drafts_dir),
        ),
    )


@pytest.fixture
def sample_task(tmp_path):
    """创建一个带有实际文件的测试任务"""
    input_dir = tmp_path / "input"
    sub_dir = input_dir / "Magazine" / "Science_28"
    sub_dir.mkdir(parents=True)
    test_file = sub_dir / "test.txt"
    test_file.write_text("A" * 200, encoding="utf-8")

    return FileTask(
        filepath=str(test_file),
        filename="test.txt",
        metadata=CategoryMeta(
            category_name="Magazine",
            subcategory_name="Science",
            category_id=28,
            hashtag="#Magazine_Science",
        ),
    )


class TestPipeline:

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_process_file_success(
        self, mock_wp, mock_tg, test_settings, sample_task
    ):
        mock_wp.return_value = "https://test.wp/post-1"
        mock_tg.return_value = True

        pipeline = Pipeline(test_settings)
        mock_article = ArticleResult(
            title="测试标题", html_body="<p>正文</p>"
        )
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广文案"

        result = pipeline.process_file(sample_task)

        assert result.success is True
        assert result.title == "测试标题"
        assert result.blog_link == "https://test.wp/post-1"
