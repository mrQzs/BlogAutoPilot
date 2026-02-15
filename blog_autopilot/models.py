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
class SEOMetadata:
    """SEO 元数据"""
    meta_description: str  # 120-160 字符，用作 WordPress excerpt
    slug: str              # 纯英文小写 + 连字符，URL 友好
    wp_tags: tuple[str, ...]  # WordPress 标签关键词


@dataclass(frozen=True)
class QualityIssue:
    """审核发现的单个问题"""
    category: str        # "consistency" | "readability" | "ai_cliche"
    severity: str        # "high" | "medium" | "low"
    description: str
    suggestion: str


@dataclass(frozen=True)
class QualityReview:
    """质量审核结果"""
    consistency_score: int     # 1-10
    readability_score: int     # 1-10
    ai_cliche_score: int       # 1-10
    overall_score: int         # 加权计算
    verdict: str               # "pass" | "rewrite" | "draft"
    issues: tuple[QualityIssue, ...]
    summary: str


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
