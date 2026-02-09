"""数据模型"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CategoryMeta:
    """目录解析后的分类元数据"""
    category_name: str
    subcategory_name: str
    category_id: int
    hashtag: str


@dataclass(frozen=True)
class FileTask:
    """待处理文件任务"""
    filepath: str
    filename: str
    metadata: CategoryMeta


@dataclass(frozen=True)
class ArticleResult:
    """AI 生成的文章结果"""
    title: str
    html_body: str


@dataclass(frozen=True)
class PipelineResult:
    """单个文件的流水线处理结果"""
    filename: str
    success: bool
    title: str | None = None
    blog_link: str | None = None
    error: str | None = None


# ── 文章关联系统数据模型 ──


@dataclass(frozen=True)
class TagSet:
    """四级标签集合"""
    tag_magazine: str
    tag_science: str
    tag_topic: str
    tag_content: str


@dataclass(frozen=True)
class ArticleRecord:
    """数据库文章记录"""
    id: str
    title: str
    tags: TagSet
    tg_promo: str
    embedding: list[float] | None = None
    url: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class AssociationResult:
    """关联查询结果"""
    article: ArticleRecord
    tag_match_count: int
    relation_level: str
    similarity: float


@dataclass(frozen=True)
class IngestionResult:
    """入库处理结果"""
    article_id: str
    title: str
    tags: TagSet | None = None
    success: bool = True
    error: str | None = None
