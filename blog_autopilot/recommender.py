"""智能选题推荐模块 — 分析内容缺口并生成选题建议"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import Settings
from blog_autopilot.constants import (
    RECOMMEND_DEFAULT_TOP_N,
    RECOMMEND_FRONTIER_MULTIPLIER,
    RECOMMEND_MIN_ARTICLES,
    RECOMMEND_RECENT_TITLES_COUNT,
    RECOMMEND_RECENCY_CAP,
    RECOMMEND_SPARSE_THRESHOLD,
    RECOMMEND_TAG_GAP_WEIGHT,
    RECOMMEND_VECTOR_GAP_WEIGHT,
)
from blog_autopilot.db import Database
from blog_autopilot.exceptions import AIResponseParseError, RecommendationError
from blog_autopilot.models import ContentGap, TagSet, TopicRecommendation

logger = logging.getLogger("blog-autopilot")


class TopicRecommender:
    """智能选题推荐器，组合 Database + AIWriter"""

    def __init__(self, settings: Settings) -> None:
        self._db = Database(settings.database)
        self._writer = AIWriter(settings.ai)
        self._article_count = 0
        self._tag_combo_count = 0

    def recommend(
        self, top_n: int = RECOMMEND_DEFAULT_TOP_N
    ) -> list[TopicRecommendation]:
        """
        生成选题推荐。

        1. 前置检查文章数
        2. 标签缺口分析
        3. 向量空间分析
        4. 合并缺口
        5. AI 生成推荐

        抛出:
            RecommendationError: 文章数不足或分析失败
        """
        self._article_count = self._db.count_articles()
        if self._article_count < RECOMMEND_MIN_ARTICLES:
            raise RecommendationError(
                f"文章数不足: {self._article_count} < {RECOMMEND_MIN_ARTICLES}，"
                f"无法进行有效的选题推荐"
            )

        tag_rows = self._db.fetch_all_tags_with_dates()
        recent_titles = self._db.fetch_recent_titles(
            RECOMMEND_RECENT_TITLES_COUNT
        )

        tag_gaps = self._analyze_tag_gaps(tag_rows)
        vector_gaps = self._analyze_vector_gaps(top_n)
        merged = self._merge_gaps(tag_gaps, vector_gaps, top_n)

        return self._generate_recommendations(
            merged, recent_titles, top_n
        )

    def _analyze_tag_gaps(self, tag_rows: list[dict]) -> list[ContentGap]:
        """
        标签缺口分析：统计二级/三级标签组合频次，
        缺口分数 = 1/(count+1) × 时间衰减权重。
        """
        now = datetime.now(timezone.utc)

        # 统计 (magazine, science) 二级组合
        combo2_counts: Counter = Counter()
        combo3_counts: Counter = Counter()
        combo2_latest: dict[tuple, datetime] = {}
        combo3_latest: dict[tuple, datetime] = {}

        for row in tag_rows:
            mag = row["tag_magazine"]
            sci = row["tag_science"]
            topic = row["tag_topic"]
            created = row.get("created_at")

            key2 = (mag, sci)
            key3 = (mag, sci, topic)

            combo2_counts[key2] += 1
            combo3_counts[key3] += 1

            if created:
                if hasattr(created, "tzinfo") and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if key2 not in combo2_latest or created > combo2_latest[key2]:
                    combo2_latest[key2] = created
                if key3 not in combo3_latest or created > combo3_latest[key3]:
                    combo3_latest[key3] = created

        self._tag_combo_count = len(combo2_counts)

        gaps = []
        # 三级组合缺口（更细粒度）
        for key3, count in combo3_counts.items():
            staleness_weight = 1.0
            if key3 in combo3_latest:
                days = (now - combo3_latest[key3]).days
                staleness_weight = min(days / 30.0, RECOMMEND_RECENCY_CAP)
                staleness_weight = max(staleness_weight, 0.1)

            score = (1.0 / (count + 1)) * staleness_weight
            mag, sci, topic = key3
            gaps.append(ContentGap(
                gap_type="tag_gap",
                description=f"{mag}/{sci}/{topic} (出现 {count} 次)",
                gap_score=score,
                tags=TagSet(
                    tag_magazine=mag, tag_science=sci,
                    tag_topic=topic, tag_content="",
                ),
            ))

        gaps.sort(key=lambda g: g.gap_score, reverse=True)
        logger.info(f"标签缺口分析完成: {len(gaps)} 个组合")
        return gaps

    def _analyze_vector_gaps(self, top_n: int) -> list[ContentGap]:
        """
        向量空间分析：找到离质心最远且最近邻相似度低的文章，
        这些区域代表内容稀疏地带。
        """
        centroid = self._db.compute_centroid()
        if centroid is None:
            logger.warning("无法计算质心向量，跳过向量空间分析")
            return []

        frontier_count = top_n * RECOMMEND_FRONTIER_MULTIPLIER
        frontiers = self._db.find_frontier_articles(centroid, frontier_count)

        gaps = []
        for row in frontiers:
            nn_sim = float(row["nn_similarity"])
            if nn_sim >= RECOMMEND_SPARSE_THRESHOLD:
                continue

            dist = float(row["dist_centroid"])
            score = dist * (1.0 - nn_sim)

            gaps.append(ContentGap(
                gap_type="vector_gap",
                description=(
                    f"向量稀疏区域 (距质心 {dist:.3f}, "
                    f"最近邻相似度 {nn_sim:.3f})"
                ),
                gap_score=score,
                tags=TagSet(
                    tag_magazine=row["tag_magazine"],
                    tag_science=row["tag_science"],
                    tag_topic=row["tag_topic"],
                    tag_content=row["tag_content"],
                ),
                reference_title=row["title"],
            ))

        gaps.sort(key=lambda g: g.gap_score, reverse=True)
        logger.info(f"向量空间分析完成: {len(gaps)} 个稀疏区域")
        return gaps

    @staticmethod
    def _merge_gaps(
        tag_gaps: list[ContentGap],
        vector_gaps: list[ContentGap],
        top_n: int,
    ) -> list[ContentGap]:
        """
        合并标签缺口和向量缺口：
        - 各自 min-max 归一化到 [0,1]
        - 按 tag 组合 key 去重合并
        - 加权求和后取 top_n
        """
        if not tag_gaps and not vector_gaps:
            return []

        def _normalize(gaps: list[ContentGap]) -> list[ContentGap]:
            if not gaps:
                return []
            scores = [g.gap_score for g in gaps]
            lo, hi = min(scores), max(scores)
            if hi == lo:
                return [
                    ContentGap(
                        gap_type=g.gap_type,
                        description=g.description,
                        gap_score=1.0,
                        tags=g.tags,
                        reference_title=g.reference_title,
                    )
                    for g in gaps
                ]
            return [
                ContentGap(
                    gap_type=g.gap_type,
                    description=g.description,
                    gap_score=(g.gap_score - lo) / (hi - lo),
                    tags=g.tags,
                    reference_title=g.reference_title,
                )
                for g in gaps
            ]

        norm_tag = _normalize(tag_gaps)
        norm_vec = _normalize(vector_gaps)

        # 按 (magazine, science, topic) 合并
        merged: dict[tuple, ContentGap] = {}

        def _accumulate(
            gaps: list[ContentGap], weight: float,
        ) -> None:
            for g in gaps:
                if g.tags:
                    key = (
                        g.tags.tag_magazine,
                        g.tags.tag_science,
                        g.tags.tag_topic,
                    )
                else:
                    key = (g.description,)
                score = g.gap_score * weight
                if key in merged:
                    old = merged[key]
                    merged[key] = ContentGap(
                        gap_type="merged",
                        description=f"{old.description} + {g.description}",
                        gap_score=old.gap_score + score,
                        tags=old.tags or g.tags,
                        reference_title=old.reference_title or g.reference_title,
                    )
                else:
                    merged[key] = ContentGap(
                        gap_type=g.gap_type,
                        description=g.description,
                        gap_score=score,
                        tags=g.tags,
                        reference_title=g.reference_title,
                    )

        _accumulate(norm_tag, RECOMMEND_TAG_GAP_WEIGHT)
        _accumulate(norm_vec, RECOMMEND_VECTOR_GAP_WEIGHT)

        result = sorted(
            merged.values(), key=lambda g: g.gap_score, reverse=True
        )
        return result[:top_n]

    def _generate_recommendations(
        self,
        gaps: list[ContentGap],
        recent_titles: list[str],
        top_n: int,
    ) -> list[TopicRecommendation]:
        """将缺口数据发给 AI，生成具体选题推荐"""
        if not gaps:
            logger.warning("无内容缺口数据，跳过 AI 推荐")
            return []

        gaps_lines = []
        for i, gap in enumerate(gaps, 1):
            line = f"{i}. [{gap.gap_type}] {gap.description} (分数: {gap.gap_score:.3f})"
            if gap.tags:
                line += (
                    f"\n   标签: {gap.tags.tag_magazine}/"
                    f"{gap.tags.tag_science}/{gap.tags.tag_topic}"
                )
            if gap.reference_title:
                line += f"\n   参考文章: 《{gap.reference_title}》"
            gaps_lines.append(line)

        gaps_description = "\n".join(gaps_lines)
        titles_text = "\n".join(
            f"- {t}" for t in recent_titles
        ) if recent_titles else "（暂无已发布文章）"

        system_prompt = self._writer._load_prompt("recommend_system.txt")
        user_template = self._writer._load_prompt("recommend_user.txt")
        user_prompt = user_template.format(
            top_n=top_n,
            gaps_description=gaps_description,
            existing_titles=titles_text,
        )

        response = self._writer.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._writer._settings.model_promo,
            max_tokens=self._writer._settings.promo_max_tokens,
        )

        return self._parse_recommendations(response)

    @staticmethod
    def _parse_recommendations(response: str) -> list[TopicRecommendation]:
        """解析 AI 返回的 JSON 数组为 TopicRecommendation 列表"""
        text = response.strip()

        # 尝试直接解析
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 markdown 代码块
        if data is None:
            import re
            code_block = re.search(
                r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
            )
            if code_block:
                try:
                    data = json.loads(code_block.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # 尝试提取 [ ... ] 子串
        if data is None:
            first = text.find("[")
            last = text.rfind("]")
            if first != -1 and last > first:
                try:
                    data = json.loads(text[first:last + 1])
                except json.JSONDecodeError:
                    pass

        if not isinstance(data, list):
            raise AIResponseParseError(
                f"推荐结果解析失败，期望 JSON 数组。响应前 200 字符: {text[:200]}"
            )

        recommendations = []
        for item in data:
            if not isinstance(item, dict):
                continue
            tags_data = item.get("suggested_tags", {})
            try:
                tags = TagSet(
                    tag_magazine=str(tags_data.get("tag_magazine", "")),
                    tag_science=str(tags_data.get("tag_science", "")),
                    tag_topic=str(tags_data.get("tag_topic", "")),
                    tag_content=str(tags_data.get("tag_content", "")),
                )
            except Exception:
                continue

            priority = str(item.get("priority", "medium")).lower()
            if priority not in ("high", "medium", "low"):
                priority = "medium"

            recommendations.append(TopicRecommendation(
                topic=str(item.get("topic", "")),
                rationale=str(item.get("rationale", "")),
                suggested_tags=tags,
                priority=priority,
            ))

        return recommendations

    def format_output(
        self, recommendations: list[TopicRecommendation]
    ) -> str:
        """格式化推荐结果为终端输出"""
        lines = [
            "",
            "=" * 60,
            "  智能选题推荐",
            "=" * 60,
            f"  文章总数: {self._article_count}  |  "
            f"标签组合数: {self._tag_combo_count}",
            "-" * 60,
        ]

        if not recommendations:
            lines.append("  暂无推荐结果。")
        else:
            priority_icons = {
                "high": "[!!!]",
                "medium": "[!! ]",
                "low": "[!  ]",
            }
            for i, rec in enumerate(recommendations, 1):
                icon = priority_icons.get(rec.priority, "[   ]")
                lines.append(f"\n  {i}. {icon} {rec.topic}")
                lines.append(f"     理由: {rec.rationale}")
                tags = rec.suggested_tags
                lines.append(
                    f"     标签: {tags.tag_magazine} / {tags.tag_science} / "
                    f"{tags.tag_topic} / {tags.tag_content}"
                )
                lines.append(f"     优先级: {rec.priority}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)
