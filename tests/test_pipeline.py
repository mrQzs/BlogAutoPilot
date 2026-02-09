"""测试主流水线模块"""

import os

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.config import (
    AISettings,
    DatabaseSettings,
    EmbeddingSettings,
    PathSettings,
    Settings,
    TelegramSettings,
    WordPressSettings,
)
from blog_autopilot.models import (
    ArticleRecord,
    ArticleResult,
    AssociationResult,
    CategoryMeta,
    FileTask,
    TagSet,
)
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
        database=DatabaseSettings(),
        embedding=EmbeddingSettings(),
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
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广文案"

        result = pipeline.process_file(sample_task)

        assert result.success is True
        assert result.title == "测试标题"
        assert result.blog_link == "https://test.wp/post-1"


class TestPipelineNoDatabase:
    """数据库未配置时的回退行为"""

    def test_association_disabled(self, test_settings):
        """数据库未配置时，关联系统自动禁用"""
        pipeline = Pipeline(test_settings)
        assert pipeline._association_enabled is False
        assert pipeline._database is None
        assert pipeline._embedding_client is None

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_process_file_without_db(
        self, mock_wp, mock_tg, test_settings, sample_task
    ):
        """无数据库时使用原有生成方式"""
        mock_wp.return_value = "https://test.wp/post-1"
        mock_tg.return_value = True

        pipeline = Pipeline(test_settings)
        mock_article = ArticleResult(
            title="测试标题", html_body="<p>正文</p>"
        )
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广文案"

        result = pipeline.process_file(sample_task)

        assert result.success is True
        # generate_blog_post_with_context 应该被调用，但 associations=None
        pipeline._writer.generate_blog_post_with_context.assert_called_once()
        call_args = pipeline._writer.generate_blog_post_with_context.call_args
        assert call_args.kwargs.get("associations") is None


class TestPipelineWithDatabase:
    """数据库可用时的增强流程"""

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_process_file_with_associations(
        self, mock_wp, mock_tg, test_settings, sample_task
    ):
        """有关联文章时使用增强生成"""
        mock_wp.return_value = "https://test.wp/post-1"

        pipeline = Pipeline(test_settings)
        mock_article = ArticleResult(
            title="增强标题", html_body="<p>增强正文</p>"
        )
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广文案"
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案内容",
            "提取标题",
        )

        # Mock 数据库和 Embedding
        mock_db = MagicMock()
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072
        mock_db.find_related_articles.return_value = [
            AssociationResult(
                article=ArticleRecord(
                    id="rel-1",
                    title="关联文章",
                    tags=TagSet("周刊", "AI", "测试", "内容"),
                    tg_promo="关联推广",
                ),
                tag_match_count=3,
                relation_level="中关联",
                similarity=0.8,
            )
        ]
        mock_db.insert_article.return_value = "new-001"

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb

        result = pipeline.process_file(sample_task)

        assert result.success is True
        assert result.title == "增强标题"
        # 确认使用了增强生成
        call_args = pipeline._writer.generate_blog_post_with_context.call_args
        assert call_args.kwargs.get("associations") is not None

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_association_error_fallback(
        self, mock_wp, mock_tg, test_settings, sample_task
    ):
        """关联查询异常时回退到原有模式"""
        mock_wp.return_value = "https://test.wp/post-1"

        pipeline = Pipeline(test_settings)
        mock_article = ArticleResult(
            title="回退标题", html_body="<p>正文</p>"
        )
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广"
        pipeline._writer.extract_tags_and_promo.side_effect = Exception(
            "标签提取失败"
        )

        mock_db = MagicMock()
        mock_emb = MagicMock()
        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb

        result = pipeline.process_file(sample_task)

        # 关联失败不阻断流程
        assert result.success is True
        # associations 应该是 None（因为异常被捕获）
        call_args = pipeline._writer.generate_blog_post_with_context.call_args
        assert call_args.kwargs.get("associations") is None

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_ingest_error_not_blocking(
        self, mock_wp, mock_tg, test_settings, sample_task
    ):
        """入库异常不阻断发布"""
        mock_wp.return_value = "https://test.wp/post-1"

        pipeline = Pipeline(test_settings)
        mock_article = ArticleResult(
            title="测试标题", html_body="<p>正文</p>"
        )
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广"
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案",
            "测试标题",
        )

        mock_db = MagicMock()
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072
        mock_db.find_related_articles.return_value = []
        mock_db.insert_article.side_effect = Exception("入库失败")

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb
        pipeline._ingestor = MagicMock()

        result = pipeline.process_file(sample_task)

        # 即使入库失败，文章仍然发布成功
        assert result.success is True
        assert result.blog_link == "https://test.wp/post-1"
