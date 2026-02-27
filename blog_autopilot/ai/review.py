"""质量审核解析 + 验证 + 重写格式化 + 自审偏差检测 + 反馈增强"""

from __future__ import annotations

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


# ── Part A: 自审偏差检测 ──


def detect_self_review_bias(settings) -> bool:
    """
    writer 和 reviewer 使用相同模型 **且** 同一 API 端点时返回 True。

    如果 reviewer 配置了独立的 api_base/api_key，即使模型名相同，
    也可能是不同提供商的实例，不视为自审。
    """
    reviewer_model = settings.model_reviewer or settings.model_promo
    if reviewer_model != settings.model_writer:
        return False

    # 模型名相同时检查 API 端点是否也相同
    reviewer_base = settings.reviewer_api_base or settings.api_base
    writer_base = settings.api_base
    if reviewer_base != writer_base:
        return False

    # 检查 API key 是否不同（不同 key 可能指向不同后端）
    reviewer_key = settings.reviewer_api_key
    writer_key = settings.api_key
    if (
        reviewer_key
        and reviewer_key.get_secret_value()
        and reviewer_key.get_secret_value() != writer_key.get_secret_value()
    ):
        return False

    return True


def format_self_review_warning() -> str:
    """自审偏差警告文本，追加到审核 system prompt"""
    return (
        "\n\n"
        "═══════════════════════════\n"
        "  ⚠️ 自审偏差警告\n"
        "═══════════════════════════\n"
        "\n"
        "检测到审核模型与写作模型为同一模型。为了补偿自审偏差：\n"
        "- 对 ai_cliche（AI 痕迹）维度请额外严格，同一模型生成的内容更难识别自身的模板化表达\n"
        "- 对 factuality（事实性）维度请额外关注，避免因熟悉自身输出而放松核查\n"
        "- 系统已自动上调通过阈值来补偿自评偏差，你只需保持正常的严格标准即可\n"
        "- 尤其关注：是否存在自身模型的典型表达模式和措辞习惯\n"
    )


# ── Part C: 增强重写反馈 ──


def format_dimensional_scores(review: QualityReview) -> str:
    """各维度评分格式化"""
    return (
        f"- 一致性 (consistency): {review.consistency_score}/10\n"
        f"- 事实性 (factuality): {review.factuality_score}/10\n"
        f"- 可读性 (readability): {review.readability_score}/10\n"
        f"- AI痕迹 (ai_cliche): {review.ai_cliche_score}/10\n"
        f"- 综合分: {review.overall_score}/10"
    )


def identify_focus_areas(review: QualityReview, top_n: int = 2) -> str:
    """识别最弱维度作为重写重点。所有维度同分时提示全面改进。"""
    dimensions = [
        ("一致性 (consistency)", review.consistency_score),
        ("事实性 (factuality)", review.factuality_score),
        ("可读性 (readability)", review.readability_score),
        ("AI痕迹 (ai_cliche)", review.ai_cliche_score),
    ]
    scores = {s for _, s in dimensions}
    if len(scores) == 1:
        return f"- 所有维度均为 {scores.pop()}/10 — 各方面均需改进，无明显短板"

    sorted_dims = sorted(dimensions, key=lambda x: x[1])
    weakest = sorted_dims[:top_n]
    lines = []
    for name, score in weakest:
        lines.append(f"- {name}: {score}/10 — 需要重点改进")

    # 检查是否有更多维度与已展示的最高分并列
    shown_max_score = weakest[-1][1]
    tied_extras = [
        name for name, score in sorted_dims[top_n:]
        if score == shown_max_score
    ]
    if tied_extras:
        names = "、".join(tied_extras)
        lines.append(f"  （同分维度 {names} 也需要关注）")

    return "\n".join(lines)


def format_progressive_feedback(
    current_review: QualityReview,
    previous_review: QualityReview | None,
    attempt: int,
) -> str:
    """第2次及以后重写时对比前后评分变化，标记退步维度"""
    if previous_review is None or attempt <= 1:
        return ""

    lines = [
        "",
        "",
        f"## 第 {attempt} 次重写对比（与上次审核相比）",
    ]
    comparisons = [
        ("一致性", current_review.consistency_score, previous_review.consistency_score),
        ("事实性", current_review.factuality_score, previous_review.factuality_score),
        ("可读性", current_review.readability_score, previous_review.readability_score),
        ("AI痕迹", current_review.ai_cliche_score, previous_review.ai_cliche_score),
        ("综合分", current_review.overall_score, previous_review.overall_score),
    ]
    for name, current, previous in comparisons:
        diff = current - previous
        if diff > 0:
            lines.append(f"- {name}: {previous} → {current} (+{diff} ✓)")
        elif diff < 0:
            lines.append(f"- {name}: {previous} → {current} ({diff} ✗ 退步)")
        else:
            lines.append(f"- {name}: {previous} → {current} (持平)")

    regressed = [name for name, cur, prev in comparisons if cur < prev]
    if regressed:
        lines.append(f"\n⚠️ 退步维度: {', '.join(regressed)}，请特别关注这些方面。")
    return "\n".join(lines)
