"""质量审核解析 + 验证 + 重写格式化"""

import logging

from blog_autopilot.ai.json_parser import _parse_json_response
from blog_autopilot.constants import (
    QUALITY_PASS_THRESHOLD,
    QUALITY_REQUIRED_FIELDS,
    QUALITY_REWRITE_THRESHOLD,
    QUALITY_WEIGHT_AI_CLICHE,
    QUALITY_WEIGHT_CONSISTENCY,
    QUALITY_WEIGHT_FACTUALITY,
    QUALITY_WEIGHT_READABILITY,
)
from blog_autopilot.exceptions import AIResponseParseError, QualityReviewError
from blog_autopilot.models import QualityIssue, QualityReview

logger = logging.getLogger("blog-autopilot")


def _parse_review_response(response_text: str) -> dict:
    """解析质量审核 AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_review_fields,
        "无法从审核响应中解析 JSON",
    )


def _validate_review_fields(data: dict) -> None:
    """验证审核响应包含所有必需字段（factuality 可选，向后兼容）"""
    # factuality 缺失时 _validate_review 会回退到 consistency 值
    required = [f for f in QUALITY_REQUIRED_FIELDS if f != "factuality"]
    missing = [f for f in required if f not in data]
    if missing:
        raise AIResponseParseError(
            f"审核响应缺少必需字段: {', '.join(missing)}"
        )


def _validate_review(
    data: dict,
    pass_threshold: int = QUALITY_PASS_THRESHOLD,
    rewrite_threshold: int = QUALITY_REWRITE_THRESHOLD,
) -> QualityReview:
    """
    验证并构建 QualityReview 对象。

    - 分数 clamp 到 1-10（容错，不抛异常）
    - Python 端重算 overall_score（LLM 算术不可靠）
    - 根据阈值推导 verdict

    抛出:
        QualityReviewError: 分数不是整数
    """
    def _clamp_score(value, field_name: str) -> int:
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            raise QualityReviewError(
                f"{field_name} 必须是整数，实际值: {value!r}"
            )
        return max(1, min(10, score))

    consistency = _clamp_score(data["consistency"], "consistency")
    # 向后兼容：factuality 缺失时默认等于 consistency
    factuality = _clamp_score(
        data.get("factuality", data["consistency"]), "factuality"
    )
    readability = _clamp_score(data["readability"], "readability")
    ai_cliche = _clamp_score(data["ai_cliche"], "ai_cliche")

    overall = round(
        consistency * QUALITY_WEIGHT_CONSISTENCY
        + factuality * QUALITY_WEIGHT_FACTUALITY
        + readability * QUALITY_WEIGHT_READABILITY
        + ai_cliche * QUALITY_WEIGHT_AI_CLICHE
    )

    if overall >= pass_threshold:
        verdict = "pass"
    elif overall >= rewrite_threshold:
        verdict = "rewrite"
    else:
        verdict = "draft"

    # 解析 issues
    raw_issues = data.get("issues", [])
    issues = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, dict):
                issues.append(QualityIssue(
                    category=str(item.get("category", "")),
                    severity=str(item.get("severity", "medium")),
                    description=str(item.get("description", "")),
                    suggestion=str(item.get("suggestion", "")),
                ))

    summary = str(data.get("summary", ""))[:200]

    return QualityReview(
        consistency_score=consistency,
        factuality_score=factuality,
        readability_score=readability,
        ai_cliche_score=ai_cliche,
        overall_score=overall,
        verdict=verdict,
        issues=tuple(issues),
        summary=summary,
    )


def format_issues_for_rewrite(issues: tuple[QualityIssue, ...]) -> str:
    """将问题列表格式化为重写提示文本"""
    if not issues:
        return "无具体问题记录。"
    lines = []
    for i, issue in enumerate(issues, 1):
        lines.append(
            f"{i}. [{issue.severity}] {issue.category}: "
            f"{issue.description}\n   建议: {issue.suggestion}"
        )
    return "\n".join(lines)
