"""共享 fixtures"""

import os
import tempfile

import pytest

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
    AssociationResult,
    CategoryMeta,
    TagSet,
)


@pytest.fixture
def tmp_dirs(tmp_path):
    """创建临时的 input / processed / drafts 目录"""
    input_dir = tmp_path / "input"
    processed_dir = tmp_path / "processed"
    drafts_dir = tmp_path / "drafts"
    input_dir.mkdir()
    processed_dir.mkdir()
    drafts_dir.mkdir()
    return {
        "input": str(input_dir),
        "processed": str(processed_dir),
        "drafts": str(drafts_dir),
    }


@pytest.fixture
def ai_settings():
    return AISettings(
        api_key="test-key",
        api_base="https://test.api/v1",
    )


@pytest.fixture
def db_settings():
    return DatabaseSettings(
        host="localhost",
        port=5432,
        name="test_db",
        user="testuser",
        password="testpass",
    )


@pytest.fixture
def embedding_settings():
    return EmbeddingSettings(
        api_key="test-embedding-key",
        api_base="https://test.embedding.api/v1",
    )


@pytest.fixture
def sample_tags():
    return TagSet(
        tag_magazine="技术周刊",
        tag_science="AI应用",
        tag_topic="API开发",
        tag_content="Claude自动化",
    )


@pytest.fixture
def sample_article_record(sample_tags):
    return ArticleRecord(
        id="test-001",
        title="测试文章标题",
        tags=sample_tags,
        tg_promo="这是一段测试用的推广文案，长度足够用于测试。" * 5,
        embedding=[0.1] * 3072,
        url="https://test.blog/post-1",
    )


@pytest.fixture
def sample_association(sample_article_record):
    return AssociationResult(
        article=sample_article_record,
        tag_match_count=3,
        relation_level="中关联",
        similarity=0.85,
    )
