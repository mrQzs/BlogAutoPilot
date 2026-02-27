"""标签治理审计模块测试"""

import json
from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.constants import TAG_AUDIT_MIN_ARTICLES
from blog_autopilot.exceptions import TagAuditError
from blog_autopilot.models import (
    CooccurrencePair,
    SynonymSuggestion,
    TagAuditReport,
    TagSet,
    TagStats,
)
from blog_autopilot.tag_governance import (
    TagAuditor,
    TAG_LEVELS,
    compute_tag_consistency,
)


# ── 测试数据 ──

SAMPLE_TAG_ROWS = [
    {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "GPT", "created_at": None},
    {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "CV", "tag_content": "图像识别", "created_at": None},
    {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "BERT", "created_at": None},
    {"tag_magazine": "文化", "tag_science": "历史", "tag_topic": "古代", "tag_content": "唐朝", "created_at": None},
    {"tag_magazine": "科技", "tag_science": "安全", "tag_topic": "加密", "tag_content": "RSA", "created_at": None},
    {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "Transformer", "created_at": None},
]


def _make_auditor():
    """创建 mock 版 TagAuditor，跳过真实 DB/Embedding 初始化"""
    with patch("blog_autopilot.tag_governance.Database"):
        with patch.object(TagAuditor, "__init__", lambda self, s: None):
            auditor = TagAuditor.__new__(TagAuditor)
            auditor._db = MagicMock()
            auditor._embedding_client = None
            auditor._article_count = 0
            return auditor


# ── test_min_articles_guard ──

class TestMinArticlesGuard:
    def test_raises_when_insufficient(self):
        auditor = _make_auditor()
        auditor._db.count_articles.return_value = 2
        with pytest.raises(TagAuditError, match="文章数不足"):
            auditor.audit()

    def test_passes_when_sufficient(self):
        auditor = _make_auditor()
        auditor._db.count_articles.return_value = TAG_AUDIT_MIN_ARTICLES
        auditor._db.fetch_all_tags_with_dates.return_value = SAMPLE_TAG_ROWS
        report = auditor.audit()
        assert report.article_count == TAG_AUDIT_MIN_ARTICLES


# ── test_collect_tag_stats ──

class TestCollectTagStats:
    def test_frequency_counts(self):
        stats = TagAuditor._collect_tag_stats(SAMPLE_TAG_ROWS)
        # "科技" 出现 5 次 (magazine 层级)
        mag_stats = [s for s in stats if s.level == "magazine"]
        tech = next(s for s in mag_stats if s.tag == "科技")
        assert tech.count == 5

    def test_all_levels_present(self):
        stats = TagAuditor._collect_tag_stats(SAMPLE_TAG_ROWS)
        levels_found = {s.level for s in stats}
        assert levels_found == set(TAG_LEVELS)

    def test_empty_rows(self):
        stats = TagAuditor._collect_tag_stats([])
        assert stats == []


# ── test_cooccurrence_pairs ──

class TestCooccurrencePairs:
    def test_pair_counts(self):
        pairs = TagAuditor._build_cooccurrence(SAMPLE_TAG_ROWS)
        assert len(pairs) > 0
        # 所有 co_count 应 > 0
        for p in pairs:
            assert p.co_count > 0

    def test_no_self_pairs(self):
        pairs = TagAuditor._build_cooccurrence(SAMPLE_TAG_ROWS)
        for p in pairs:
            assert p.tag_a != p.tag_b

    def test_sorted_order(self):
        """共现对内部标签应按字典序排列（a < b）"""
        pairs = TagAuditor._build_cooccurrence(SAMPLE_TAG_ROWS)
        for p in pairs:
            assert p.tag_a <= p.tag_b


# ── test_semantic_duplicates ──

