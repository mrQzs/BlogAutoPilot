"""文章系列检测与导航生成模块"""

from __future__ import annotations

import html as _html
import logging
import math
import re
import uuid
from typing import TYPE_CHECKING

from blog_autopilot.constants import (
    SERIES_LOOKBACK_DAYS,
    SERIES_NAV_CSS_CLASS,
    SERIES_NEW_THRESHOLD,
    SERIES_SIMILARITY_THRESHOLD,
    SERIES_TITLE_PATTERN_THRESHOLD,
    SERIES_TITLE_PATTERNS,
)
from blog_autopilot.exceptions import SeriesDetectionError
from blog_autopilot.models import ArticleRecord, SeriesInfo, TagSet

if TYPE_CHECKING:
    from blog_autopilot.db import Database

logger = logging.getLogger("blog-autopilot")


# ── 标题模式检测 ──

_COMPILED_PATTERNS = tuple(re.compile(p) for p in SERIES_TITLE_PATTERNS)


def has_series_title_pattern(title: str) -> bool:
    """检查标题是否包含系列模式关键词"""
    return any(p.search(title) for p in _COMPILED_PATTERNS)


# ── 相似度计算 ──

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度（数值稳定版本）"""
    dot = math.fsum(x * y for x, y in zip(a, b))
    norm_a = math.fsum(x * x for x in a) ** 0.5
    norm_b = math.fsum(x * x for x in b) ** 0.5
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _avg_similarity(
    embedding: list[float], member_embeddings: list[list[float]],
) -> float:
    """计算新文章与系列成员的平均相似度"""
    if not member_embeddings:
        return 0.0
    sims = [_cosine_similarity(embedding, m) for m in member_embeddings]
    return sum(sims) / len(sims)


# ── 系列检测 ──

def _llm_series_check(
    new_title: str,
    candidate_titles: list[str],
    ai_writer,
) -> bool:
    """使用 LLM 判断标题是否属于同一系列"""
    if not candidate_titles or ai_writer is None:
        return False

    try:
        from blog_autopilot.ai_writer import PROMPTS_DIR
        import json

        system_prompt = (PROMPTS_DIR / "series_check_system.txt").read_text(encoding="utf-8")
        user_template = (PROMPTS_DIR / "series_check_user.txt").read_text(encoding="utf-8")

        titles_text = "\n".join(f"- {t}" for t in candidate_titles[:10])
        user_prompt = user_template.format(
            new_title=new_title,
            candidate_titles=titles_text,
        )

        response = ai_writer.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            max_tokens=200,
        )

        # 解析 JSON 响应
        text = response.strip()
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            data = json.loads(text[first_brace:last_brace + 1])
            is_series = data.get("is_series", False)
            confidence = float(data.get("confidence", 0))
            reason = data.get("reason", "")
            logger.info(
                f"LLM 系列判断: is_series={is_series}, "
                f"confidence={confidence:.2f}, reason={reason}"
            )
            return is_series and confidence >= 0.7
    except Exception as e:
        logger.warning(f"LLM 系列检测失败: {e}")

    return False


def detect_series(
    db: Database,
    tags: TagSet,
    embedding: list[float],
    title: str,
    ai_writer=None,
) -> SeriesInfo | None:
    """
    检测文章是否属于某个系列。

    流程：
    1. 精确匹配 top-3 标签查找候选系列
    2. 计算与候选系列成员的平均相似度
    3. 超过阈值 → 加入系列
    4. 无匹配 → 查找近期相似文章，尝试创建新系列
    5. 无匹配 → 返回 None
    """
    try:
        return _detect_series_impl(db, tags, embedding, title, ai_writer)
    except SeriesDetectionError:
        raise
    except Exception as e:
        raise SeriesDetectionError(f"系列检测失败: {e}") from e


def _detect_series_impl(
    db: Database,
    tags: TagSet,
    embedding: list[float],
    title: str,
    ai_writer=None,
) -> SeriesInfo | None:
    title_has_pattern = has_series_title_pattern(title)
    threshold = (
        SERIES_TITLE_PATTERN_THRESHOLD if title_has_pattern
        else SERIES_SIMILARITY_THRESHOLD
    )

    # 1. 查找匹配 top-3 标签的候选系列
    candidates = db.detect_series_candidates(
        tags.tag_magazine, tags.tag_science, tags.tag_topic,
    )

    # 缓存每个候选系列的 embeddings 和相似度，避免 LLM 回退时重复查询
    candidate_cache: dict[str, tuple[list[list[float]], float]] = {}

    for series in candidates:
        member_embeddings = db.get_series_article_embeddings(series.id)
        avg_sim = _avg_similarity(embedding, member_embeddings)
        candidate_cache[series.id] = (member_embeddings, avg_sim)
        if avg_sim >= threshold:
            members = db.get_series_articles(series.id)
            new_order = len(members) + 1
            prev_article = members[-1] if members else None
            logger.info(
                f"匹配到系列《{series.title}》"
                f"(相似度: {avg_sim:.2f}, 位置: {new_order})"
            )
            return SeriesInfo(
                series_id=series.id,
                series_title=series.title,
                order=new_order,
                total=new_order,
                prev_article=prev_article,
            )

    # 1.5 LLM 辅助判断：对接近阈值的候选系列做二次确认
    if candidates and ai_writer:
        for series in candidates:
            _, avg_sim = candidate_cache[series.id]
            # 相似度在 [threshold-0.1, threshold) 区间的候选，用 LLM 二次确认
            if avg_sim >= threshold - 0.1:
                members = db.get_series_articles(series.id)
                member_titles = [m.title for m in members]
                if _llm_series_check(title, member_titles, ai_writer):
                    new_order = len(members) + 1
                    prev_article = members[-1] if members else None
                    logger.info(
                        f"LLM 确认匹配系列《{series.title}》"
                        f"(向量相似度: {avg_sim:.2f}, 位置: {new_order})"
                    )
                    return SeriesInfo(
                        series_id=series.id,
                        series_title=series.title,
                        order=new_order,
                        total=new_order,
                        prev_article=prev_article,
                    )

    # 2. 无匹配系列 → 查找近期相似文章，尝试创建新系列
    similar = db.find_recent_similar_articles(
        tags.tag_magazine, tags.tag_science, tags.tag_topic,
        embedding,
        lookback_days=SERIES_LOOKBACK_DAYS,
        threshold=SERIES_NEW_THRESHOLD,
    )

    if similar:
        # 至少有 1 篇相似文章 → 与新文章一起构成系列
        series_id = str(uuid.uuid4())[:12]
        series_title = f"{tags.tag_topic}系列"
        db.create_series(
            series_id, series_title,
            tags.tag_magazine, tags.tag_science, tags.tag_topic,
        )
        # 按创建时间排序后分配 series_order（最早的排第一）
        similar_sorted = sorted(
            similar,
            key=lambda r: r.get("created_at") or "",
        )
        for idx, match in enumerate(similar_sorted, 1):
            db.add_to_series(match["id"], series_id, idx)
        new_order = len(similar_sorted) + 1

        # 上一篇 = 时间最晚的已有文章（series_order 最大）
        prev_match = similar_sorted[-1]
        prev_article = db.get_article(prev_match["id"])
        logger.info(
            f"创建新系列《{series_title}》"
            f"(纳入 {len(similar_sorted)} 篇已有文章, 新文章位置: {new_order})"
        )
        return SeriesInfo(
            series_id=series_id,
            series_title=series_title,
            order=new_order,
            total=new_order,
            prev_article=prev_article,
        )

    return None


# ── 导航 HTML 生成 ──

def build_series_navigation(series_info: SeriesInfo) -> str:
    """生成系列导航 HTML 块"""
    prev_link = ""
    if series_info.prev_article and series_info.prev_article.url:
        prev_title = _html.escape(series_info.prev_article.title)
        prev_url = _html.escape(series_info.prev_article.url)
        prev_link = (
            f'    <a href="{prev_url}" style="color:#1a73e8;text-decoration:none;">'
            f'\u2190 上一篇：{prev_title}</a>\n'
        )

    series_title = _html.escape(series_info.series_title)
    return (
        f'<div class="{SERIES_NAV_CSS_CLASS}" style="margin:2em 0;padding:1.5em;'
        f'border:1px solid #e0e0e0;border-radius:8px;background:#f9f9f9;">\n'
        f'  <p style="margin:0 0 0.8em;font-weight:bold;color:#333;">\n'
        f'    \U0001f4da 本文属于系列：《{series_title}》'
        f'（第 {series_info.order}/{series_info.total} 篇）\n'
        f'  </p>\n'
        f'  <div style="display:flex;justify-content:space-between;gap:1em;">\n'
        f'{prev_link}'
        f'  </div>\n'
        f'</div>'
    )


def inject_series_navigation(html_body: str, series_info: SeriesInfo) -> str:
    """将系列导航注入到文章 HTML 末尾"""
    nav_html = build_series_navigation(series_info)
    return html_body.rstrip() + "\n\n" + nav_html


# ── 回溯更新导航 ──

def replace_series_navigation(
    html_content: str,
    new_nav_html: str,
) -> str:
    """替换已有的系列导航块，或追加新导航"""
    pattern = (
        rf'<div class="{re.escape(SERIES_NAV_CSS_CLASS)}"[^>]*>.*?</div>\s*</div>'
    )
    match = re.search(pattern, html_content, re.DOTALL)
    if match:
        return html_content[:match.start()] + new_nav_html + html_content[match.end():]
    return html_content.rstrip() + "\n\n" + new_nav_html


def build_backfill_navigation(
    series_title: str,
    order: int,
    total: int,
    prev_article: ArticleRecord | None,
    next_article_title: str,
    next_article_url: str,
) -> str:
    """为已发布文章生成包含下一篇链接的导航 HTML"""
    prev_link = ""
    if prev_article and prev_article.url:
        prev_link = (
            f'    <a href="{_html.escape(prev_article.url)}" style="color:#1a73e8;text-decoration:none;">'
            f'\u2190 上一篇：{_html.escape(prev_article.title)}</a>\n'
        )

    next_link = (
        f'    <a href="{_html.escape(next_article_url)}" style="color:#1a73e8;text-decoration:none;">'
        f'下一篇：{_html.escape(next_article_title)} \u2192</a>\n'
    )

    return (
        f'<div class="{SERIES_NAV_CSS_CLASS}" style="margin:2em 0;padding:1.5em;'
        f'border:1px solid #e0e0e0;border-radius:8px;background:#f9f9f9;">\n'
        f'  <p style="margin:0 0 0.8em;font-weight:bold;color:#333;">\n'
        f'    \U0001f4da 本文属于系列：《{_html.escape(series_title)}》'
        f'（第 {order}/{total} 篇）\n'
        f'  </p>\n'
        f'  <div style="display:flex;justify-content:space-between;gap:1em;">\n'
        f'{prev_link}'
        f'{next_link}'
        f'  </div>\n'
        f'</div>'
    )

