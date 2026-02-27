"""测试多维度内容去重系统"""

import hashlib

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
from blog_autopilot.db import Database
from blog_autopilot.models import (
    ArticleRecord,
    ArticleResult,
    CategoryMeta,
    FileTask,
    TagSet,
)
from blog_autopilot.publisher import PublishResult
from blog_autopilot.pipeline import Pipeline


# ── DB 层: find_duplicate_by_hash ──


class TestFindDuplicateByHash:

    def test_found(self, db_settings):
        """哈希匹配时返回文章信息"""
        db = Database(db_settings)
        expected = {"id": "abc-123", "title": "已有文章", "url": "https://blog/1"}

        with patch.object(db, "fetch_one", return_value=expected) as mock_fetch:
            result = db.find_duplicate_by_hash("deadbeef" * 8)
            assert result == expected
            mock_fetch.assert_called_once()

    def test_not_found(self, db_settings):
        """哈希不匹配时返回 None"""
        db = Database(db_settings)

        with patch.object(db, "fetch_one", return_value=None):
            result = db.find_duplicate_by_hash("deadbeef" * 8)
            assert result is None

    def test_db_error_returns_none(self, db_settings):
        """数据库异常时安全返回 None"""
        db = Database(db_settings)

        with patch.object(db, "fetch_one", side_effect=Exception("连接超时")):
            result = db.find_duplicate_by_hash("deadbeef" * 8)
            assert result is None


# ── DB 层: find_similar_titles ──


class TestFindSimilarTitles:

    def test_match(self, db_settings, sample_tags):
        """标题 + 四级标签完全匹配时返回结果"""
        db = Database(db_settings)
        expected = {"id": "dup-1", "title": "重复标题", "url": "https://blog/dup"}

        with patch.object(db, "fetch_one", return_value=expected) as mock_fetch:
            result = db.find_similar_titles("重复标题", sample_tags)
            assert result == expected
            # 验证传入了 5 个参数（4 个标签 + 标题）
            call_args = mock_fetch.call_args
            assert len(call_args[0][1]) == 5

    def test_no_match(self, db_settings, sample_tags):
        """无匹配时返回 None"""
        db = Database(db_settings)

        with patch.object(db, "fetch_one", return_value=None):
            result = db.find_similar_titles("全新标题", sample_tags)
            assert result is None

    def test_db_error_returns_none(self, db_settings, sample_tags):
        """数据库异常时安全返回 None"""
        db = Database(db_settings)

        with patch.object(db, "fetch_one", side_effect=Exception("查询超时")):
            result = db.find_similar_titles("某标题", sample_tags)
            assert result is None


# ── DB 层: insert_article with source_hash ──


