"""数据模型"""

from dataclasses import dataclass


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
