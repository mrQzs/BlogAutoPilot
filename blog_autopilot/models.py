"""数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TokenUsage:
    """单次 API 调用的 token 用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    task: str = ""  # "writer" | "promo" | "tagger" | "reviewer" | "seo"


@dataclass
class TokenUsageSummary:
    """流水线级别的 token 用量汇总"""
    calls: list[TokenUsage]

    def __init__(self):
        self.calls = []

    def add(self, usage: TokenUsage) -> None:
        self.calls.append(usage)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(u.prompt_tokens for u in self.calls)

    @property
    def total_completion_tokens(self) -> int:
        return sum(u.completion_tokens for u in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.calls)

    def summary_str(self) -> str:
        if not self.calls:
            return "Token 用量: 无 API 调用"
        return (
            f"Token 用量: {self.total_tokens:,} "
            f"(输入: {self.total_prompt_tokens:,}, "
            f"输出: {self.total_completion_tokens:,}, "
            f"调用: {len(self.calls)} 次)"
        )


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


# ── 智能选题推荐数据模型 ──


@dataclass(frozen=True)
class ContentGap:
    """内容缺口"""
    gap_type: str          # "tag_gap" | "vector_gap" | "merged"
    description: str
    gap_score: float
    tags: TagSet | None = None
    reference_title: str | None = None


@dataclass(frozen=True)
class TopicRecommendation:
    """主题推荐结果"""
    topic: str
    rationale: str
    suggested_tags: TagSet
    priority: str          # "high" | "medium" | "low"
    gap_score: float = 0.0


# ── 文章系列数据模型 ──


@dataclass(frozen=True)
class SeriesRecord:
    """数据库系列记录"""
    id: str
    title: str
    tag_magazine: str
    tag_science: str
    tag_topic: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class SeriesInfo:
    """文章系列信息"""
    series_id: str
    series_title: str
    order: int                          # 本文在系列中的位置 (1-based)
    total: int                          # 系列当前总篇数
    prev_article: ArticleRecord | None  # 上一篇
