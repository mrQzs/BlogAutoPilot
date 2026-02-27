"""综述类文章生成模块 — 从同主题文章组自动生成综述"""

import logging
from itertools import combinations

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import Settings
from blog_autopilot.constants import (
    SURVEY_LOOKBACK_DAYS,
    SURVEY_MAX_SOURCE_ARTICLES,
    SURVEY_MIN_ARTICLES,
    SURVEY_TOPIC_SIMILARITY,
)
from blog_autopilot.db import Database
from blog_autopilot.exceptions import SurveyGenerationError
from blog_autopilot.models import SurveyResult

logger = logging.getLogger("blog-autopilot")


class SurveyGenerator:
    """综述文章生成器，组合 Database + AIWriter + Embedding 模糊分组"""

    def __init__(self, settings: Settings) -> None:
        if not settings.database or not settings.database.user:
            raise SurveyGenerationError("综述生成需要数据库配置")
        self._db = Database(settings.database)
        self._writer = AIWriter(settings.ai)
        self._embedding_client = None
        if settings.embedding and settings.embedding.api_key:
            try:
                from blog_autopilot.embedding import EmbeddingClient
                self._embedding_client = EmbeddingClient(settings.embedding)
            except Exception as e:
                logger.warning(f"综述 embedding 初始化失败，回退精确匹配: {e}")

    def _cluster_topics(
        self, rows: list[dict],
    ) -> list[dict]:
        """
        对同 (magazine, science) 下的 topic 做 embedding 模糊聚类。
        返回合并后的候选列表，每项含 tag_topics (list) 和 article_count。
        """
        from blog_autopilot.series import _cosine_similarity

        # 按 (magazine, science) 分桶
        buckets: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            key = (r["tag_magazine"], r["tag_science"])
            buckets.setdefault(key, []).append(r)

        results = []
        for (mag, sci), items in buckets.items():
            topics = [it["tag_topic"] for it in items]
            count_map = {it["tag_topic"]: it["article_count"] for it in items}

            if not self._embedding_client or len(topics) < 2:
                # 无 embedding，按原始精确匹配
                for it in items:
                    results.append({
                        "tag_magazine": mag,
                        "tag_science": sci,
                        "tag_topic": it["tag_topic"],
                        "tag_topics": [it["tag_topic"]],
                        "article_count": it["article_count"],
                    })
                continue

            # union-find
            parent: dict[str, str] = {t: t for t in topics}

            def find(x: str) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: str, b: str) -> None:
                parent[find(a)] = find(b)

            # 获取 embedding（拼接 science 提供上下文，提升短文本相似度）
            emb_map: dict[str, list[float]] = {}
            for t in topics:
                try:
                    emb_map[t] = self._embedding_client.get_embedding(
                        f"{sci} {t}",
                    )
                except Exception as e:
                    logger.warning(f"topic '{t}' embedding 失败: {e}")

            # 两两比较，相似则合并
            emb_topics = list(emb_map.keys())
            for i, j in combinations(range(len(emb_topics)), 2):
                a, b = emb_topics[i], emb_topics[j]
                sim = _cosine_similarity(emb_map[a], emb_map[b])
                if sim >= SURVEY_TOPIC_SIMILARITY:
                    union(a, b)

            # 收集分组
            groups: dict[str, list[str]] = {}
            for t in topics:
                root = find(t) if t in parent else t
                groups.setdefault(root, []).append(t)

            for members in groups.values():
                total = sum(count_map.get(t, 0) for t in members)
                # canonical = 频率最高的 topic
                members.sort(
                    key=lambda t: count_map.get(t, 0), reverse=True,
                )
                results.append({
                    "tag_magazine": mag,
                    "tag_science": sci,
                    "tag_topic": members[0],
                    "tag_topics": members,
                    "article_count": total,
                })

        results.sort(key=lambda r: r["article_count"], reverse=True)
        return results

    def detect_candidates(
        self,
        min_articles: int = SURVEY_MIN_ARTICLES,
        lookback_days: int = SURVEY_LOOKBACK_DAYS,
    ) -> list[dict]:
        """检测可生成综述的文章组（embedding 模糊分组后过滤）"""
        raw = self._db.find_survey_candidates(
            min_articles=min_articles,
            lookback_days=lookback_days,
        )
        clustered = self._cluster_topics(raw)
        # 过滤：组总数 >= min_articles
        candidates = [c for c in clustered if c["article_count"] >= min_articles]
        logger.info(f"综述候选检测完成: {len(candidates)} 个文章组")
        return candidates

    def generate(self, candidate: dict) -> SurveyResult:
        """
        为指定候选组生成综述文章。

        Args:
            candidate: detect_candidates() 返回的 dict，
                       含 tag_magazine, tag_science, tag_topics, article_count
        """
        tag_mag = candidate["tag_magazine"]
        tag_sci = candidate["tag_science"]
        tag_tops = candidate.get("tag_topics") or [candidate["tag_topic"]]

        articles = self._db.fetch_articles_by_tags(
            tag_mag, tag_sci, tag_tops,
            limit=SURVEY_MAX_SOURCE_ARTICLES,
        )
        if len(articles) < SURVEY_MIN_ARTICLES:
            raise SurveyGenerationError(
                f"源文章不足: {len(articles)} < {SURVEY_MIN_ARTICLES}"
            )

        source_text = self._format_source_articles(articles)
        topic_tags = f"{tag_mag} / {tag_sci} / {' + '.join(tag_tops)}"

        system_prompt = self._writer._load_prompt("survey_system.txt")
        user_template = self._writer._load_prompt("survey_user.txt")
        user_prompt = user_template.format(
            topic_tags=topic_tags,
            article_count=len(articles),
            source_articles=source_text,
        )

        response = self._writer.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._writer._settings.model_writer,
            max_tokens=self._writer._settings.writer_max_tokens,
        )

        article = self._writer._parse_article_response(response)

        logger.info(
            f"综述生成完成 | 标题: {article.title} | "
            f"源文章: {len(articles)} 篇"
        )
        return SurveyResult(
            title=article.title,
            html_body=article.html_body,
            source_count=len(articles),
            tag_magazine=tag_mag,
            tag_science=tag_sci,
            tag_topic=tag_tops[0],
        )

    @staticmethod
    def _format_source_articles(articles: list[dict]) -> str:
        """将源文章列表格式化为提示词上下文"""
        lines = []
        for i, art in enumerate(articles, 1):
            title = art.get("title", "无标题")
            url = art.get("url", "")
            summary = art.get("summary") or art.get("tg_promo", "")
            created = art.get("created_at")

            entry = f"--- 文章 {i} ---"
            entry += f"\n标题: {title}"
            if url:
                entry += f"\nURL: {url}"
            if created:
                entry += f"\n发布时间: {created}"
            if summary:
                entry += f"\n摘要: {summary}"
            lines.append(entry)
        return "\n\n".join(lines)

    @staticmethod
    def format_candidates(candidates: list[dict]) -> str:
        """格式化候选列表为终端输出"""
        lines = [
            "",
            "=" * 60,
            "  综述文章候选组",
            "=" * 60,
        ]
        if not candidates:
            lines.append("  未发现可生成综述的文章组。")
        else:
            for i, c in enumerate(candidates, 1):
                topics = c.get("tag_topics") or [c["tag_topic"]]
                topic_str = " + ".join(topics)
                lines.append(
                    f"  {i}. {c['tag_magazine']} / {c['tag_science']}"
                    f" / {topic_str}  ({c['article_count']} 篇)"
                )
        lines.append("=" * 60)
        return "\n".join(lines)
