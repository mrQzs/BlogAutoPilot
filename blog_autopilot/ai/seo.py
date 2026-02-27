"""SEO 元数据解析 + 验证"""

import logging
import re

from blog_autopilot.ai.json_parser import _parse_json_response
from blog_autopilot.constants import (
    SEO_META_DESC_MAX_LENGTH,
    SEO_META_DESC_MIN_LENGTH,
    SEO_SLUG_MAX_LENGTH,
    SEO_WP_TAG_MAX_LENGTH,
    SEO_WP_TAGS_MAX_COUNT,
    SEO_WP_TAGS_MIN_COUNT,
)
from blog_autopilot.exceptions import AIResponseParseError, SEOExtractionError
from blog_autopilot.models import SEOMetadata

logger = logging.getLogger("blog-autopilot")

# SEO 提取 JSON 必需字段
_SEO_REQUIRED_FIELDS = ("meta_description", "slug", "wp_tags")


def _parse_seo_response(response_text: str) -> dict:
    """解析 SEO AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_seo_fields,
        "无法从 SEO 响应中解析 JSON",
    )


def _validate_seo_fields(data: dict) -> None:
    """验证 SEO 响应包含所有必需字段"""
    missing = [f for f in _SEO_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"SEO 响应缺少必需字段: {', '.join(missing)}"
        )


def _validate_seo_metadata(data: dict) -> SEOMetadata:
    """
    验证并规范化 SEO 元数据。

    抛出:
        SEOExtractionError: 验证失败
    """
    # meta_description
    desc = str(data.get("meta_description", "")).strip()
    if not desc:
        raise SEOExtractionError("meta_description 不能为空")
    if len(desc) < SEO_META_DESC_MIN_LENGTH:
        logger.warning(
            f"meta_description 偏短: {len(desc)} 字符 "
            f"(建议 {SEO_META_DESC_MIN_LENGTH}-{SEO_META_DESC_MAX_LENGTH})"
        )
    if len(desc) > SEO_META_DESC_MAX_LENGTH:
        desc = desc[:SEO_META_DESC_MAX_LENGTH]

    # slug
    slug = str(data.get("slug", "")).strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    if not slug:
        raise SEOExtractionError("slug 规范化后为空")
    if len(slug) > SEO_SLUG_MAX_LENGTH:
        slug = slug[:SEO_SLUG_MAX_LENGTH].rstrip("-")

    # wp_tags
    raw_tags = data.get("wp_tags")
    if not isinstance(raw_tags, list):
        raise SEOExtractionError("wp_tags 必须是数组")
    tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    tags = [t[:SEO_WP_TAG_MAX_LENGTH] for t in tags]
    if len(tags) < SEO_WP_TAGS_MIN_COUNT:
        raise SEOExtractionError(
            f"wp_tags 数量不足: {len(tags)} < {SEO_WP_TAGS_MIN_COUNT}"
        )
    tags = tags[:SEO_WP_TAGS_MAX_COUNT]

    return SEOMetadata(
        meta_description=desc,
        slug=slug,
        wp_tags=tuple(tags),
    )