class TestInsertArticleSourceHash:

    def test_source_hash_passed_to_sql(self, db_settings, sample_article_record):
        """source_hash 正确传入 INSERT 语句"""
        db = Database(db_settings)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(db, "get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: mock_conn
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            db.insert_article(sample_article_record, source_hash="abc123")
            # source_hash 是 INSERT 的倒数第三个参数（后面是 summary, content_excerpt）
            call_args = mock_cursor.execute.call_args[0][1]
            assert call_args[-3] == "abc123"

    def test_source_hash_none_by_default(self, db_settings, sample_article_record):
        """不传 source_hash 时默认为 None"""
        db = Database(db_settings)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(db, "get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: mock_conn
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            db.insert_article(sample_article_record)
            call_args = mock_cursor.execute.call_args[0][1]
            # source_hash is second-to-last (last is summary)
            assert call_args[-2] is None


# ── Pipeline 层: 三级去重集成测试 ──


@pytest.fixture
def pipeline_settings(tmp_path):
    """构造测试用 Settings"""
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
def pipeline_task(tmp_path):
    """创建带实际文件的测试任务"""
    input_dir = tmp_path / "input"
    sub_dir = input_dir / "Magazine" / "Science_28"
    sub_dir.mkdir(parents=True, exist_ok=True)
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


class TestPipelineLevel1HashDedup:
    """Pipeline Level 1: 原文指纹精确匹配（零 API 成本）"""

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_hash_duplicate_blocks_processing(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """Level 1: 哈希匹配时立即返回失败，不调用任何 AI"""
        pipeline = Pipeline(pipeline_settings)
        pipeline._writer = MagicMock()

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = {
            "id": "existing-1",
            "title": "已有文章",
            "url": "https://blog/existing",
        }
        pipeline._database = mock_db

        result = pipeline.process_file(pipeline_task)

        assert result.success is False
        assert "内容重复(指纹)" in result.error
        assert "已有文章" in result.error
        # 不应调用任何 AI 方法
        pipeline._writer.generate_blog_post_with_context.assert_not_called()
        pipeline._writer.extract_tags_and_promo.assert_not_called()
        # 不应发布
        mock_wp.assert_not_called()
        mock_tg.assert_not_called()

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_hash_no_match_continues(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """Level 1: 哈希不匹配时继续后续流程"""
        mock_wp.return_value = PublishResult(url="https://test.wp/post-1", post_id=1)
        mock_tg.return_value = True

        pipeline = Pipeline(pipeline_settings)
        mock_article = ArticleResult(title="新文章", html_body="<p>正文</p>")
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广"

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = None  # 无哈希匹配
        pipeline._database = mock_db

        result = pipeline.process_file(pipeline_task)

        assert result.success is True
        mock_db.find_duplicate_by_hash.assert_called_once()

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_no_db_skips_hash_check(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """无数据库时跳过 Level 1 哈希检查"""
        mock_wp.return_value = PublishResult(url="https://test.wp/post-1", post_id=1)
        mock_tg.return_value = True

        pipeline = Pipeline(pipeline_settings)
        mock_article = ArticleResult(title="新文章", html_body="<p>正文</p>")
        pipeline._writer = MagicMock()
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广"
        # 确保无数据库
        pipeline._database = None

        result = pipeline.process_file(pipeline_task)

        assert result.success is True


class TestPipelineLevel2EmbeddingDedup:
    """Pipeline Level 2: embedding 相似度去重"""

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_embedding_duplicate_blocks_processing(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """Level 2: embedding 相似度超阈值时返回失败"""
        pipeline = Pipeline(pipeline_settings)
        pipeline._writer = MagicMock()
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案",
            "测试标题",
        )

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = None
        mock_db.find_duplicate.return_value = {
            "id": "dup-emb",
            "title": "高度相似文章",
            "similarity": 0.97,
        }
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb

        result = pipeline.process_file(pipeline_task)

        assert result.success is False
        assert "内容重复" in result.error
        assert "高度相似文章" in result.error
        mock_wp.assert_not_called()

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_embedding_no_duplicate_continues(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """Level 2: embedding 相似度低于阈值时继续处理"""
        mock_wp.return_value = PublishResult(url="https://test.wp/post-1", post_id=1)

        pipeline = Pipeline(pipeline_settings)
        mock_article = ArticleResult(title="新文章", html_body="<p>正文</p>")
        pipeline._writer = MagicMock()
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案",
            "新文章",
        )
        pipeline._writer.generate_blog_post_with_context.return_value = mock_article
        pipeline._writer.generate_promo.return_value = "推广"

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = None
        mock_db.find_duplicate.return_value = None  # 无 embedding 重复
        mock_db.find_similar_titles.return_value = None
        mock_db.find_related_articles.return_value = []
        mock_db.insert_article.return_value = "new-001"
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb

        result = pipeline.process_file(pipeline_task)

        assert result.success is True


class TestPipelineLevel3TitleDedup:
    """Pipeline Level 3: 标题 + 标签完全匹配（仅警告，不阻断）"""

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_title_match_warns_but_continues(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """Level 3: 标题+标签匹配时不阻断发布"""
        mock_wp.return_value = PublishResult(
            url="https://test.wp/post-1", post_id=1,
        )

        pipeline = Pipeline(pipeline_settings)
        mock_article = ArticleResult(
            title="重复标题", html_body="<p>正文</p>",
        )
        pipeline._writer = MagicMock()
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案",
            "重复标题",
        )
        pipeline._writer.generate_blog_post_with_context.return_value = (
            mock_article
        )
        pipeline._writer.generate_promo.return_value = "推广"

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = None
        mock_db.find_duplicate.return_value = None
        mock_db.find_similar_titles.return_value = {
            "id": "dup-title",
            "title": "重复标题",
            "url": "https://blog/dup",
        }
        mock_db.find_related_articles.return_value = []
        mock_db.insert_article.return_value = "new-001"
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb

        result = pipeline.process_file(pipeline_task)

        # Level 3 不阻断
        assert result.success is True
        assert result.blog_link == "https://test.wp/post-1"
        mock_db.find_similar_titles.assert_called_once()


class TestPipelineSourceHashPersistence:
    """source_hash 在入库和失败保存中正确传递"""

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress")
    def test_source_hash_passed_to_insert(
        self, mock_wp, mock_tg, pipeline_settings, pipeline_task
    ):
        """入库时 source_hash 正确传递给 insert_article"""
        mock_wp.return_value = PublishResult(
            url="https://test.wp/post-1", post_id=1,
        )

        pipeline = Pipeline(pipeline_settings)
        mock_article = ArticleResult(
            title="新文章", html_body="<p>正文</p>",
        )
        pipeline._writer = MagicMock()
        pipeline._writer.extract_tags_and_promo.return_value = (
            TagSet("周刊", "AI", "测试", "内容"),
            "推广文案",
            "新文章",
        )
        pipeline._writer.generate_blog_post_with_context.return_value = (
            mock_article
        )
        pipeline._writer.generate_promo.return_value = "推广"

        mock_db = MagicMock()
        mock_db.find_duplicate_by_hash.return_value = None
        mock_db.find_duplicate.return_value = None
        mock_db.find_similar_titles.return_value = None
        mock_db.find_related_articles.return_value = []
        mock_db.insert_article.return_value = "new-001"
        mock_emb = MagicMock()
        mock_emb.get_embedding.return_value = [0.1] * 3072

        pipeline._database = mock_db
        pipeline._embedding_client = mock_emb
        pipeline._ingestor = MagicMock()

        result = pipeline.process_file(pipeline_task)

        assert result.success is True
        # 验证 insert_article 被调用且 source_hash 非空
        mock_db.insert_article.assert_called_once()
        call_kwargs = mock_db.insert_article.call_args
        assert call_kwargs.kwargs.get("source_hash") is not None
        # 验证 hash 是 SHA256 格式（64 位十六进制）
        sh = call_kwargs.kwargs["source_hash"]
        assert len(sh) == 64
