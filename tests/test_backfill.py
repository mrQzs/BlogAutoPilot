"""测试 --backfill-summaries CLI 命令"""

from unittest.mock import MagicMock, patch

import pytest


class TestBackfillSummaries:

    @patch("blog_autopilot.__main__.get_settings")
    def test_no_db_config_exits(self, mock_settings, capsys):
        """数据库未配置时应报错退出"""
        settings = MagicMock()
        settings.database = None
        mock_settings.return_value = settings

        with patch("sys.argv", ["prog", "--backfill-summaries"]):
            from blog_autopilot.__main__ import main
            with pytest.raises(SystemExit, match="1"):
                main()

        captured = capsys.readouterr()
        assert "数据库未配置" in captured.out

    @patch("blog_autopilot.__main__.get_settings")
    def test_invalid_limit_exits(self, mock_settings, capsys):
        """--limit 非整数时应报错退出"""
        settings = MagicMock()
        settings.database = MagicMock()
        settings.database.user = "testuser"
        mock_settings.return_value = settings

        with patch("sys.argv", ["prog", "--backfill-summaries", "--limit", "abc"]):
            from blog_autopilot.__main__ import main
            with pytest.raises(SystemExit, match="1"):
                main()

        captured = capsys.readouterr()
        assert "--limit" in captured.out
        assert "整数" in captured.out

    @patch("blog_autopilot.__main__.get_settings")
    def test_no_articles_to_backfill(self, mock_settings, capsys):
        """所有文章已有摘要时应提示无需回填"""
        settings = MagicMock()
        settings.database = MagicMock()
        settings.database.user = "testuser"
        mock_settings.return_value = settings

        with patch("sys.argv", ["prog", "--backfill-summaries"]), \
             patch("blog_autopilot.db.Database") as MockDB, \
             patch("blog_autopilot.ai_writer.AIWriter"):
            MockDB.return_value.fetch_articles_without_summary.return_value = []
            from blog_autopilot.__main__ import main
            main()

        captured = capsys.readouterr()
        assert "无需回填" in captured.out

    @patch("blog_autopilot.__main__.get_settings")
    def test_successful_backfill(self, mock_settings, capsys):
        """正常回填流程"""
        settings = MagicMock()
        settings.database = MagicMock()
        settings.database.user = "testuser"
        mock_settings.return_value = settings

        articles = [
            {"id": "a1", "title": "文章1", "content_excerpt": "摘录1", "tg_promo": "推广1"},
            {"id": "a2", "title": "文章2", "content_excerpt": None, "tg_promo": "推广2"},
        ]

        with patch("sys.argv", ["prog", "--backfill-summaries", "--limit", "10"]), \
             patch("blog_autopilot.db.Database") as MockDB, \
             patch("blog_autopilot.ai_writer.AIWriter") as MockWriter:
            mock_db = MockDB.return_value
            mock_db.fetch_articles_without_summary.return_value = articles
            mock_writer = MockWriter.return_value
            mock_writer.generate_summary.return_value = "生成的摘要"

            from blog_autopilot.__main__ import main
            main()

            # 两篇都应处理（a1 用 content_excerpt，a2 回退到 tg_promo）
            assert mock_writer.generate_summary.call_count == 2
            assert mock_db.update_article_summary.call_count == 2

        captured = capsys.readouterr()
        assert "2/2" in captured.out

    @patch("blog_autopilot.__main__.get_settings")
    def test_ai_failure_continues(self, mock_settings, capsys):
        """单篇 AI 失败不阻断后续"""
        settings = MagicMock()
        settings.database = MagicMock()
        settings.database.user = "testuser"
        mock_settings.return_value = settings

        articles = [
            {"id": "a1", "title": "会失败", "content_excerpt": "内容1", "tg_promo": ""},
            {"id": "a2", "title": "会成功", "content_excerpt": "内容2", "tg_promo": ""},
        ]

        with patch("sys.argv", ["prog", "--backfill-summaries"]), \
             patch("blog_autopilot.db.Database") as MockDB, \
             patch("blog_autopilot.ai_writer.AIWriter") as MockWriter:
            mock_db = MockDB.return_value
            mock_db.fetch_articles_without_summary.return_value = articles
            mock_writer = MockWriter.return_value
            mock_writer.generate_summary.side_effect = [
                Exception("API 超时"),
                "摘要OK",
            ]

            from blog_autopilot.__main__ import main
            main()

            # 第二篇应成功入库
            assert mock_db.update_article_summary.call_count == 1

        captured = capsys.readouterr()
        assert "失败" in captured.out
        assert "1/2" in captured.out

    @patch("blog_autopilot.__main__.get_settings")
    def test_skip_article_without_content(self, mock_settings, capsys):
        """content_excerpt 和 tg_promo 都为空时跳过"""
        settings = MagicMock()
        settings.database = MagicMock()
        settings.database.user = "testuser"
        mock_settings.return_value = settings

        articles = [
            {"id": "a1", "title": "空内容", "content_excerpt": None, "tg_promo": ""},
        ]

        with patch("sys.argv", ["prog", "--backfill-summaries"]), \
             patch("blog_autopilot.db.Database") as MockDB, \
             patch("blog_autopilot.ai_writer.AIWriter") as MockWriter:
            mock_db = MockDB.return_value
            mock_db.fetch_articles_without_summary.return_value = articles
            mock_writer = MockWriter.return_value

            from blog_autopilot.__main__ import main
            main()

            mock_writer.generate_summary.assert_not_called()
            mock_db.update_article_summary.assert_not_called()

        captured = capsys.readouterr()
        assert "跳过" in captured.out
        assert "0/1" in captured.out