class TestSemanticDuplicates:
    def test_found_with_mock_embedding(self):
        auditor = _make_auditor()
        mock_emb = MagicMock()
        # 两个标签返回几乎相同的向量
        mock_emb.get_embedding.side_effect = lambda tag: (
            [1.0, 0.0, 0.0] if tag == "人工智能" else
            [0.99, 0.1, 0.0] if tag == "AI技术" else
            [0.0, 1.0, 0.0]
        )
        auditor._embedding_client = mock_emb

        stats = [
            TagStats(tag="人工智能", level="science", count=5),
            TagStats(tag="AI技术", level="science", count=3),
            TagStats(tag="历史", level="science", count=2),
        ]
        suggestions = auditor._find_semantic_duplicates(stats)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.canonical == "人工智能"  # 频率更高
        assert s.synonym == "AI技术"

    def test_fuzzy_group_merges_low_count_tags(self):
        """单独 count=1 的标签，模糊分组后组总数 >= 3 仍生成建议"""
        auditor = _make_auditor()
        mock_emb = MagicMock()
        # 三个语义相近的标签，各出现 1 次，组总数 = 3
        mock_emb.get_embedding.side_effect = lambda tag: (
            [1.0, 0.0, 0.0] if tag == "图像去噪" else
            [0.98, 0.1, 0.0] if tag == "去噪方法" else
            [0.97, 0.12, 0.0] if tag == "图像降噪" else
            [0.0, 1.0, 0.0]
        )
        auditor._embedding_client = mock_emb

        stats = [
            TagStats(tag="图像去噪", level="topic", count=1),
            TagStats(tag="去噪方法", level="topic", count=1),
            TagStats(tag="图像降噪", level="topic", count=1),
            TagStats(tag="加密算法", level="topic", count=2),
        ]
        suggestions = auditor._find_semantic_duplicates(stats)
        # 三个去噪标签合并为一组，组总数 3 >= 阈值
        synonyms = {s.synonym for s in suggestions}
        assert len(suggestions) == 2
        assert "去噪方法" in synonyms
        assert "图像降噪" in synonyms
        # canonical 是组内任一（count 相同时取排序结果）
        canonicals = {s.canonical for s in suggestions}
        assert len(canonicals) == 1  # 同一组只有一个 canonical

    def test_fuzzy_group_below_threshold_skipped(self):
        """组总数 < 3 时不生成建议"""
        auditor = _make_auditor()
        mock_emb = MagicMock()
        mock_emb.get_embedding.side_effect = lambda tag: (
            [1.0, 0.0, 0.0] if tag == "量子计算" else
            [0.98, 0.1, 0.0] if tag == "量子运算" else
            [0.0, 1.0, 0.0]
        )
        auditor._embedding_client = mock_emb

        stats = [
            TagStats(tag="量子计算", level="topic", count=1),
            TagStats(tag="量子运算", level="topic", count=1),
            TagStats(tag="加密算法", level="topic", count=5),
        ]
        suggestions = auditor._find_semantic_duplicates(stats)
        # 组总数 2 < 3，不生成建议
        assert len(suggestions) == 0

    def test_no_embedding_skips_semantic(self):
        auditor = _make_auditor()
        auditor._db.count_articles.return_value = 10
        auditor._db.fetch_all_tags_with_dates.return_value = SAMPLE_TAG_ROWS
        report = auditor.audit()
        assert report.embedding_available is False
        assert len(report.suggestions) == 0


# ── test_cross_check_marks_existing ──

class TestCrossCheck:
    @patch("blog_autopilot.tag_normalizer._load_synonyms")
    def test_marks_already_mapped(self, mock_load):
        mock_load.return_value = {"AI应用": "人工智能应用"}
        suggestions = [
            SynonymSuggestion(
                canonical="人工智能应用",
                synonym="AI应用",
                similarity=0.92,
                reason="embedding",
            ),
        ]
        result = TagAuditor._cross_check_existing(suggestions)
        assert len(result) == 1
        assert result[0].already_mapped is True

    @patch("blog_autopilot.tag_normalizer._load_synonyms")
    def test_leaves_unmapped(self, mock_load):
        mock_load.return_value = {}
        suggestions = [
            SynonymSuggestion(
                canonical="深度学习",
                synonym="DL",
                similarity=0.88,
                reason="embedding",
            ),
        ]
        result = TagAuditor._cross_check_existing(suggestions)
        assert result[0].already_mapped is False


# ── test_export_json_structure ──

class TestExportJson:
    def test_valid_json_structure(self):
        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(TagStats(tag="AI", level="science", count=3),),
            top_cooccurrences=(
                CooccurrencePair(tag_a="AI", tag_b="科技", co_count=2),
            ),
            suggestions=(
                SynonymSuggestion(
                    canonical="人工智能",
                    synonym="AI技术",
                    similarity=0.91,
                    reason="embedding",
                    already_mapped=False,
                ),
            ),
            embedding_available=True,
        )
        output = TagAuditor.export_json(report)
        data = json.loads(output)
        assert data["article_count"] == 10
        assert data["unique_tag_count"] == 5
        assert data["embedding_available"] is True
        assert len(data["tag_stats"]) == 1
        assert len(data["top_cooccurrences"]) == 1
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["canonical"] == "人工智能"


