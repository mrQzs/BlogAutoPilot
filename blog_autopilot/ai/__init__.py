"""AI 子包 — 公共 API 再导出"""

from blog_autopilot.ai.client import AIWriter, PROMPTS_DIR
from blog_autopilot.ai.html_utils import _warn_unclosed_tags
from blog_autopilot.ai.json_parser import _parse_json_response, _repair_truncated_json
from blog_autopilot.ai.relation_context import build_relation_context, _log_link_coverage
from blog_autopilot.ai.review import (
    _parse_review_response,
    _validate_review,
    format_issues_for_rewrite,
)
from blog_autopilot.ai.sanitize import sanitize_input
from blog_autopilot.ai.seo import _parse_seo_response, _validate_seo_metadata
from blog_autopilot.ai.tagger import (
    _parse_tagger_response,
    normalize_tag,
    validate_tags,
)

__all__ = [
    "AIWriter",
    "PROMPTS_DIR",
    "_log_link_coverage",
    "_parse_json_response",
    "_parse_review_response",
    "_parse_seo_response",
    "_parse_tagger_response",
    "_repair_truncated_json",
    "_validate_review",
    "_validate_seo_metadata",
    "_warn_unclosed_tags",
    "build_relation_context",
    "format_issues_for_rewrite",
    "normalize_tag",
    "sanitize_input",
    "validate_tags",
]
