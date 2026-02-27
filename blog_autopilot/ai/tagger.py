"""标签提取解析 + 验证 + 规范化"""

import logging
import re

from blog_autopilot.ai.json_parser import _parse_json_response
from blog_autopilot.constants import TAG_CONTENT_MAX_LENGTH, TAG_MAX_LENGTH
from blog_autopilot.exceptions import AIResponseParseError, TagExtractionError
from blog_autopilot.models import TagSet

logger = logging.getLogger("blog-autopilot")

# 标签提取 JSON 必需字段
_TAGGER_REQUIRED_FIELDS = (
    "title", "tag_magazine", "tag_science",
    "tag_topic", "tag_content", "tg_promo",
)


def _parse_tagger_response(response_text: str) -> dict:
    """解析标签提取 AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_tagger_fields,
        "无法从 AI 响应中解析 JSON",
    )


def _validate_tagger_fields(data: dict) -> None:
    """验证解析后的 dict 包含所有必需字段"""
    missing = [f for f in _TAGGER_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"AI 响应缺少必需字段: {', '.join(missing)}"
        )


def normalize_tag(tag: str) -> str:
    """规范化单个标签：去除空白、合并多余空格"""
    tag = tag.strip()
    # 全角空格 → 半角
    tag = tag.replace("\u3000", " ")
    # 合并连续空格
    tag = re.sub(r"\s+", " ", tag)
    return tag


def validate_tags(tags: TagSet) -> TagSet:
    """
    验证并规范化四级标签。

    抛出:
        TagExtractionError: 标签为空或超长
    """
    normalized = {}
    limits = {
        "tag_magazine": TAG_MAX_LENGTH,
        "tag_science": TAG_MAX_LENGTH,
        "tag_topic": TAG_MAX_LENGTH,
        "tag_content": TAG_CONTENT_MAX_LENGTH,
    }

    for field_name, max_len in limits.items():
        value = getattr(tags, field_name)
        value = normalize_tag(value)

        if not value:
            raise TagExtractionError(f"标签 {field_name} 不能为空")

        if len(value) > max_len:
            raise TagExtractionError(
                f"标签 {field_name} 超长: {len(value)} > {max_len}"
            )

        normalized[field_name] = value

    return TagSet(**normalized)
