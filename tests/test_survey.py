"""测试综述文章生成模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.exceptions import SurveyGenerationError
from blog_autopilot.models import SurveyResult, TagSet


def _make_article(
    idx: int,
    tag_magazine: str = "技术",
    tag_science: str = "AI",
    tag_topic: str = "大模型",
    tag_content: str = "GPT技术突破",
    embedding: list[float] | None = None,
) -> dict:
    """构造测试用源文章 dict"""
    return {
        "id": f"a{idx}",
        "title": f"文章{idx}",
        "summary": f"摘要{idx}",
        "tg_promo": f"推广{idx}",
        "url": f"https://blog/{idx}",
        "created_at": None,
        "tag_magazine": tag_magazine,
        "tag_science": tag_science,
        "tag_topic": tag_topic,
        "tag_content": tag_content,
        "embedding": embedding,
    }


class TestSurveyResult:
    """SurveyResult 数据模型验证"""

    def test_dataclass_fields(self):
        result = SurveyResult(
            title="综述标题",
            html_body="<p>正文</p>",
            source_count=5,
            tag_magazine="技术周刊",
            tag_science="AI应用",
            tag_topic="大模型",
        )
        assert result.title == "综述标题"
        assert result.source_count == 5
        assert result.tag_magazine == "技术周刊"
        assert result.merged_tags is None
        assert result.merged_embedding is None

    def test_with_merged_fields(self):
        tags = TagSet(
            tag_magazine="技术",
            tag_science="AI",
            tag_topic="大模型",
            tag_content="GPT技术突破",
        )
        emb = (0.1, 0.2, 0.3)
        result = SurveyResult(
            title="综述标题",
            html_body="<p>正文</p>",
            source_count=5,
            tag_magazine="技术",
            tag_science="AI",
            tag_topic="大模型",
            merged_tags=tags,
            merged_embedding=emb,
        )
        assert result.merged_tags == tags
        assert result.merged_embedding == (0.1, 0.2, 0.3)

    def test_frozen(self):
        result = SurveyResult(
            title="标题", html_body="<p>x</p>",
            source_count=3, tag_magazine="a",
            tag_science="b", tag_topic="c",
        )
        with pytest.raises(AttributeError):
            result.title = "新标题"


class TestSurveyGeneratorInit:
    """SurveyGenerator 初始化验证"""

    def test_no_database_raises(self, ai_settings):
        """无数据库配置时抛异常"""
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database = None
        with pytest.raises(SurveyGenerationError, match="数据库配置"):
            SurveyGenerator(settings)

    def test_empty_db_user_raises(self, ai_settings):
        """数据库 user 为空时抛异常"""
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = ""
        with pytest.raises(SurveyGenerationError, match="数据库配置"):
            SurveyGenerator(settings)


class TestDetectCandidates:
    """综述候选检测"""

    @patch("blog_autopilot.survey.Database")
    def test_found(self, mock_db_cls):
        mock_db = mock_db_cls.return_value
        mock_db.find_survey_candidates.return_value = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "大模型", "article_count": 5},
        ]
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)
        candidates = gen.detect_candidates()
        assert len(candidates) == 1
        assert candidates[0]["article_count"] == 5
        assert candidates[0]["tag_topics"] == ["大模型"]

    @patch("blog_autopilot.survey.Database")
    def test_empty(self, mock_db_cls):
        mock_db = mock_db_cls.return_value
        mock_db.find_survey_candidates.return_value = []
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)
        assert gen.detect_candidates() == []

    @patch("blog_autopilot.survey.Database")
    def test_fuzzy_topic_grouping(self, mock_db_cls):
        """embedding 模糊分组：图像去噪 + 去噪方法 合并"""
        mock_db = mock_db_cls.return_value
        mock_db.find_survey_candidates.return_value = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "图像去噪", "article_count": 3},
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "去噪方法", "article_count": 3},
        ]
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        # 注入 mock embedding（代码会拼接 "AI 图像去噪" 上下文前缀）
        mock_emb = MagicMock()
        mock_emb.get_embedding.side_effect = lambda t: (
            [1.0, 0.0, 0.0] if "图像去噪" in t else [0.98, 0.1, 0.0]
        )
        gen._embedding_client = mock_emb

        candidates = gen.detect_candidates()
        assert len(candidates) == 1
        assert candidates[0]["article_count"] == 6
        assert set(candidates[0]["tag_topics"]) == {"图像去噪", "去噪方法"}

    @patch("blog_autopilot.survey.Database")
    def test_below_threshold_filtered(self, mock_db_cls):
        """分组后总数不足 min_articles 被过滤"""
        mock_db = mock_db_cls.return_value
        mock_db.find_survey_candidates.return_value = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "冷门话题", "article_count": 1},
        ]
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)
        candidates = gen.detect_candidates()
        assert len(candidates) == 0


class TestGenerateSurvey:
    """综述文章生成"""

    @patch("blog_autopilot.survey.Database")
    @patch("blog_autopilot.survey.AIWriter")
    def test_success(self, mock_writer_cls, mock_db_cls):
        mock_db = mock_db_cls.return_value
        mock_db.fetch_articles_by_tags.return_value = [
            _make_article(1, embedding=[1.0, 0.0, 0.0]),
            _make_article(2, embedding=[0.0, 1.0, 0.0]),
            _make_article(3, embedding=[0.0, 0.0, 1.0]),
            _make_article(4, embedding=[1.0, 1.0, 0.0]),
            _make_article(5, embedding=[0.0, 1.0, 1.0]),
        ]

        from blog_autopilot.models import ArticleResult
        mock_writer = mock_writer_cls.return_value
        mock_writer._load_prompt.return_value = "prompt {topic_tags} {article_count} {source_articles}"
        mock_writer.call_claude.return_value = "综述标题\n<p>综述正文</p>"
        mock_writer._parse_article_response.return_value = ArticleResult(
            title="综述标题",
            html_body="<p>综述正文</p>\n" + "<p>测试段落内容，用于通过综述最小长度校验。</p>\n" * 30,
        )

        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        candidate = {
            "tag_magazine": "技术", "tag_science": "AI",
            "tag_topic": "大模型", "tag_topics": ["大模型"],
            "article_count": 5,
        }
        result = gen.generate(candidate)

        assert result.title == "综述标题"
        assert result.source_count == 5
        assert result.tag_magazine == "技术"
        # 标签合并验证
        assert result.merged_tags is not None
        assert result.merged_tags.tag_magazine == "技术"
        assert result.merged_tags.tag_science == "AI"
        assert result.merged_tags.tag_topic == "大模型"
        assert result.merged_tags.tag_content == "GPT技术突破"
        # Embedding 均值验证
        assert result.merged_embedding is not None
        assert len(result.merged_embedding) == 3
        assert abs(result.merged_embedding[0] - 2 / 5) < 1e-9
        assert abs(result.merged_embedding[1] - 3 / 5) < 1e-9
        assert abs(result.merged_embedding[2] - 2 / 5) < 1e-9
        # prompt 文件名
        mock_writer._load_prompt.assert_any_call("writer_synthesis_system.txt")
        mock_writer._load_prompt.assert_any_call("writer_synthesis_user.txt")

    @patch("blog_autopilot.survey.Database")
    @patch("blog_autopilot.survey.AIWriter")
    def test_multi_topic_generate(self, mock_writer_cls, mock_db_cls):
        """多 topic 合并后生成综述"""
        mock_db = mock_db_cls.return_value
        mock_db.fetch_articles_by_tags.return_value = [
            _make_article(i, tag_topic="图像去噪" if i <= 3 else "去噪方法")
            for i in range(1, 6)
        ]

        from blog_autopilot.models import ArticleResult
        mock_writer = mock_writer_cls.return_value
        mock_writer._load_prompt.return_value = "prompt {topic_tags} {article_count} {source_articles}"
        mock_writer._parse_article_response.return_value = ArticleResult(
            title="去噪综述",
            html_body="<p>综述</p>\n" + "<p>测试段落内容，用于通过综述最小长度校验。</p>\n" * 30,
        )

        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        candidate = {
            "tag_magazine": "技术", "tag_science": "AI",
            "tag_topic": "图像去噪",
            "tag_topics": ["图像去噪", "去噪方法"],
            "article_count": 5,
        }
        result = gen.generate(candidate)
        assert result.source_count == 5
        # 标签合并：频率最高的 topic 胜出（3 篇图像去噪 vs 2 篇去噪方法）
        assert result.merged_tags is not None
        assert result.merged_tags.tag_topic == "图像去噪"
        # 验证传了多个 topic
        call_args = mock_db.fetch_articles_by_tags.call_args
        assert call_args[0][2] == ["图像去噪", "去噪方法"]

    @patch("blog_autopilot.survey.Database")
    @patch("blog_autopilot.survey.AIWriter")
    def test_insufficient_articles(self, mock_writer_cls, mock_db_cls):
        mock_db = mock_db_cls.return_value
        mock_db.fetch_articles_by_tags.return_value = [
            _make_article(1),
        ]

        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        candidate = {
            "tag_magazine": "技术", "tag_science": "AI",
            "tag_topic": "大模型", "tag_topics": ["大模型"],
            "article_count": 1,
        }
        with pytest.raises(SurveyGenerationError, match="源文章不足"):
            gen.generate(candidate)


class TestMergeTags:
    """标签合并逻辑验证"""

    def test_uniform_tags(self):
        """所有文章标签相同 → 取该值"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [_make_article(i) for i in range(1, 4)]
        result = SurveyGenerator._merge_tags(articles)
        assert result == TagSet(
            tag_magazine="技术",
            tag_science="AI",
            tag_topic="大模型",
            tag_content="GPT技术突破",
        )

    def test_frequency_weighting(self):
        """频率最高的标签胜出"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [
            _make_article(1, tag_science="AI"),
            _make_article(2, tag_science="AI"),
            _make_article(3, tag_science="机器学习"),
        ]
        result = SurveyGenerator._merge_tags(articles)
        assert result.tag_science == "AI"

    def test_missing_tag_returns_none(self):
        """某层级标签全缺失 → 返回 None"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "大模型", "tag_content": None},
        ]
        result = SurveyGenerator._merge_tags(articles)
        assert result is None

    def test_empty_articles(self):
        """空文章列表 → 返回 None"""
        from blog_autopilot.survey import SurveyGenerator
        result = SurveyGenerator._merge_tags([])
        assert result is None


