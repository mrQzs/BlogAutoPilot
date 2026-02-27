"""标签提取解析 + 验证 + 规范化"""

import logging
import re

from blog_autopilot.ai.json_parser import _parse_json_response
from blog_autopilot.constants import TAG_CONTENT_MAX_LENGTH, TAG_MAX_LENGTH
from blog_autopilot.exceptions import AIResponseParseError, TagExtractionError
from blog_autopilot.models import TagSet
from blog_autopilot.tag_registry import validate_tags_against_registry  # noqa: F401

logger = logging.getLogger("blog-autopilot")

# 标签提取 JSON 必需字段
_TAGGER_REQUIRED_FIELDS = (
    "title", "tag_magazine", "tag_science",
    "tag_topic", "tag_content", "tg_promo",
)


def _parse_tagger_response(response_text: str) -> dict:
    """解析标签提取 AI 响应 JSON，JSON 解析全部失败时回退正则提取"""
    try:
        return _parse_json_response(
            response_text, _validate_tagger_fields,
            "无法从 AI 响应中解析 JSON",
        )
    except AIResponseParseError:
        # JSON 解析全部失败（常见原因：tg_promo 含未转义引号），
        # 用字段名锚点正则提取
        data = _regex_extract_tagger_fields(response_text)
        if data:
            logger.warning("JSON 解析失败，已通过正则回退提取标签字段")
            return data
        raise


def _regex_extract_tagger_fields(text: str) -> dict | None:
    """
    JSON 解析失败时的正则回退：利用字段名作为锚点逐一提取值。

    处理 tg_promo 含未转义引号等 AI 常见 JSON 格式问题。
    返回 dict 或 None（无法提取时）。
    """
    fields = list(_TAGGER_REQUIRED_FIELDS)
    result = {}

    for i, field in enumerate(fields):
        # 找 "field": " 起始位置
        start_pat = rf'"{re.escape(field)}"\s*:\s*"'
        start_match = re.search(start_pat, text)
        if not start_match:
            return None
        val_start = start_match.end()

        if i + 1 < len(fields):
            # 用下一个字段键名定位当前值的结束
            next_field = fields[i + 1]
            end_pat = rf'"\s*,?\s*\n?\s*"{re.escape(next_field)}"\s*:'
            end_match = re.search(end_pat, text[val_start:])
            if end_match:
                result[field] = text[val_start:val_start + end_match.start()]
                continue
            return None
        else:
            # 最后一个字段（tg_promo）：找 "} 结尾
            remaining = text[val_start:]
            last_brace = remaining.rfind("}")
            if last_brace == -1:
                return None
            before_brace = remaining[:last_brace].rstrip()
            if before_brace.endswith('"'):
                result[field] = before_brace[:-1]
            else:
                return None

    # 反转义
    for key in result:
        val = result[key]
        val = val.replace("\\n", "\n").replace("\\r", "")
        val = val.replace('\\"', '"').replace("\\\\", "\\")
        result[key] = val.strip()

    try:
        _validate_tagger_fields(result)
    except AIResponseParseError:
        return None
    return result


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
