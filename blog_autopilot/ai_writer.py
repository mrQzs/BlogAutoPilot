"""向后兼容 shim — 所有符号已迁移到 blog_autopilot.ai 子包"""

from blog_autopilot.ai import (  # noqa: F401
    AIWriter,
    PROMPTS_DIR,
    _log_link_coverage,
    _parse_json_response,
    _parse_review_response,
    _parse_seo_response,
    _parse_tagger_response,
    _repair_truncated_json,
    _validate_review,
    _validate_seo_metadata,
    _warn_unclosed_tags,
    build_relation_context,
    format_issues_for_rewrite,
    normalize_tag,
    sanitize_input,
    validate_tags,
)