class TestComputeMeanEmbedding:
    """Embedding 均值计算验证"""

    def test_mean_of_two(self):
        from blog_autopilot.survey import SurveyGenerator
        articles = [
            _make_article(1, embedding=[2.0, 4.0]),
            _make_article(2, embedding=[6.0, 8.0]),
        ]
        result = SurveyGenerator._compute_mean_embedding(articles)
        assert result == (4.0, 6.0)

    def test_skip_none_embeddings(self):
        """忽略 embedding 为 None 的文章"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [
            _make_article(1, embedding=[2.0, 4.0]),
            _make_article(2, embedding=None),
            _make_article(3, embedding=[6.0, 8.0]),
        ]
        result = SurveyGenerator._compute_mean_embedding(articles)
        assert result == (4.0, 6.0)

    def test_all_none_returns_none(self):
        """所有 embedding 都为 None → 返回 None"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [
            _make_article(1, embedding=None),
            _make_article(2, embedding=None),
        ]
        result = SurveyGenerator._compute_mean_embedding(articles)
        assert result is None

    def test_empty_articles(self):
        from blog_autopilot.survey import SurveyGenerator
        result = SurveyGenerator._compute_mean_embedding([])
        assert result is None

    def test_single_article(self):
        """单篇文章的 embedding 即为结果"""
        from blog_autopilot.survey import SurveyGenerator
        articles = [_make_article(1, embedding=[1.0, 2.0, 3.0])]
        result = SurveyGenerator._compute_mean_embedding(articles)
        assert result == (1.0, 2.0, 3.0)


class TestFormatCandidates:
    """终端输出格式验证"""

    def test_with_candidates(self):
        from blog_autopilot.survey import SurveyGenerator
        candidates = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "大模型", "tag_topics": ["大模型"],
             "article_count": 5},
            {"tag_magazine": "科学", "tag_science": "物理",
             "tag_topic": "量子", "tag_topics": ["量子"],
             "article_count": 3},
        ]
        output = SurveyGenerator.format_candidates(candidates)
        assert "技术 / AI / 大模型" in output
        assert "5 篇" in output
        assert "科学 / 物理 / 量子" in output
        assert "3 篇" in output

    def test_grouped_topics_display(self):
        """多 topic 合并后显示 + 连接"""
        from blog_autopilot.survey import SurveyGenerator
        candidates = [
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "图像去噪",
             "tag_topics": ["图像去噪", "去噪方法"],
             "article_count": 4},
        ]
        output = SurveyGenerator.format_candidates(candidates)
        assert "图像去噪 + 去噪方法" in output
        assert "4 篇" in output

    def test_empty_candidates(self):
        from blog_autopilot.survey import SurveyGenerator
        output = SurveyGenerator.format_candidates([])
        assert "未发现" in output
