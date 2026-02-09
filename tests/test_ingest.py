"""测试入库流程"""

import os

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.ingest import ArticleIngestor
from blog_autopilot.models import (
    ArticleRecord,
    IngestionResult,
    TagSet,
)
from blog_autopilot.exceptions import (
    AIAPIError,
    DatabaseError,
    EmbeddingError,
)


@pytest.fixture
def mock_settings():
    """构造最小化的 mock Settings"""
    settings = MagicMock()
    settings.ai = MagicMock()
    settings.ai.api_key = MagicMock()
    settings.ai.api_key.get_secret_value.return_value = "test-key"
    settings.ai.api_base = "https://test.api/v1"
    settings.ai.model_writer = "test-model"
    settings.ai.model_promo = "test-model-promo"
    settings.ai.writer_max_tokens = 4000
    settings.ai.promo_max_tokens = 2000
    settings.ai.default_headers = {}
    settings.database = MagicMock()
    settings.database.get_dsn.return_value = "postgresql://test:test@localhost/test"
    settings.embedding = MagicMock()
    settings.embedding.api_key = MagicMock()
    settings.embedding.api_key.get_secret_value.return_value = "test-emb-key"
    settings.embedding.api_base = "https://test.emb/v1"
    settings.embedding.model = "test-embedding-model"
    settings.embedding.dimensions = 3072
    return settings


@pytest.fixture
def ingestor(mock_settings):
    """创建一个带 mock 组件的 ArticleIngestor"""
    with patch("blog_autopilot.ingest.AIWriter") as MockWriter, \
         patch("blog_autopilot.ingest.EmbeddingClient") as MockEmb, \
         patch("blog_autopilot.ingest.Database") as MockDB:

        mock_writer = MockWriter.return_value
        mock_emb = MockEmb.return_value
        mock_db = MockDB.return_value

        mock_writer.extract_tags_and_promo.return_value = (
            TagSet("技术周刊", "AI应用", "API开发", "自动化"),
            "这是推广文案" * 20,
            "测试标题",
        )
        mock_emb.get_embedding.return_value = [0.1] * 3072
        mock_db.get_article_by_url.return_value = None
        mock_db.insert_article.return_value = "gen-001"

        ing = ArticleIngestor(mock_settings)
        # 替换内部组件为 mock
        ing._writer = mock_writer
        ing._embedding = mock_emb
        ing._db = mock_db
        yield ing


class TestIngestArticle:

    def test_success(self, ingestor):
        result = ingestor.ingest_article("一些文章内容" * 50)
        assert result.success is True
        assert result.article_id is not None

    def test_with_url(self, ingestor):
        result = ingestor.ingest_article(
            "文章内容" * 50,
            url="https://blog.test/post-1",
        )
        assert result.success is True

    def test_url_dedup_skip(self, ingestor):
        """已存在的 URL 跳过入库"""
        existing = ArticleRecord(
            id="existing-001",
            title="已有文章",
            tags=TagSet("周刊", "AI", "测试", "内容"),
            tg_promo="已有推广",
        )
        ingestor._db.get_article_by_url.return_value = existing

        result = ingestor.ingest_article(
            "内容" * 50,
            url="https://blog.test/existing",
        )
        assert result.success is True
        assert result.article_id == "existing-001"
        # 不应该调用 insert
        ingestor._db.insert_article.assert_not_called()

    def test_tag_extraction_failure(self, ingestor):
        ingestor._writer.extract_tags_and_promo.side_effect = AIAPIError(
            "API 超时"
        )
        result = ingestor.ingest_article("内容" * 50)
        assert result.success is False
        assert "标签提取失败" in result.error

    def test_embedding_failure(self, ingestor):
        ingestor._embedding.get_embedding.side_effect = EmbeddingError(
            "Embedding 失败"
        )
        result = ingestor.ingest_article("内容" * 50)
        assert result.success is False
        assert "Embedding 失败" in result.error

    def test_database_failure(self, ingestor):
        ingestor._db.insert_article.side_effect = DatabaseError(
            "写入失败"
        )
        result = ingestor.ingest_article("内容" * 50)
        assert result.success is False
        assert "数据库写入失败" in result.error


class TestIngestBatch:

    def test_batch_success(self, ingestor):
        articles = [
            {"content": "文章1内容" * 50},
            {"content": "文章2内容" * 50},
            {"content": "文章3内容" * 50},
        ]
        results = ingestor.ingest_batch(articles)
        assert len(results) == 3
        assert all(r.success for r in results)

    def test_batch_partial_failure(self, ingestor):
        """部分失败不影响后续"""
        call_count = [0]
        original_extract = ingestor._writer.extract_tags_and_promo

        def extract_side_effect(content):
            call_count[0] += 1
            if call_count[0] == 2:
                raise AIAPIError("第2篇失败")
            return original_extract(content)

        ingestor._writer.extract_tags_and_promo = MagicMock(
            side_effect=extract_side_effect
        )

        articles = [
            {"content": "文章1" * 50},
            {"content": "文章2" * 50},
            {"content": "文章3" * 50},
        ]
        results = ingestor.ingest_batch(articles)
        assert len(results) == 3
        assert results[0].success is True
        assert results[1].success is False
        assert results[2].success is True

    def test_batch_empty_content(self, ingestor):
        articles = [{"content": ""}]
        results = ingestor.ingest_batch(articles)
        assert len(results) == 1
        assert results[0].success is False
        assert "内容为空" in results[0].error

    def test_batch_with_progress(self, ingestor):
        progress_calls = []

        def on_progress(current, total, result):
            progress_calls.append((current, total))

        articles = [{"content": "文章" * 50}]
        ingestor.ingest_batch(articles, on_progress=on_progress)
        assert len(progress_calls) == 1
        assert progress_calls[0] == (1, 1)


class TestIngestFromDirectory:

    def test_directory_scan(self, ingestor, tmp_path):
        """扫描目录并入库"""
        # 创建测试文件
        (tmp_path / "article1.txt").write_text("A" * 200, encoding="utf-8")
        (tmp_path / "article2.md").write_text("B" * 200, encoding="utf-8")
        (tmp_path / "not_supported.xyz").write_text("C" * 200, encoding="utf-8")

        results = ingestor.ingest_from_directory(str(tmp_path))

        # 只处理 .txt 和 .md，不处理 .xyz
        assert len(results) == 2

    def test_empty_directory(self, ingestor, tmp_path):
        results = ingestor.ingest_from_directory(str(tmp_path))
        assert results == []