# ── test_format_output_readable ──

class TestFormatOutput:
    def test_contains_key_info(self):
        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(
                TagStats(tag="AI", level="science", count=3),
            ),
            top_cooccurrences=(
                CooccurrencePair(tag_a="AI", tag_b="科技", co_count=2),
            ),
            suggestions=(),
            embedding_available=False,
        )
        auditor = _make_auditor()
        output = auditor.format_output(report)
        assert "标签治理审计报告" in output
        assert "文章总数: 10" in output
        assert "唯一标签数: 5" in output
        assert "不可用" in output
        assert "AI (3)" in output
        assert "AI + 科技 (2)" in output


# ── test_merge_suggestions ──


class TestMergeSuggestions:
    def test_dry_run_returns_unmerged(self):
        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(),
            top_cooccurrences=(),
            suggestions=(
                SynonymSuggestion(
                    canonical="深度学习", synonym="DL",
                    similarity=0.90, reason="embedding", already_mapped=False,
                ),
                SynonymSuggestion(
                    canonical="人工智能", synonym="AI",
                    similarity=0.88, reason="embedding", already_mapped=True,
                ),
            ),
            embedding_available=True,
        )
        result = TagAuditor.merge_suggestions(report, dry_run=True)
        assert len(result) == 1
        assert result[0].synonym == "DL"

    def test_skips_already_mapped(self):
        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(),
            top_cooccurrences=(),
            suggestions=(
                SynonymSuggestion(
                    canonical="人工智能", synonym="AI",
                    similarity=0.88, reason="embedding", already_mapped=True,
                ),
            ),
            embedding_available=True,
        )
        result = TagAuditor.merge_suggestions(report, dry_run=True)
        assert result == []

    def test_writes_to_file(self, tmp_path):
        synonyms_file = tmp_path / "tag_synonyms.json"
        synonyms_file.write_text('{"已有": ["别名"]}', encoding="utf-8")

        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(),
            top_cooccurrences=(),
            suggestions=(
                SynonymSuggestion(
                    canonical="深度学习", synonym="DL",
                    similarity=0.90, reason="embedding", already_mapped=False,
                ),
            ),
            embedding_available=True,
        )

        with patch("blog_autopilot.tag_governance.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.__truediv__ = lambda self, x: synonyms_file
            with patch("blog_autopilot.tag_normalizer._invalidate_cache"):
                result = TagAuditor.merge_suggestions(report)

        assert len(result) == 1
        # 验证文件实际写入了正确内容
        data = json.loads(synonyms_file.read_text(encoding="utf-8"))
        assert "已有" in data  # 保留原有映射
        assert "深度学习" in data
        assert "DL" in data["深度学习"]

    def test_no_duplicate_synonyms(self, tmp_path):
        """合并时不应产生重复同义词"""
        synonyms_file = tmp_path / "tag_synonyms.json"
        synonyms_file.write_text('{"深度学习": ["DL"]}', encoding="utf-8")

        report = TagAuditReport(
            article_count=10,
            unique_tag_count=5,
            tag_stats=(),
            top_cooccurrences=(),
            suggestions=(
                SynonymSuggestion(
                    canonical="深度学习", synonym="DL",
                    similarity=0.90, reason="embedding", already_mapped=False,
                ),
                SynonymSuggestion(
                    canonical="深度学习", synonym="Deep Learning",
                    similarity=0.88, reason="embedding", already_mapped=False,
                ),
            ),
            embedding_available=True,
        )

        with patch("blog_autopilot.tag_governance.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.parent.__truediv__ = lambda self, x: synonyms_file
            with patch("blog_autopilot.tag_normalizer._invalidate_cache"):
                TagAuditor.merge_suggestions(report)

        data = json.loads(synonyms_file.read_text(encoding="utf-8"))
        # DL should not be duplicated
        assert data["深度学习"].count("DL") == 1
        assert "Deep Learning" in data["深度学习"]


# ── test_compute_tag_consistency ──


class TestComputeTagConsistency:
    def test_full_match(self):
        db = MagicMock()
        db.find_nearest_by_embedding.return_value = [
            {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "GPT"},
            {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "GPT"},
        ]
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        score, neighbors = compute_tag_consistency(db, tags, [0.1, 0.2])
        assert score == 1.0
        assert len(neighbors) == 2

    def test_no_match(self):
        db = MagicMock()
        db.find_nearest_by_embedding.return_value = [
            {"tag_magazine": "文化", "tag_science": "历史", "tag_topic": "古代", "tag_content": "唐朝"},
        ]
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        score, neighbors = compute_tag_consistency(db, tags, [0.1, 0.2])
        assert score == 0.0
        assert len(neighbors) == 1

    def test_partial_match(self):
        db = MagicMock()
        db.find_nearest_by_embedding.return_value = [
            {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "CV", "tag_content": "YOLO"},
        ]
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        score, neighbors = compute_tag_consistency(db, tags, [0.1, 0.2])
        # 2 out of 4 match
        assert score == 0.5

    def test_no_neighbors_returns_one(self):
        db = MagicMock()
        db.find_nearest_by_embedding.return_value = []
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        score, neighbors = compute_tag_consistency(db, tags, [0.1, 0.2])
        assert score == 1.0
        assert neighbors == []

    def test_multiple_neighbors_averaged(self):
        db = MagicMock()
        db.find_nearest_by_embedding.return_value = [
            {"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "NLP", "tag_content": "GPT"},
            {"tag_magazine": "文化", "tag_science": "历史", "tag_topic": "古代", "tag_content": "唐朝"},
        ]
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        score, neighbors = compute_tag_consistency(db, tags, [0.1, 0.2])
        # First neighbor: 4/4, second: 0/4 → 4/8 = 0.5
        assert score == 0.5
        assert len(neighbors) == 2


# ── test_review_tags (AIWriter) ──


class TestReviewTags:
    """测试 AIWriter.review_tags 方法"""

    def _make_writer(self):
        with patch("blog_autopilot.ai.client.OpenAI"):
            from blog_autopilot.ai.client import AIWriter
            from blog_autopilot.config import AISettings
            settings = AISettings(
                api_key="sk-test",
                api_base="https://api.test.com",
                model_writer="test-model",
                model_promo="test-model",
            )
            writer = AIWriter(settings)
            return writer

    @patch("blog_autopilot.ai.client.AIWriter.call_claude")
    def test_ai_says_no_change(self, mock_call):
        """AI 返回 changed=false 时保留原标签"""
        mock_call.return_value = json.dumps({
            "tag_magazine": "科技", "tag_science": "AI",
            "tag_topic": "NLP", "tag_content": "GPT",
            "changed": False, "reason": "",
        })
        writer = self._make_writer()
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        result = writer.review_tags(tags, [], "test summary")
        assert result == tags

    @patch("blog_autopilot.ai.client.AIWriter.call_claude")
    def test_ai_corrects_tags(self, mock_call):
        """AI 返回 changed=true 时使用修正后的标签"""
        mock_call.return_value = json.dumps({
            "tag_magazine": "科技", "tag_science": "AI",
            "tag_topic": "CV", "tag_content": "图像识别",
            "changed": True, "reason": "topic 应为 CV 而非 NLP",
        })
        writer = self._make_writer()
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        result = writer.review_tags(tags, [{"tag_magazine": "科技", "tag_science": "AI", "tag_topic": "CV", "tag_content": "图像"}], "图像识别相关文章")
        assert result.tag_topic == "CV"
        assert result.tag_content == "图像识别"

    @patch("blog_autopilot.ai.client.AIWriter.call_claude")
    def test_response_without_title_tg_promo_works(self, mock_call):
        """标签复核响应不需要 title 和 tg_promo 字段"""
        mock_call.return_value = json.dumps({
            "tag_magazine": "科技", "tag_science": "AI",
            "tag_topic": "NLP", "tag_content": "GPT",
            "changed": False, "reason": "",
        })
        writer = self._make_writer()
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        # Should NOT raise AIResponseParseError about missing title/tg_promo
        result = writer.review_tags(tags, [], "summary")
        assert result == tags

    @patch("blog_autopilot.ai.client.AIWriter.call_claude")
    def test_missing_tag_field_raises(self, mock_call):
        """缺少标签字段时应抛出异常"""
        mock_call.return_value = json.dumps({
            "tag_magazine": "科技",
            "changed": False,
        })
        writer = self._make_writer()
        tags = TagSet(tag_magazine="科技", tag_science="AI", tag_topic="NLP", tag_content="GPT")
        from blog_autopilot.exceptions import AIResponseParseError
        with pytest.raises(AIResponseParseError):
            writer.review_tags(tags, [], "summary")
