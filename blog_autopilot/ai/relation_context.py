"""关联上下文组装 + 内链覆盖日志"""

import logging

from blog_autopilot.models import AssociationResult

logger = logging.getLogger("blog-autopilot")


def build_relation_context(
    associations: list[AssociationResult],
) -> dict[str, str]:
    """
    将关联查询结果按强度分组，格式化为 Prompt 上下文。

    返回:
        {"strong_relations": "...", "medium_relations": "...", "weak_relations": "..."}
    """
    groups: dict[str, list[str]] = {
        "强关联": [],
        "中关联": [],
        "弱关联": [],
    }

    for assoc in associations:
        level = assoc.relation_level
        if level in groups:
            entry = (
                f"  {len(groups[level]) + 1}. "
                f"《{assoc.article.title}》"
            )
            if assoc.article.url:
                entry += f"\n     链接: {assoc.article.url}"
            # 标签和相似度元数据
            tags = assoc.article.tags
            entry += (
                f"\n     标签: {tags.tag_magazine} / {tags.tag_science}"
                f" / {tags.tag_topic} / {tags.tag_content}"
            )
            entry += f"\n     相似度: {assoc.similarity:.0%}"
            if assoc.article.created_at:
                entry += f"\n     发布时间: {assoc.article.created_at:%Y-%m-%d}"
            # 三级回退：summary → content_excerpt → tg_promo
            if assoc.article.summary:
                desc = f"[摘要] {assoc.article.summary}"
            elif assoc.article.content_excerpt:
                desc = f"[摘录] {assoc.article.content_excerpt}"
            elif assoc.article.tg_promo:
                desc = f"[推广] {assoc.article.tg_promo}"
            else:
                desc = f"[标题] {assoc.article.title}"
            entry += f"\n     {desc}"
            groups[level].append(entry)

    return {
        "strong_relations": "\n".join(groups["强关联"]) if groups["强关联"] else "",
        "medium_relations": "\n".join(groups["中关联"]) if groups["中关联"] else "",
        "weak_relations": "\n".join(groups["弱关联"]) if groups["弱关联"] else "",
    }


def _log_link_coverage(
    html_body: str,
    associations: list[AssociationResult],
) -> None:
    """检查生成的 HTML 中内链覆盖率并记录日志"""
    linkable = [a for a in associations if a.article.url]
    if not linkable:
        return
    linked = sum(1 for a in linkable if a.article.url in html_body)
    logger.info(f"内链覆盖: {linked}/{len(linkable)} 篇关联文章已生成内链")
    if linked == 0 and len(linkable) >= 2:
        logger.warning("AI 未生成任何内链，可能需要调整提示词")
