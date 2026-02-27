"""标签治理审计模块 — 分析标签频率、共现关系和语义近义词"""

import json
import logging
from collections import Counter
from itertools import combinations

from blog_autopilot.config import Settings
from pathlib import Path

from blog_autopilot.constants import (
    TAG_AUDIT_MIN_ARTICLES,
    TAG_AUDIT_MIN_TAG_COUNT,
    TAG_AUDIT_SIMILARITY_THRESHOLD,
    TAG_AUDIT_TOP_COOCCURRENCES,
    TAG_CONSISTENCY_NEIGHBORS,
)
from blog_autopilot.db import Database
from blog_autopilot.exceptions import TagAuditError
from blog_autopilot.models import (
    CooccurrencePair,
    SynonymSuggestion,
    TagAuditReport,
    TagSet,
    TagStats,
)

logger = logging.getLogger("blog-autopilot")

TAG_LEVELS = ("magazine", "science", "topic", "content")


class TagAuditor:
    """标签治理审计器，分析标签碎片化并生成合并建议"""

    def __init__(self, settings: Settings) -> None:
        self._db = Database(settings.database)
        self._embedding_client = None
        try:
            if settings.embedding.api_key.get_secret_value():
                from blog_autopilot.embedding import EmbeddingClient
                self._embedding_client = EmbeddingClient(settings.embedding)
        except Exception:
            pass
        self._article_count = 0

    def audit(self) -> TagAuditReport:
        """
        执行标签审计，返回审计报告。

        抛出:
            TagAuditError: 文章数不足
        """
        self._article_count = self._db.count_articles()
        if self._article_count < TAG_AUDIT_MIN_ARTICLES:
            raise TagAuditError(
                f"文章数不足: {self._article_count} < {TAG_AUDIT_MIN_ARTICLES}，"
                f"无法进行有效的标签审计"
            )

        tag_rows = self._db.fetch_all_tags_with_dates()
        tag_stats = self._collect_tag_stats(tag_rows)
        cooccurrences = self._build_cooccurrence(tag_rows)

        embedding_available = self._embedding_client is not None
        suggestions = []
        if embedding_available:
            suggestions = self._find_semantic_duplicates(tag_stats)

        suggestions = self._cross_check_existing(suggestions)

        unique_tags = {ts.tag for ts in tag_stats}
        return TagAuditReport(
            article_count=self._article_count,
            unique_tag_count=len(unique_tags),
            tag_stats=tuple(tag_stats),
            top_cooccurrences=tuple(cooccurrences),
            suggestions=tuple(suggestions),
            embedding_available=embedding_available,
        )

    @staticmethod
    def _collect_tag_stats(tag_rows: list[dict]) -> list[TagStats]:
        """按层级统计每个唯一标签的出现频率"""
        counters: dict[str, Counter] = {lv: Counter() for lv in TAG_LEVELS}
        for row in tag_rows:
            for lv in TAG_LEVELS:
                val = row.get(f"tag_{lv}", "")
                if val:
                    counters[lv][val] += 1

        stats = []
        for lv in TAG_LEVELS:
            for tag, count in counters[lv].most_common():
                stats.append(TagStats(tag=tag, level=lv, count=count))
        return stats

    @staticmethod
    def _build_cooccurrence(tag_rows: list[dict]) -> list[CooccurrencePair]:
        """构建标签共现矩阵，返回 top N 共现对"""
        pair_counter: Counter = Counter()
        for row in tag_rows:
            tags = []
            for lv in TAG_LEVELS:
                val = row.get(f"tag_{lv}", "")
                if val:
                    tags.append(val)
            for a, b in combinations(sorted(set(tags)), 2):
                pair_counter[(a, b)] += 1

        result = []
        for (a, b), count in pair_counter.most_common(TAG_AUDIT_TOP_COOCCURRENCES):
            result.append(CooccurrencePair(tag_a=a, tag_b=b, co_count=count))
        return result

    def _find_semantic_duplicates(
        self, tag_stats: list[TagStats],
    ) -> list[SynonymSuggestion]:
        """按层级分组，embedding 模糊聚类后合并计数，组总数 >= 阈值才生成建议"""
        from blog_autopilot.series import _cosine_similarity

        # 按层级分组，不预先过滤低频标签
        level_tags: dict[str, list[tuple[str, int]]] = {lv: [] for lv in TAG_LEVELS}
        for ts in tag_stats:
            level_tags[ts.level].append((ts.tag, ts.count))

        # union-find 辅助
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        suggestions = []
        for lv in TAG_LEVELS:
            tags_in_level = level_tags[lv]
            if len(tags_in_level) < 2:
                continue

            count_map = {t: c for t, c in tags_in_level}

            # 获取每个标签的 embedding
            tag_embeddings: dict[str, list[float]] = {}
            for tag, _ in tags_in_level:
                try:
                    emb = self._embedding_client.get_embedding(tag)
                    tag_embeddings[tag] = emb
                except Exception as e:
                    logger.warning(f"标签 '{tag}' embedding 失败: {e}")

            # 初始化 union-find
            parent.clear()
            for tag in tag_embeddings:
                parent[tag] = tag

            # 记录每对相似度
            pair_sim: dict[tuple[str, str], float] = {}
            tag_list = list(tag_embeddings.keys())
            for i, j in combinations(range(len(tag_list)), 2):
                a, b = tag_list[i], tag_list[j]
                sim = _cosine_similarity(
                    tag_embeddings[a], tag_embeddings[b],
                )
                if sim >= TAG_AUDIT_SIMILARITY_THRESHOLD:
                    union(a, b)
                    pair_sim[(a, b)] = round(sim, 4)

            # 收集分组
            groups: dict[str, list[str]] = {}
            for tag in tag_embeddings:
                root = find(tag)
                groups.setdefault(root, []).append(tag)

            # 只处理多成员组，且组总计数 >= 阈值
            for members in groups.values():
                if len(members) < 2:
                    continue
                group_total = sum(count_map.get(t, 0) for t in members)
                if group_total < TAG_AUDIT_MIN_TAG_COUNT:
                    continue
                # canonical = 组内频率最高的标签
                members.sort(key=lambda t: count_map.get(t, 0), reverse=True)
                canonical = members[0]
                for synonym in members[1:]:
                    key = (min(canonical, synonym), max(canonical, synonym))
                    sim = pair_sim.get(key) or pair_sim.get(
                        (max(canonical, synonym), min(canonical, synonym)), 0.0,
                    )
                    suggestions.append(SynonymSuggestion(
                        canonical=canonical,
                        synonym=synonym,
                        similarity=sim,
                        reason="embedding",
                    ))

        suggestions.sort(key=lambda s: s.similarity, reverse=True)
        return suggestions

    @staticmethod
    def _cross_check_existing(
        suggestions: list[SynonymSuggestion],
    ) -> list[SynonymSuggestion]:
        """与 tag_synonyms.json 交叉比对，标记已覆盖的对"""
        from blog_autopilot.tag_normalizer import _load_synonyms
        mapping = _load_synonyms()

        result = []
        for s in suggestions:
            already = (
                mapping.get(s.synonym) == s.canonical
                or mapping.get(s.canonical) == s.synonym
            )
            if already and not s.already_mapped:
                s = SynonymSuggestion(
                    canonical=s.canonical,
                    synonym=s.synonym,
                    similarity=s.similarity,
                    reason=s.reason,
                    already_mapped=True,
                )
            result.append(s)
        return result

    def format_output(self, report: TagAuditReport) -> str:
        """格式化审计报告为终端输出"""
        lines = [
            "",
            "=" * 60,
            "  标签治理审计报告",
            "=" * 60,
            f"  文章总数: {report.article_count}  |  "
            f"唯一标签数: {report.unique_tag_count}  |  "
            f"Embedding: {'可用' if report.embedding_available else '不可用'}",
            "-" * 60,
        ]

        # 频率统计（按层级分组，每组 top 10）
        lines.append("\n  [标签频率 Top 10/层级]")
        for lv in TAG_LEVELS:
            lv_stats = [s for s in report.tag_stats if s.level == lv][:10]
            if lv_stats:
                lines.append(f"    {lv}:")
                for ts in lv_stats:
                    lines.append(f"      {ts.tag} ({ts.count})")

        # 共现对
        if report.top_cooccurrences:
            lines.append(f"\n  [Top {len(report.top_cooccurrences)} 共现对]")
            for cp in report.top_cooccurrences:
                lines.append(f"    {cp.tag_a} + {cp.tag_b} ({cp.co_count})")

        # 同义词建议
        if report.suggestions:
            lines.append(f"\n  [同义词合并建议] ({len(report.suggestions)} 条)")
            for sg in report.suggestions:
                mapped = " [已映射]" if sg.already_mapped else ""
                lines.append(
                    f"    {sg.synonym} -> {sg.canonical}"
                    f"  (相似度: {sg.similarity:.4f}){mapped}"
                )
        elif report.embedding_available:
            lines.append("\n  [同义词合并建议] 未发现语义近义词")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def merge_suggestions(
        report: TagAuditReport, dry_run: bool = False,
    ) -> list[SynonymSuggestion]:
        """
        将审计报告中未映射的同义词建议写入 tag_synonyms.json。

        Args:
            report: 审计报告
            dry_run: True 时仅返回待合并列表，不写文件

        Returns:
            实际合并的 SynonymSuggestion 列表
        """
        to_merge = [s for s in report.suggestions if not s.already_mapped]
        if not to_merge:
            return []

        if dry_run:
            return to_merge

        synonyms_path = Path(__file__).parent.parent / "tag_synonyms.json"
        # 加载现有映射
        existing: dict[str, list[str]] = {}
        if synonyms_path.exists():
            try:
                with open(synonyms_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception as e:
                logger.warning(f"读取 tag_synonyms.json 失败: {e}")

        # 按 canonical 分组追加
        for s in to_merge:
            if s.canonical not in existing:
                existing[s.canonical] = []
            if s.synonym not in existing[s.canonical]:
                existing[s.canonical].append(s.synonym)

        # 写回
        with open(synonyms_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        logger.info(f"已合并 {len(to_merge)} 条同义词到 tag_synonyms.json")

        # 刷新缓存
        from blog_autopilot.tag_normalizer import _invalidate_cache
        _invalidate_cache()

        return to_merge

    @staticmethod
    def export_json(report: TagAuditReport) -> str:
        """导出审计报告为 JSON 格式"""
        data = {
            "article_count": report.article_count,
            "unique_tag_count": report.unique_tag_count,
            "embedding_available": report.embedding_available,
            "tag_stats": [
                {"tag": s.tag, "level": s.level, "count": s.count}
                for s in report.tag_stats
            ],
            "top_cooccurrences": [
                {"tag_a": c.tag_a, "tag_b": c.tag_b, "co_count": c.co_count}
                for c in report.top_cooccurrences
            ],
            "suggestions": [
                {
                    "canonical": s.canonical,
                    "synonym": s.synonym,
                    "similarity": s.similarity,
                    "reason": s.reason,
                    "already_mapped": s.already_mapped,
                }
                for s in report.suggestions
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)


def compute_tag_consistency(
    db: Database,
    tags: TagSet,
    embedding: list[float],
    top_k: int = TAG_CONSISTENCY_NEIGHBORS,
) -> tuple[float, list[dict]]:
    """
    计算标签与最近邻文章的一致性得分。

    查找 K 个 embedding 最近邻，统计 4 级标签在邻居中的命中率。

    Returns:
        (score, neighbors): score 为 [0, 1] 一致性得分，
        neighbors 为最近邻标签列表（供 AI 复核使用）。
    """
    neighbors = db.find_nearest_by_embedding(embedding, top_k=top_k)
    if not neighbors:
        return 1.0, []

    current = {
        "tag_magazine": tags.tag_magazine,
        "tag_science": tags.tag_science,
        "tag_topic": tags.tag_topic,
        "tag_content": tags.tag_content,
    }

    total_hits = 0
    total_checks = 0
    for neighbor in neighbors:
        for level in TAG_LEVELS:
            key = f"tag_{level}"
            if current.get(key):
                total_checks += 1
                if neighbor.get(key) == current[key]:
                    total_hits += 1

    if total_checks == 0:
        return 1.0, neighbors

    return total_hits / total_checks, neighbors
