"""测试质量审核系统"""

import json

import pytest
from unittest.mock import patch

from blog_autopilot.ai_writer import (
    AIWriter,
    _parse_review_response,
    _validate_review,
    detect_self_review_bias,
    format_dimensional_scores,
    format_issues_for_rewrite,
    format_progressive_feedback,
    format_self_review_warning,
    identify_focus_areas,
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
        "factuality": 8,
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
            consistency=9, factuality=9, readability=8, ai_cliche=8,
        ))
        review = _validate_review(data)
        assert review.verdict == "pass"
        assert review.overall_score >= 7

    def test_rewrite_verdict(self):
        data = json.loads(_make_valid_review_json(
            consistency=6, factuality=6, readability=6, ai_cliche=6,
        ))
        review = _validate_review(data)
        assert review.verdict == "rewrite"
        assert 5 <= review.overall_score < 7

    def test_draft_verdict(self):
        data = json.loads(_make_valid_review_json(
            consistency=3, factuality=3, readability=3, ai_cliche=3,
        ))
        review = _validate_review(data)
        assert review.verdict == "draft"
        assert review.overall_score < 5

    def test_boundary_pass_at_7(self):
        # 7*0.25 + 7*0.20 + 7*0.25 + 7*0.30 = 7.0 → pass
        data = json.loads(_make_valid_review_json(
            consistency=7, factuality=7, readability=7, ai_cliche=7,
        ))
        review = _validate_review(data)
        assert review.verdict == "pass"
        assert review.overall_score == 7

    def test_boundary_rewrite_at_5(self):
        # 5*0.25 + 5*0.20 + 5*0.25 + 5*0.30 = 5.0 → rewrite
        data = json.loads(_make_valid_review_json(
            consistency=5, factuality=5, readability=5, ai_cliche=5,
        ))
        review = _validate_review(data)
        assert review.verdict == "rewrite"
        assert review.overall_score == 5

    def test_weighted_calculation(self):
        # 10*0.25 + 10*0.20 + 10*0.25 + 10*0.30 = 10
        data = json.loads(_make_valid_review_json(
            consistency=10, factuality=10, readability=10, ai_cliche=10,
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

    def test_factuality_score_affects_overall(self):
        """factuality 低分拉低综合分"""
        # high factuality: 8*0.25 + 9*0.20 + 8*0.25 + 8*0.30 = 8.2 → 8
        data_high = json.loads(_make_valid_review_json(
            consistency=8, factuality=9, readability=8, ai_cliche=8,
        ))
        review_high = _validate_review(data_high)

        # low factuality: 8*0.25 + 3*0.20 + 8*0.25 + 8*0.30 = 7.0
        data_low = json.loads(_make_valid_review_json(
            consistency=8, factuality=3, readability=8, ai_cliche=8,
        ))
        review_low = _validate_review(data_low)

        assert review_high.overall_score > review_low.overall_score

    def test_factuality_missing_defaults_to_consistency(self):
        """factuality 缺失时回退到 consistency 值"""
        data = {
            "consistency": 9,
            "readability": 7,
            "ai_cliche": 6,
            "issues": [],
            "summary": "测试",
        }
        review = _validate_review(data)
        assert review.factuality_score == 9

    def test_factuality_in_review_dataclass(self):
        """QualityReview 包含 factuality_score 字段"""
        data = json.loads(_make_valid_review_json(factuality=7))
        review = _validate_review(data)
        assert review.factuality_score == 7


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


# ── Part A: 自审偏差检测测试 ──


class TestDetectSelfReviewBias:

    def test_same_model_detected(self, ai_settings):
        """writer 和 reviewer 使用相同模型时返回 True"""
        ai_settings.model_reviewer = ai_settings.model_writer
        assert detect_self_review_bias(ai_settings) is True

    def test_different_model_not_detected(self, ai_settings):
        """不同模型时返回 False"""
        ai_settings.model_reviewer = "different-model"
        assert detect_self_review_bias(ai_settings) is False

    def test_empty_reviewer_fallback_to_promo(self, ai_settings):
        """reviewer 为空时回退到 promo，与 writer 不同则无偏差"""
        ai_settings.model_reviewer = ""
        ai_settings.model_promo = "different-model"
        assert detect_self_review_bias(ai_settings) is False

    def test_empty_reviewer_same_as_writer(self, ai_settings):
        """reviewer 为空时回退到 promo，promo == writer 则有偏差"""
        ai_settings.model_reviewer = ""
        ai_settings.model_promo = ai_settings.model_writer
        assert detect_self_review_bias(ai_settings) is True

    def test_same_model_different_api_base_no_bias(self, ai_settings):
        """模型名相同但 API 端点不同时不视为自审"""
        ai_settings.model_reviewer = ai_settings.model_writer
        ai_settings.reviewer_api_base = "https://different-provider.api/v1"
        assert detect_self_review_bias(ai_settings) is False

    def test_same_model_different_api_key_no_bias(self, ai_settings):
        """模型名和端点相同但 API key 不同时不视为自审"""
        from pydantic import SecretStr
        ai_settings.model_reviewer = ai_settings.model_writer
        ai_settings.reviewer_api_key = SecretStr("different-key")
        assert detect_self_review_bias(ai_settings) is False

    def test_same_model_same_api_base_is_bias(self, ai_settings):
        """模型名相同且端点相同时视为自审"""
        ai_settings.model_reviewer = ai_settings.model_writer
        # reviewer_api_base 为空时回退到 api_base，所以仍是同一端点
        ai_settings.reviewer_api_base = ""
        assert detect_self_review_bias(ai_settings) is True

    def test_empty_string_api_key_treated_as_same(self, ai_settings):
        """reviewer_api_key 为空字符串时视为未配置独立 key，仍检测为自审"""
        from pydantic import SecretStr
        ai_settings.model_reviewer = ai_settings.model_writer
        ai_settings.reviewer_api_key = SecretStr("")
        assert detect_self_review_bias(ai_settings) is True


class TestFormatSelfReviewWarning:

    def test_contains_warning_keywords(self):
        warning = format_self_review_warning()
        assert "自审偏差" in warning
        assert "ai_cliche" in warning
        assert "factuality" in warning

    def test_not_empty(self):
        assert len(format_self_review_warning()) > 50

    def test_no_numeric_score_reduction(self):
        """不应包含具体降分指令（阈值上调已处理）"""
        warning = format_self_review_warning()
        assert "降低 1-2 分" not in warning
        assert "系统已自动上调" in warning


class TestSelfReviewThresholdAdjustment:

    def test_threshold_raised_when_self_review(self, ai_settings):
        """自审偏差时阈值上调"""
        ai_settings.model_reviewer = ai_settings.model_writer
        writer = AIWriter(ai_settings)

        # 综合分 7 — 正常情况下 pass，自审偏差时 threshold 升至 8 → rewrite
        mock_response = _make_valid_review_json(
            consistency=7, factuality=7, readability=7, ai_cliche=7,
        )
        with patch.object(writer, "call_claude", return_value=mock_response):
            review = writer.review_quality("标题", "<p>内容</p>", "素材")

        # 默认 pass_threshold=7, self_review 上调 +1 → 8，综合分 7 < 8 → rewrite
        assert review.verdict == "rewrite"

    def test_no_adjustment_when_different_model(self, ai_settings):
        """不同模型时不上调阈值"""
        ai_settings.model_reviewer = "different-model"
        writer = AIWriter(ai_settings)

        mock_response = _make_valid_review_json(
            consistency=7, factuality=7, readability=7, ai_cliche=7,
        )
        with patch.object(writer, "call_claude", return_value=mock_response):
            review = writer.review_quality("标题", "<p>内容</p>", "素材")

        assert review.verdict == "pass"

    def test_threshold_clamped_at_10(self, ai_settings):
        """自审偏差阈值上调不超过 10"""
        ai_settings.model_reviewer = ai_settings.model_writer
        ai_settings.quality_pass_threshold = 10
        ai_settings.quality_rewrite_threshold = 10
        writer = AIWriter(ai_settings)

        # 满分 10 应该仍能 pass（阈值不会变成 11）
        mock_response = _make_valid_review_json(
            consistency=10, factuality=10, readability=10, ai_cliche=10,
        )
        with patch.object(writer, "call_claude", return_value=mock_response):
            review = writer.review_quality("标题", "<p>内容</p>", "素材")

        assert review.verdict == "pass"

    def test_explicit_zero_threshold_not_overridden(self, ai_settings):
        """传入 pass_threshold=0 时不会被 settings 默认值覆盖"""
        ai_settings.model_reviewer = "different-model"
        writer = AIWriter(ai_settings)

        mock_response = _make_valid_review_json(
            consistency=1, factuality=1, readability=1, ai_cliche=1,
        )
        with patch.object(writer, "call_claude", return_value=mock_response):
            # pass_threshold=0 意味着任何分数都能 pass
            review = writer.review_quality(
                "标题", "<p>内容</p>", "素材", pass_threshold=0,
            )

        assert review.verdict == "pass"


# ── Part C: 增强重写反馈测试 ──


class TestFormatDimensionalScores:

    def test_contains_all_dimensions(self, sample_quality_review):
        result = format_dimensional_scores(sample_quality_review)
        assert "consistency" in result
        assert "factuality" in result
        assert "readability" in result
        assert "ai_cliche" in result
        assert "/10" in result

    def test_contains_overall(self, sample_quality_review):
        result = format_dimensional_scores(sample_quality_review)
        assert "综合分" in result


class TestIdentifyFocusAreas:

    def test_returns_weakest_dimensions(self):
        review = QualityReview(
            consistency_score=9,
            factuality_score=8,
            readability_score=4,
            ai_cliche_score=3,
            overall_score=6,
            verdict="rewrite",
            issues=(),
            summary="",
        )
        result = identify_focus_areas(review, top_n=2)
        assert "ai_cliche" in result
        assert "readability" in result
        assert "需要重点改进" in result

    def test_top_n_limits_output(self):
        review = QualityReview(
            consistency_score=5,
            factuality_score=4,
            readability_score=3,
            ai_cliche_score=2,
            overall_score=3,
            verdict="draft",
            issues=(),
            summary="",
        )
        result = identify_focus_areas(review, top_n=1)
        # 只返回最弱的 1 个维度
        assert result.count("需要重点改进") == 1

    def test_all_equal_scores_generic_message(self):
        """所有维度分数相同时返回通用改进提示"""
        review = QualityReview(
            consistency_score=5,
            factuality_score=5,
            readability_score=5,
            ai_cliche_score=5,
            overall_score=5,
            verdict="rewrite",
            issues=(),
            summary="",
        )
        result = identify_focus_areas(review)
        assert "所有维度均为" in result
        assert "5/10" in result
        assert "需要重点改进" not in result

    def test_tied_dimensions_mentioned(self):
        """多维度同为最低分但超出 top_n 时提示同分维度"""
        review = QualityReview(
            consistency_score=3,
            factuality_score=3,
            readability_score=3,
            ai_cliche_score=9,
            overall_score=4,
            verdict="draft",
            issues=(),
            summary="",
        )
        result = identify_focus_areas(review, top_n=2)
        # top_n=2 显示 2 个最弱维度，但有 3 个维度同为 3 分
        assert result.count("需要重点改进") == 2
        assert "同分维度" in result

    def test_no_tied_extras_when_all_shown(self):
        """没有多余并列维度时不显示同分提示"""
        review = QualityReview(
            consistency_score=3,
            factuality_score=4,
            readability_score=7,
            ai_cliche_score=8,
            overall_score=5,
            verdict="rewrite",
            issues=(),
            summary="",
        )
        result = identify_focus_areas(review, top_n=2)
        assert "需要重点改进" in result
        assert "同分维度" not in result


class TestFormatProgressiveFeedback:

    def test_no_previous_returns_empty(self, sample_quality_review):
        result = format_progressive_feedback(sample_quality_review, None, 1)
        assert result == ""

    def test_attempt_1_returns_empty(self, sample_quality_review):
        result = format_progressive_feedback(sample_quality_review, sample_quality_review, 1)
        assert result == ""

    def test_shows_comparison_on_second_attempt(self):
        prev = QualityReview(
            consistency_score=5, factuality_score=5, readability_score=5,
            ai_cliche_score=5, overall_score=5, verdict="rewrite",
            issues=(), summary="",
        )
        current = QualityReview(
            consistency_score=7, factuality_score=6, readability_score=4,
            ai_cliche_score=6, overall_score=6, verdict="rewrite",
            issues=(), summary="",
        )
        result = format_progressive_feedback(current, prev, 2)
        assert "第 2 次重写对比" in result
        assert "+2" in result  # consistency improved
        assert "退步" in result  # readability dropped

    def test_marks_regressed_dimensions(self):
        prev = QualityReview(
            consistency_score=8, factuality_score=8, readability_score=8,
            ai_cliche_score=8, overall_score=8, verdict="pass",
            issues=(), summary="",
        )
        current = QualityReview(
            consistency_score=6, factuality_score=7, readability_score=5,
            ai_cliche_score=9, overall_score=7, verdict="pass",
            issues=(), summary="",
        )
        result = format_progressive_feedback(current, prev, 2)
        assert "退步维度" in result


class TestRewriteWithEnhancedFeedback:

    def test_rewrite_with_previous_review(self, ai_settings, sample_quality_review):
        writer = AIWriter(ai_settings)
        mock_response = "新标题\n<p>重写后的正文内容</p>"

        with patch.object(writer, "call_claude", return_value=mock_response):
            result = writer.rewrite_with_feedback(
                "旧标题", "<p>旧内容</p>", "原始素材",
                sample_quality_review, category_name="Articles",
                previous_review=sample_quality_review, attempt=2,
                exemplar_context="示例文本",
            )

        assert result.title == "新标题"
        assert "重写后的正文内容" in result.html_body

    def test_rewrite_first_attempt_no_progressive(self, ai_settings, sample_quality_review):
        writer = AIWriter(ai_settings)
        mock_response = "新标题\n<p>重写后的正文内容</p>"

        with patch.object(writer, "call_claude", return_value=mock_response) as mock_call:
            writer.rewrite_with_feedback(
                "旧标题", "<p>旧内容</p>", "原始素材",
                sample_quality_review, category_name="Articles",
                previous_review=None, attempt=1,
            )
            # Check the user prompt doesn't contain progressive feedback header
            call_args = mock_call.call_args
            user_prompt = call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")
            assert "重写对比" not in user_prompt
