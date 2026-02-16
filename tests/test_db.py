"""测试数据库模块"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from blog_autopilot.db import Database
from blog_autopilot.exceptions import DatabaseError
from blog_autopilot.models import ArticleRecord, TagSet


class TestDatabaseConnection:

    def test_test_connection_success(self, db_settings):
        db = Database(db_settings)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(db, "get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: mock_conn
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            assert db.test_connection() is True

    def test_test_connection_failure(self, db_settings):
        db = Database(db_settings)
        with patch.object(db, "get_connection", side_effect=Exception("连接失败")):
            assert db.test_connection() is False


class TestDatabaseSchema:

    @patch("blog_autopilot.db.pool.SimpleConnectionPool")
    @patch("blog_autopilot.db.register_vector")
    def test_initialize_schema(self, mock_reg, mock_pool_cls, db_settings):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # 索引不存在
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_pool_cls.return_value = mock_pool

        db = Database(db_settings)
        db.initialize_schema()

        # 验证 cursor.execute 被调用了多次（DDL 语句）
        assert mock_cursor.execute.call_count >= 7


class TestDatabaseCRUD:

    def _make_db_with_mock(self, db_settings):
        """创建一个带 mock 连接的 Database 实例"""
        db = Database(db_settings)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return db, mock_conn, mock_cursor

    def test_insert_article(self, db_settings, sample_article_record):
        db, mock_conn, mock_cursor = self._make_db_with_mock(db_settings)

        with patch.object(db, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = lambda s: mock_conn
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            result_id = db.insert_article(sample_article_record)
            assert result_id == "test-001"
            mock_cursor.execute.assert_called_once()

    def test_insert_article_duplicate_id(self, db_settings, sample_article_record):
        db = Database(db_settings)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("duplicate key value")
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(db, "get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = lambda s: mock_conn
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(DatabaseError, match="重复"):
                db.insert_article(sample_article_record)

    def test_get_article_found(self, db_settings, sample_tags):
        db = Database(db_settings)
        mock_row = {
            "id": "test-001",
            "title": "测试标题",
            "tag_magazine": "技术周刊",
            "tag_science": "AI应用",
            "tag_topic": "API开发",
            "tag_content": "Claude自动化",
            "tg_promo": "推广文案",
            "embedding": [0.1] * 10,
            "url": "https://test.blog/1",
            "created_at": None,
        }

        with patch.object(db, "fetch_one", return_value=mock_row):
            result = db.get_article("test-001")
            assert result is not None
            assert result.id == "test-001"
            assert result.title == "测试标题"
            assert result.tags.tag_magazine == "技术周刊"

    def test_get_article_not_found(self, db_settings):
        db = Database(db_settings)

        with patch.object(db, "fetch_one", return_value=None):
            result = db.get_article("nonexistent")
            assert result is None

    def test_get_article_by_url(self, db_settings):
        db = Database(db_settings)

        with patch.object(db, "fetch_one", return_value=None):
            result = db.get_article_by_url("https://no.such.url")
            assert result is None

    def test_count_articles(self, db_settings):
        db = Database(db_settings)

        with patch.object(db, "fetch_one", return_value={"cnt": 42}):
            assert db.count_articles() == 42


class TestFindRelatedArticles:

    def test_find_related_basic(self, db_settings, sample_tags):
        db = Database(db_settings)
        mock_rows = [
            {
                "id": "related-1",
                "title": "关联文章1",
                "tg_promo": "推广1",
                "url": None,
                "created_at": None,
                "tag_magazine": "技术周刊",
                "tag_science": "AI应用",
                "tag_topic": "API开发",
                "tag_content": "其他内容",
                "tag_match_count": 3,
                "relation_level": "中关联",
                "similarity": 0.85,
            },
        ]

        with patch.object(db, "fetch_all", return_value=mock_rows):
            results = db.find_related_articles(
                tags=sample_tags,
                embedding=[0.1] * 3072,
                exclude_id="current-001",
            )
            assert len(results) == 1
            assert results[0].tag_match_count == 3
            assert results[0].relation_level == "中关联"
            assert results[0].similarity == 0.85

    def test_find_related_empty(self, db_settings, sample_tags):
        db = Database(db_settings)

        with patch.object(db, "fetch_all", return_value=[]):
            results = db.find_related_articles(
                tags=sample_tags,
                embedding=[0.1] * 3072,
            )
            assert results == []

    def test_find_related_strong(self, db_settings, sample_tags):
        """4 标签全匹配 → 强关联"""
        db = Database(db_settings)
        mock_rows = [
            {
                "id": "related-2",
                "title": "强关联文章",
                "tg_promo": "推广",
                "url": None,
                "created_at": None,
                "tag_magazine": "技术周刊",
                "tag_science": "AI应用",
                "tag_topic": "API开发",
                "tag_content": "Claude自动化",
                "tag_match_count": 4,
                "relation_level": "强关联",
                "similarity": 0.95,
            },
        ]

        with patch.object(db, "fetch_all", return_value=mock_rows):
            results = db.find_related_articles(
                tags=sample_tags,
                embedding=[0.1] * 3072,
            )
            assert results[0].relation_level == "强关联"
            assert results[0].tag_match_count == 4

    def test_find_related_weak(self, db_settings, sample_tags):
        """2 标签匹配 → 弱关联"""
        db = Database(db_settings)
        mock_rows = [
            {
                "id": "related-3",
                "title": "弱关联文章",
                "tg_promo": "推广",
                "url": None,
                "created_at": None,
                "tag_magazine": "技术周刊",
                "tag_science": "其他领域",
                "tag_topic": "其他主题",
                "tag_content": "其他内容",
                "tag_match_count": 2,
                "relation_level": "弱关联",
                "similarity": 0.60,
            },
        ]

        with patch.object(db, "fetch_all", return_value=mock_rows):
            results = db.find_related_articles(
                tags=sample_tags,
                embedding=[0.1] * 3072,
            )
            assert results[0].relation_level == "弱关联"

    def test_find_related_query_error(self, db_settings, sample_tags):
        """查询异常时返回空列表"""
        db = Database(db_settings)

        with patch.object(db, "fetch_all", side_effect=Exception("DB error")):
            results = db.find_related_articles(
                tags=sample_tags,
                embedding=[0.1] * 3072,
            )
            assert results == []

    def test_generate_id(self):
        """ID 生成唯一性"""
        ids = {Database._generate_id() for _ in range(100)}
        assert len(ids) == 100
