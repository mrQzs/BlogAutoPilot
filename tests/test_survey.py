"""测试综述文章生成模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.exceptions import SurveyGenerationError
from blog_autopilot.models import SurveyResult


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
             "tag_topic": "图像去噪", "article_count": 2},
            {"tag_magazine": "技术", "tag_science": "AI",
             "tag_topic": "去噪方法", "article_count": 2},
        ]
        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        # 注入 mock embedding
        mock_emb = MagicMock()
        mock_emb.get_embedding.side_effect = lambda t: (
            [1.0, 0.0, 0.0] if t == "图像去噪" else [0.98, 0.1, 0.0]
        )
        gen._embedding_client = mock_emb

        candidates = gen.detect_candidates()
        assert len(candidates) == 1
        assert candidates[0]["article_count"] == 4
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
            {"id": "a1", "title": "文章1", "summary": "摘要1",
             "tg_promo": "推广1", "url": "https://blog/1", "created_at": None},
            {"id": "a2", "title": "文章2", "summary": "摘要2",
             "tg_promo": "推广2", "url": "https://blog/2", "created_at": None},
            {"id": "a3", "title": "文章3", "summary": "摘要3",
             "tg_promo": "推广3", "url": "https://blog/3", "created_at": None},
        ]

        from blog_autopilot.models import ArticleResult
        mock_writer = mock_writer_cls.return_value
        mock_writer._load_prompt.return_value = "prompt {topic_tags} {article_count} {source_articles}"
        mock_writer.call_claude.return_value = "综述标题\n<p>综述正文</p>"
        mock_writer._parse_article_response.return_value = ArticleResult(
            title="综述标题", html_body="<p>综述正文</p>",
        )

        from blog_autopilot.survey import SurveyGenerator
        settings = MagicMock()
        settings.database.user = "testuser"
        settings.embedding = None
        gen = SurveyGenerator(settings)

        candidate = {
            "tag_magazine": "技术", "tag_science": "AI",
            "tag_topic": "大模型", "tag_topics": ["大模型"],
            "article_count": 3,
        }
        result = gen.generate(candidate)

        assert result.title == "综述标题"
        assert result.source_count == 3
        assert result.tag_magazine == "技术"
        # fetch_articles_by_tags 收到 topic 列表
        mock_db.fetch_articles_by_tags.assert_called_once()

    @patch("blog_autopilot.survey.Database")
    @patch("blog_autopilot.survey.AIWriter")
    def test_multi_topic_generate(self, mock_writer_cls, mock_db_cls):
        """多 topic 合并后生成综述"""
        mock_db = mock_db_cls.return_value
        mock_db.fetch_articles_by_tags.return_value = [
            {"id": f"a{i}", "title": f"文章{i}", "summary": f"摘要{i}",
             "tg_promo": f"推广{i}", "url": f"https://blog/{i}", "created_at": None}
            for i in range(1, 5)
        ]

        from blog_autopilot.models import ArticleResult
        mock_writer = mock_writer_cls.return_value
        mock_writer._load_prompt.return_value = "prompt {topic_tags} {article_count} {source_articles}"
        mock_writer._parse_article_response.return_value = ArticleResult(
            title="去噪综述", html_body="<p>综述</p>",
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
            "article_count": 4,
        }
        result = gen.generate(candidate)
        assert result.source_count == 4
        # 验证传了多个 topic
        call_args = mock_db.fetch_articles_by_tags.call_args
        assert call_args[0][2] == ["图像去噪", "去噪方法"]

    @patch("blog_autopilot.survey.Database")
    @patch("blog_autopilot.survey.AIWriter")
    def test_insufficient_articles(self, mock_writer_cls, mock_db_cls):
        mock_db = mock_db_cls.return_value
        mock_db.fetch_articles_by_tags.return_value = [
            {"id": "a1", "title": "文章1", "summary": "摘要1",
             "tg_promo": "推广1", "url": "https://blog/1", "created_at": None},
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
