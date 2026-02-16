"""测试质量审核系统"""

import json

import pytest
from unittest.mock import patch

from blog_autopilot.ai_writer import (
    AIWriter,
    _parse_review_response,
    _validate_review,
    format_issues_for_rewrite,
)
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    QualityReviewError,
)
from blog_autopilot.models import QualityIssue, QualityReview
from blog_autopilot.publisher import PublishResult


def _make_valid_review_json(**overrides) -> str:
    data = {
        "consistency": 8,
        "readability": 7,
        "ai_cliche": 6,
        "issues": [
            {
                "category": "ai_cliche",
                "severity": "medium",
                "description": "第三段使用了套话",
                "suggestion": "改用更自然的过渡语",
            }
        ],
        "summary": "文章整体质量良好。",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class TestParseReviewResponse:

    def test_pure_json(self):
        result = _parse_review_response(_make_valid_review_json())
        assert result["consistency"] == 8

    def test_markdown_code_block(self):
        text = f"```json\n{_make_valid_review_json()}\n```"
        result = _parse_review_response(text)
        assert result["readability"] == 7

    def test_brace_extraction(self):
        text = f"以下是审核结果：\n{_make_valid_review_json()}\n完成。"
        result = _parse_review_response(text)
        assert "consistency" in result

    def test_non_json_raises(self):
        with pytest.raises(AIResponseParseError, match="无法从审核响应"):
            _parse_review_response("这不是 JSON")

    def test_missing_fields_raises(self):
        text = json.dumps({"consistency": 8})
        with pytest.raises(AIResponseParseError, match="缺少必需字段"):
            _parse_review_response(text)


class TestValidateReview:

    def test_pass_verdict(self):
        data = json.loads(_make_valid_review_json(
            consistency=9, readability=8, ai_cliche=8,
        ))
        review = _validate_review(data)
        assert review.verdict == "pass"
        assert review.overall_score >= 7

    def test_rewrite_verdict(self):
        data = json.loads(_make_valid_review_json(
            consistency=6, readability=6, ai_cliche=6,
        ))
        review = _validate_review(data)
        assert review.verdict == "rewrite"
        assert 5 <= review.overall_score < 7

    def test_draft_verdict(self):
        data = json.loads(_make_valid_review_json(
            consistency=3, readability=3, ai_cliche=3,
        ))
        review = _validate_review(data)
        assert review.verdict == "draft"
        assert review.overall_score < 5

    def test_boundary_pass_at_7(self):
        # 7*0.35 + 7*0.30 + 7*0.35 = 7.0 → pass
        data = json.loads(_make_valid_review_json(
            consistency=7, readability=7, ai_cliche=7,
        ))
        review = _validate_review(data)
        assert review.verdict == "pass"
        assert review.overall_score == 7

    def test_boundary_rewrite_at_5(self):
        # 5*0.35 + 5*0.30 + 5*0.35 = 5.0 → rewrite
        data = json.loads(_make_valid_review_json(
            consistency=5, readability=5, ai_cliche=5,
        ))
        review = _validate_review(data)
        assert review.verdict == "rewrite"
        assert review.overall_score == 5

    def test_weighted_calculation(self):
        # 10*0.35 + 10*0.30 + 10*0.35 = 10
        data = json.loads(_make_valid_review_json(
            consistency=10, readability=10, ai_cliche=10,
        ))
        review = _validate_review(data)
        assert review.overall_score == 10

    def test_score_clamped_above_10(self):
        data = json.loads(_make_valid_review_json(consistency=15))
        review = _validate_review(data)
        assert review.consistency_score == 10

    def test_score_clamped_below_1(self):
        data = json.loads(_make_valid_review_json(readability=0))
        review = _validate_review(data)
        assert review.readability_score == 1

    def test_non_integer_score_raises(self):
        data = json.loads(_make_valid_review_json(consistency="abc"))
        with pytest.raises(QualityReviewError, match="必须是整数"):
            _validate_review(data)

    def test_float_score_accepted(self):
        data = json.loads(_make_valid_review_json(consistency=7.5))
        review = _validate_review(data)
        assert review.consistency_score == 7

    def test_float_string_score_accepted(self):
        data = json.loads(_make_valid_review_json(consistency="8.3"))
        review = _validate_review(data)
        assert review.consistency_score == 8

    def test_issues_parsed(self):
        data = json.loads(_make_valid_review_json())
        review = _validate_review(data)
        assert len(review.issues) == 1
        assert review.issues[0].category == "ai_cliche"
        assert review.issues[0].severity == "medium"

    def test_summary_truncated(self):
        long_summary = "A" * 300
        data = json.loads(_make_valid_review_json(summary=long_summary))
        review = _validate_review(data)
        assert len(review.summary) == 200


class TestFormatIssuesForRewrite:

    def test_format_output(self):
        issues = (
            QualityIssue(
                category="ai_cliche",
                severity="high",
                description="大量套话",
                suggestion="重写",
            ),
        )
        result = format_issues_for_rewrite(issues)
        assert "1." in result
        assert "[high]" in result
        assert "大量套话" in result
        assert "建议: 重写" in result

    def test_empty_issues(self):
        result = format_issues_for_rewrite(())
        assert "无具体问题" in result


class TestReviewQuality:

    def test_full_flow(self, ai_settings):
        writer = AIWriter(ai_settings)
        mock_response = _make_valid_review_json()

        with patch.object(writer, "call_claude", return_value=mock_response):
            review = writer.review_quality("测试标题", "<p>内容</p>", "原始素材")

        assert isinstance(review, QualityReview)
        assert review.consistency_score == 8

    def test_invalid_json_raises(self, ai_settings):
        writer = AIWriter(ai_settings)

        with patch.object(writer, "call_claude", return_value="not json"):
            with pytest.raises(AIResponseParseError):
                writer.review_quality("标题", "<p>内容</p>", "素材")

    def test_fallback_model(self, ai_settings):
        """model_reviewer 为空时回退到 model_promo"""
        ai_settings.model_reviewer = ""
        writer = AIWriter(ai_settings)
        mock_response = _make_valid_review_json()

        with patch.object(writer, "call_claude", return_value=mock_response) as mock_call:
            writer.review_quality("标题", "<p>内容</p>", "素材")
            _, kwargs = mock_call.call_args
            assert kwargs["model"] == ai_settings.model_promo


class TestRewriteWithFeedback:

    def test_rewrite_produces_article(self, ai_settings, sample_quality_review):
        writer = AIWriter(ai_settings)
        mock_response = "新标题\n<p>重写后的正文内容</p>"

        with patch.object(writer, "call_claude", return_value=mock_response):
            result = writer.rewrite_with_feedback(
                "旧标题", "<p>旧内容</p>", "原始素材",
                sample_quality_review, category_name="Articles",
            )

        assert result.title == "新标题"
        assert "重写后的正文内容" in result.html_body

    def test_empty_response_raises(self, ai_settings, sample_quality_review):
        writer = AIWriter(ai_settings)

        with patch.object(writer, "call_claude", return_value=""):
            with pytest.raises(AIResponseParseError, match="AI 返回内容为空"):
                writer.rewrite_with_feedback(
                    "标题", "<p>内容</p>", "素材",
                    sample_quality_review,
                )


# ── Pipeline 集成测试 ──


class TestPipelineQualityReview:
    """测试质量审核在流水线中的集成行为"""

    def _make_pipeline(self, ai_settings, tmp_dirs, review_enabled=True):
        from blog_autopilot.config import (
            DatabaseSettings, PathSettings, Settings, TelegramSettings,
            WordPressSettings,
        )
        from blog_autopilot.pipeline import Pipeline

        ai_settings.quality_review_enabled = review_enabled
        ai_settings.cover_image_enabled = False  # 禁用封面图，避免真实 API 调用
        settings = Settings(
            wp=WordPressSettings(
                url="https://test.wp/api",
                user="test",
                app_password="test",
            ),
            tg=TelegramSettings(bot_token="test", channel_id="@test"),
            ai=ai_settings,
            paths=PathSettings(
                input_folder=tmp_dirs["input"],
                processed_folder=tmp_dirs["processed"],
                drafts_folder=tmp_dirs["drafts"],
            ),
            database=DatabaseSettings(),  # 空配置，禁用关联系统
        )
        return Pipeline(settings)

    def _make_task(self, tmp_dirs):
        from blog_autopilot.models import CategoryMeta, FileTask
        import os

        cat_dir = os.path.join(tmp_dirs["input"], "Articles", "test_15")
        os.makedirs(cat_dir, exist_ok=True)
        filepath = os.path.join(cat_dir, "test.txt")
        with open(filepath, "w") as f:
            f.write("A" * 100)

        return FileTask(
            filepath=filepath,
            filename="test.txt",
            metadata=CategoryMeta(
                category_name="Articles",
                subcategory_name="test",
                category_id=15,
                hashtag="#test",
            ),
        )

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress", return_value=PublishResult(url="https://test/post-1", post_id=1))
    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_pass_continues_to_publish(
        self, mock_extract, mock_wp, mock_tg, ai_settings, tmp_dirs,
    ):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        review_resp = _make_valid_review_json(
            consistency=9, readability=8, ai_cliche=8,
        )
        seo_resp = "not valid json"  # SEO 会失败但不阻断
        promo_resp = "推广文案内容"

        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[article_resp, review_resp, seo_resp, promo_resp],
        ):
            result = pipeline.process_file(task)

        assert result.success is True
        mock_wp.assert_called_once()

    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_draft_saves_and_fails(self, mock_extract, ai_settings, tmp_dirs):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        review_resp = _make_valid_review_json(
            consistency=2, readability=2, ai_cliche=2,
        )

        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[article_resp, review_resp],
        ):
            result = pipeline.process_file(task)

        assert result.success is False
        assert "质量审核未通过" in result.error

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress", return_value=PublishResult(url="https://test/post-1", post_id=1))
    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_rewrite_then_pass(
        self, mock_extract, mock_wp, mock_tg, ai_settings, tmp_dirs,
    ):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        rewrite_review = _make_valid_review_json(
            consistency=6, readability=6, ai_cliche=6,
        )
        rewrite_resp = "改进标题\n<p>改进后的正文</p>"
        pass_review = _make_valid_review_json(
            consistency=9, readability=8, ai_cliche=8,
        )
        seo_resp = "not valid json"
        promo_resp = "推广文案"

        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[
                article_resp, rewrite_review,
                rewrite_resp, pass_review,
                seo_resp, promo_resp,
            ],
        ):
            result = pipeline.process_file(task)

        assert result.success is True

    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_rewrite_exhausted_saves_draft(
        self, mock_extract, ai_settings, tmp_dirs,
    ):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        rewrite_review = _make_valid_review_json(
            consistency=6, readability=6, ai_cliche=6,
        )
        rewrite_resp = "改进标题\n<p>改进后的正文</p>"

        # article → review(rewrite) → rewrite → review(rewrite) → rewrite → review(rewrite)
        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[
                article_resp, rewrite_review,
                rewrite_resp, rewrite_review,
                rewrite_resp, rewrite_review,
            ],
        ):
            result = pipeline.process_file(task)

        assert result.success is False
        assert "重写" in result.error

    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_rewrite_degrades_to_draft(
        self, mock_extract, ai_settings, tmp_dirs,
    ):
        """重写后质量更差变成 draft，应存草稿而非继续发布"""
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        rewrite_review = _make_valid_review_json(
            consistency=6, readability=6, ai_cliche=6,
        )
        rewrite_resp = "改进标题\n<p>改进后的正文</p>"
        draft_review = _make_valid_review_json(
            consistency=2, readability=2, ai_cliche=2,
        )

        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[
                article_resp, rewrite_review,
                rewrite_resp, draft_review,
            ],
        ):
            result = pipeline.process_file(task)

        assert result.success is False
        assert "质量审核未通过" in result.error or "重写后" in result.error

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress", return_value=PublishResult(url="https://test/post-1", post_id=1))
    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_disabled_skips_review(
        self, mock_extract, mock_wp, mock_tg, ai_settings, tmp_dirs,
    ):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs, review_enabled=False)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        seo_resp = "not valid json"
        promo_resp = "推广文案"

        with patch.object(
            pipeline._writer, "call_claude",
            side_effect=[article_resp, seo_resp, promo_resp],
        ):
            result = pipeline.process_file(task)

        assert result.success is True

    @patch("blog_autopilot.pipeline.send_to_telegram")
    @patch("blog_autopilot.pipeline.post_to_wordpress", return_value=PublishResult(url="https://test/post-1", post_id=1))
    @patch("blog_autopilot.pipeline.extract_text_from_file", return_value="原始文本" * 20)
    def test_api_failure_degrades_gracefully(
        self, mock_extract, mock_wp, mock_tg, ai_settings, tmp_dirs,
    ):
        pipeline = self._make_pipeline(ai_settings, tmp_dirs)
        task = self._make_task(tmp_dirs)

        article_resp = "测试标题\n<p>文章正文内容</p>"
        promo_resp = "推广文案"

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return article_resp  # generate_blog_post
            if call_count == 2:
                raise AIAPIError("API 超时")  # review_quality fails
            if call_count == 3:
                return "not valid json"  # SEO extraction (fails gracefully)
            return promo_resp  # generate_promo

        with patch.object(pipeline._writer, "call_claude", side_effect=side_effect):
            result = pipeline.process_file(task)

        # 审核失败应降级继续发布
        assert result.success is True
        mock_wp.assert_called_once()
