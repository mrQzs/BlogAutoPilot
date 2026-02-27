"""审核反馈学习模块 — 从历史审核数据中提取校准信息和高质量示例"""

import logging
from dataclasses import dataclass, field

from blog_autopilot.constants import (
    REVIEW_CALIBRATION_SAMPLE_SIZE,
    REVIEW_EXEMPLAR_COUNT,
    REVIEW_EXEMPLAR_MIN_SCORE,
    REVIEW_INFLATION_WARNING_THRESHOLD,
)

logger = logging.getLogger("blog-autopilot")


def _pct(part: int, total: int) -> str:
    """百分比格式化，total 为 0 时返回 '0%'"""
    if total == 0:
        return "0%"
    return f"{round(part * 100 / total)}%"


@dataclass(frozen=True)
class ReviewCalibration:
    """审核校准数据：历史评分分布 + 高质量文章示例"""

    # 统计信息
    sample_count: int = 0
    avg_consistency: float = 0.0
    avg_factuality: float = 0.0
    avg_readability: float = 0.0
    avg_ai_cliche: float = 0.0
    avg_overall: float = 0.0
    std_overall: float = 0.0

    # 各维度标准差
    std_consistency: float = 0.0
    std_factuality: float = 0.0
    std_readability: float = 0.0
    std_ai_cliche: float = 0.0

    # 判定分布
    verdict_pass: int = 0
    verdict_rewrite: int = 0
    verdict_draft: int = 0

    # 高分文章示例 (title, score, summary)
    exemplars: tuple[dict, ...] = ()

    @property
    def has_stats(self) -> bool:
        return self.sample_count > 0

    @property
    def has_exemplars(self) -> bool:
        return len(self.exemplars) > 0


def fetch_calibration(database, category_name: str | None = None) -> ReviewCalibration:
    """
    从数据库获取审核校准数据。

    包含两部分：
    1. 历史评分统计（平均分、标准差、判定分布）
    2. 高分文章示例（标题 + 摘要）
    """
    stats = database.fetch_review_stats(
        category_name=category_name,
        limit=REVIEW_CALIBRATION_SAMPLE_SIZE,
    )

    exemplar_rows = database.fetch_high_score_articles(
        min_score=REVIEW_EXEMPLAR_MIN_SCORE,
        category_name=category_name,
        limit=REVIEW_EXEMPLAR_COUNT,
    )

    if not stats:
        if exemplar_rows:
            return ReviewCalibration(exemplars=tuple(exemplar_rows))
        return ReviewCalibration()

    return ReviewCalibration(
        sample_count=int(stats["count"]),
        avg_consistency=float(stats.get("avg_consistency") or 0),
        avg_factuality=float(stats.get("avg_factuality") or 0),
        avg_readability=float(stats.get("avg_readability") or 0),
        avg_ai_cliche=float(stats.get("avg_ai_cliche") or 0),
        avg_overall=float(stats.get("avg_overall") or 0),
        std_overall=float(stats.get("std_overall") or 0),
        std_consistency=float(stats.get("std_consistency") or 0),
        std_factuality=float(stats.get("std_factuality") or 0),
        std_readability=float(stats.get("std_readability") or 0),
        std_ai_cliche=float(stats.get("std_ai_cliche") or 0),
        verdict_pass=int(stats.get("verdict_pass") or 0),
        verdict_rewrite=int(stats.get("verdict_rewrite") or 0),
        verdict_draft=int(stats.get("verdict_draft") or 0),
        exemplars=tuple(exemplar_rows),
    )


def format_review_calibration_context(
    calibration: ReviewCalibration,
    is_self_review: bool = False,
) -> str:
    """
    将校准数据格式化为审核提示词的补充段落。

    注入到 review_system.txt 末尾，帮助审核模型校准评分基线。

    Args:
        is_self_review: 是否已启用自审偏差补偿（避免重复"压低评分"指令）
    """
    if not calibration.has_stats:
        return ""

    total = calibration.sample_count
    verdict_total = calibration.verdict_pass + calibration.verdict_rewrite + calibration.verdict_draft
    lines = [
        "",
        "═══════════════════════════",
        "  历史评分校准参考",
        "═══════════════════════════",
        "",
        f"以下是该分类最近 {total} 篇文章的审核评分分布，"
        "供你校准评分基线（避免所有文章都打相近的分数）：",
        "",
        "各维度平均分 (±标准差):",
        f"- 一致性: {calibration.avg_consistency} ±{calibration.std_consistency}",
        f"- 事实性: {calibration.avg_factuality} ±{calibration.std_factuality}",
        f"- 可读性: {calibration.avg_readability} ±{calibration.std_readability}",
        f"- AI痕迹: {calibration.avg_ai_cliche} ±{calibration.std_ai_cliche}",
        f"- 综合分: {calibration.avg_overall} ±{calibration.std_overall}",
        "",
        f"判定分布 ({verdict_total} 篇有判定结果):",
        f"- 通过: {calibration.verdict_pass} 篇 ({_pct(calibration.verdict_pass, verdict_total)})",
        f"- 重写: {calibration.verdict_rewrite} 篇 ({_pct(calibration.verdict_rewrite, verdict_total)})",
        f"- 草稿: {calibration.verdict_draft} 篇 ({_pct(calibration.verdict_draft, verdict_total)})",
        "",
        "请注意：",
        "- 如果你发现自己给出的分数总是接近平均值，请有意识地拉开区分度",
        "- 真正优秀的文章应该得到 8-9 分，有明显问题的文章应该低于 5 分",
        "- 标准差反映了历史评分的离散程度，你的评分也应保持类似的区分度",
    ]

    if calibration.avg_overall >= REVIEW_INFLATION_WARNING_THRESHOLD:
        lines.append("")
        if is_self_review:
            lines.append(
                f"⚠️ 评分膨胀警告: 历史平均综合分 {calibration.avg_overall} 偏高 "
                f"(阈值 {REVIEW_INFLATION_WARNING_THRESHOLD})。"
                "注意：自审偏差补偿已在阈值层面生效，此处无需额外压低评分，"
                "但仍需保持评分的区分度。"
            )
        else:
            lines.append(
                f"⚠️ 评分膨胀警告: 历史平均综合分 {calibration.avg_overall} 偏高 "
                f"(阈值 {REVIEW_INFLATION_WARNING_THRESHOLD})，请有意识地压低评分，"
                "避免所有文章都获得高分导致审核失去筛选作用。"
            )

    return "\n".join(lines)


def format_exemplar_context(calibration: ReviewCalibration) -> str:
    """
    将高分文章示例格式化为写作提示词的补充段落。

    注入到 writer_system.txt 末尾，引导 AI 学习高质量文章的模式。
    """
    if not calibration.has_exemplars:
        return ""

    lines = [
        "",
        "═══════════════════════════",
        "  高质量文章参考",
        "═══════════════════════════",
        "",
        "以下是该分类中审核评分最高的文章，它们的写作模式值得参考：",
    ]

    for i, ex in enumerate(calibration.exemplars, 1):
        title = ex.get("article_title", "未知标题")
        score = ex.get("overall_score", "?")
        summary = ex.get("summary", "")
        article_summary = ex.get("article_summary", "")
        lines.append(f"  {i}. 《{title}》(综合分: {score})")
        if article_summary:
            lines.append(f"     内容摘要: {article_summary}")
        if summary:
            lines.append(f"     审核评价: {summary}")

    lines.append("")
    lines.append("请参考这些文章的优点，在写作中保持类似的质量水准。")
    return "\n".join(lines)
