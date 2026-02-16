"""TopicRecommender 单元测试"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from blog_autopilot.exceptions import RecommendationError
from blog_autopilot.models import ContentGap, TagSet, TopicRecommendation
from blog_autopilot.recommender import TopicRecommender


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.database = MagicMock()
    settings.ai = MagicMock()
    settings.ai.api_key.get_secret_value.return_value = "test-key"
    settings.ai.api_base = "https://test.api/v1"
    settings.ai.model_promo = "test-model"
    settings.ai.promo_max_tokens = 4096
    settings.ai.default_headers = {}
    return settings


@pytest.fixture
def sample_tag_rows():
    now = datetime.now(timezone.utc)
    return [
        {
            "tag_magazine": "技术周刊",
            "tag_science": "AI应用",
            "tag_topic": "NLP",
            "tag_content": "GPT实践",
            "created_at": now - timedelta(days=5),
        },
        {
            "tag_magazine": "技术周刊",
            "tag_science": "AI应用",
            "tag_topic": "NLP",
            "tag_content": "BERT分析",
            "created_at": now - timedelta(days=10),
        },
        {
            "tag_magazine": "技术周刊",
            "tag_science": "数据库",
            "tag_topic": "PostgreSQL",
            "tag_content": "性能优化",
            "created_at": now - timedelta(days=60),
        },
        {
            "tag_magazine": "科学前沿",
            "tag_science": "量子计算",
            "tag_topic": "量子纠错",
            "tag_content": "最新进展",
            "created_at": now - timedelta(days=90),
        },
    ]


class TestTagGapAnalysis:
    def test_tag_gap_analysis_returns_gaps(self, mock_settings, sample_tag_rows):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._db = MagicMock()
            rec._writer = MagicMock()
            rec._article_count = 0
            rec._tag_combo_count = 0

        gaps = rec._analyze_tag_gaps(sample_tag_rows)

        assert len(gaps) > 0
        assert all(isinstance(g, ContentGap) for g in gaps)
        assert all(g.gap_type == "tag_gap" for g in gaps)

    def test_tag_gap_scores_sorted_descending(self, mock_settings, sample_tag_rows):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._db = MagicMock()
            rec._writer = MagicMock()
            rec._article_count = 0
            rec._tag_combo_count = 0

        gaps = rec._analyze_tag_gaps(sample_tag_rows)
        scores = [g.gap_score for g in gaps]
        assert scores == sorted(scores, reverse=True)

    def test_rare_combos_score_higher(self, mock_settings, sample_tag_rows):
        """出现次数少 + 时间久远的组合应该得分更高"""
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._db = MagicMock()
            rec._writer = MagicMock()
            rec._article_count = 0
            rec._tag_combo_count = 0

        gaps = rec._analyze_tag_gaps(sample_tag_rows)

        # 量子计算只出现1次且90天前 → 应排在前面
        # NLP出现2次且5天前 → 应排在后面
        quantum_gaps = [g for g in gaps if g.tags and g.tags.tag_science == "量子计算"]
        nlp_gaps = [g for g in gaps if g.tags and g.tags.tag_topic == "NLP"]

        assert quantum_gaps[0].gap_score > nlp_gaps[0].gap_score


class TestVectorGapAnalysis:
    def test_vector_gap_filters_sparse(self, mock_settings):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._db = MagicMock()
            rec._writer = MagicMock()
            rec._article_count = 0
            rec._tag_combo_count = 0

        rec._db.compute_centroid.return_value = [0.1] * 3072
        rec._db.find_frontier_articles.return_value = [
            {
                "id": "1", "title": "稀疏文章",
                "tag_magazine": "M", "tag_science": "S",
                "tag_topic": "T", "tag_content": "C",
                "dist_centroid": 1.5, "nn_similarity": 0.3,
            },
            {
                "id": "2", "title": "密集文章",
                "tag_magazine": "M2", "tag_science": "S2",
                "tag_topic": "T2", "tag_content": "C2",
                "dist_centroid": 1.2, "nn_similarity": 0.85,
            },
        ]

        gaps = rec._analyze_vector_gaps(5)

        # 只有 nn_similarity < 0.7 的才保留
        assert len(gaps) == 1
        assert gaps[0].reference_title == "稀疏文章"

    def test_vector_gap_empty_on_no_centroid(self, mock_settings):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._db = MagicMock()
            rec._writer = MagicMock()
            rec._article_count = 0
            rec._tag_combo_count = 0

        rec._db.compute_centroid.return_value = None

        gaps = rec._analyze_vector_gaps(5)
        assert gaps == []


class TestMergeGaps:
    def test_merge_normalizes_and_weights(self):
        tag_gaps = [
            ContentGap(
                gap_type="tag_gap", description="A",
                gap_score=10.0,
                tags=TagSet("M", "S", "T1", ""),
            ),
            ContentGap(
                gap_type="tag_gap", description="B",
                gap_score=5.0,
                tags=TagSet("M", "S", "T2", ""),
            ),
        ]
        vector_gaps = [
            ContentGap(
                gap_type="vector_gap", description="V",
                gap_score=2.0,
                tags=TagSet("M", "S", "T1", "C"),
                reference_title="Ref",
            ),
        ]

        merged = TopicRecommender._merge_gaps(tag_gaps, vector_gaps, 5)

        assert len(merged) >= 1
        # T1 应该合并了 tag + vector 权重，分数最高
        assert merged[0].tags.tag_topic == "T1"

    def test_merge_empty_inputs(self):
        assert TopicRecommender._merge_gaps([], [], 5) == []

    def test_merge_respects_top_n(self):
        gaps = [
            ContentGap(
                gap_type="tag_gap", description=f"G{i}",
                gap_score=float(i),
                tags=TagSet("M", "S", f"T{i}", ""),
            )
            for i in range(10)
        ]
        merged = TopicRecommender._merge_gaps(gaps, [], 3)
        assert len(merged) == 3


class TestMinArticlesGuard:
    def test_raises_on_insufficient_articles(self, mock_settings):
        with patch("blog_autopilot.recommender.Database") as MockDB, \
             patch("blog_autopilot.recommender.AIWriter"):
            db_instance = MockDB.return_value
            db_instance.count_articles.return_value = 5

            rec = TopicRecommender(mock_settings)

            with pytest.raises(RecommendationError, match="文章数不足"):
                rec.recommend()


class TestAIRecommendationParsing:
    def test_parse_valid_json_array(self):
        response = json.dumps([
            {
                "topic": "测试选题",
                "rationale": "测试理由",
                "suggested_tags": {
                    "tag_magazine": "M",
                    "tag_science": "S",
                    "tag_topic": "T",
                    "tag_content": "C",
                },
                "priority": "high",
            }
        ])

        result = TopicRecommender._parse_recommendations(response)

        assert len(result) == 1
        assert result[0].topic == "测试选题"
        assert result[0].priority == "high"
        assert result[0].suggested_tags.tag_magazine == "M"

    def test_parse_markdown_code_block(self):
        response = '```json\n[{"topic":"T","rationale":"R","suggested_tags":{"tag_magazine":"M","tag_science":"S","tag_topic":"T","tag_content":"C"},"priority":"medium"}]\n```'

        result = TopicRecommender._parse_recommendations(response)
        assert len(result) == 1

    def test_parse_invalid_priority_defaults_medium(self):
        response = json.dumps([
            {
                "topic": "T",
                "rationale": "R",
                "suggested_tags": {
                    "tag_magazine": "M",
                    "tag_science": "S",
                    "tag_topic": "T",
                    "tag_content": "C",
                },
                "priority": "urgent",
            }
        ])

        result = TopicRecommender._parse_recommendations(response)
        assert result[0].priority == "medium"

    def test_parse_non_json_raises(self):
        from blog_autopilot.exceptions import AIResponseParseError
        with pytest.raises(AIResponseParseError):
            TopicRecommender._parse_recommendations("not json at all")


class TestFormatOutput:
    def test_format_output_with_recommendations(self, mock_settings):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._article_count = 50
            rec._tag_combo_count = 12

        recs = [
            TopicRecommendation(
                topic="量子计算入门指南",
                rationale="量子计算领域覆盖不足",
                suggested_tags=TagSet("科学前沿", "量子计算", "入门", "教程"),
                priority="high",
            ),
        ]

        output = rec.format_output(recs)

        assert "智能选题推荐" in output
        assert "文章总数: 50" in output
        assert "量子计算入门指南" in output
        assert "[!!!]" in output

    def test_format_output_empty(self, mock_settings):
        with patch.object(TopicRecommender, "__init__", lambda self, s: None):
            rec = TopicRecommender.__new__(TopicRecommender)
            rec._article_count = 0
            rec._tag_combo_count = 0

        output = rec.format_output([])
        assert "暂无推荐结果" in output
