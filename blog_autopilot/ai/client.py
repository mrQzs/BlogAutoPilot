"""AI 写作模块 — AIWriter 类，延迟初始化，依赖注入"""

import logging
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from blog_autopilot.ai.html_utils import _warn_unclosed_tags
from blog_autopilot.ai.relation_context import build_relation_context, _log_link_coverage
from blog_autopilot.ai.review import (
    _parse_review_response,
    _validate_review,
    detect_self_review_bias,
    format_dimensional_scores,
    format_issues_for_rewrite,
    format_progressive_feedback,
    format_self_review_warning,
    identify_focus_areas,
)
from blog_autopilot.ai.sanitize import sanitize_input
from blog_autopilot.ai.seo import _parse_seo_response, _validate_seo_metadata
from blog_autopilot.ai.json_parser import _parse_json_response
from blog_autopilot.ai.tagger import _parse_tagger_response, validate_tags
from blog_autopilot.tag_registry import (
    build_tagger_prompt_section,
    validate_tags_against_registry,
)
from blog_autopilot.config import AISettings
from blog_autopilot.constants import (
    AI_PROMO_PREVIEW_LIMIT,
    AI_WRITER_INPUT_LIMIT,
    CATEGORY_TEMPERATURE,
    DEFAULT_TEMPERATURE,
    QUALITY_INPUT_PREVIEW_LIMIT,
    SELF_REVIEW_THRESHOLD_ADJUSTMENT,
    SEO_INPUT_PREVIEW_LIMIT,
    SUMMARY_INPUT_PREVIEW_LIMIT,
    TG_PROMO_MAX_LENGTH,
    TG_PROMO_MIN_LENGTH,
)
from blog_autopilot.exceptions import AIAPIError, AIResponseParseError
from blog_autopilot.models import (
    ArticleResult,
    AssociationResult,
    QualityReview,
    SEOMetadata,
    TagSet,
    TokenUsage,
    TokenUsageSummary,
)

logger = logging.getLogger("blog-autopilot")

# 提示词目录：基于本文件所在位置向上一级到 blog_autopilot/prompts
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# 标签注册表不可用时的内联回退（含候选值示例）
_TAG_REFERENCE_FALLBACK = (
    "四级标签体系（从粗到细）:\n"
    "一级 tag_magazine：杂志分类（如：技术周刊、科学前沿、商业评论、文学杂志）\n"
    "二级 tag_science：学科领域（如：AI应用、信号处理、数据科学、量子计算）\n"
    "三级 tag_topic：具体主题（如：API开发、图像去噪、推荐系统、文本生成）\n"
    "四级 tag_content：内容概括，5字以内（如：Claude自动化、小波阈值降噪）\n"
)


def _is_retryable_ai_error(exc: BaseException) -> bool:
    """判断 AI API 错误是否值得重试（排除认证、格式等永久性错误）"""
    msg = str(exc).lower()
    if exc.__cause__:
        msg += " " + str(exc.__cause__).lower()
    non_retryable = ("401", "403", "invalid_api_key", "authentication", "permission")
    return not any(keyword in msg for keyword in non_retryable)


class AIWriter:
    """AI 写作器，延迟创建 OpenAI client"""

    def __init__(self, settings: AISettings) -> None:
        self._settings = settings
        self._client: OpenAI | None = None
        self._usage_summary = TokenUsageSummary()

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.api_key.get_secret_value(),
                base_url=self._settings.api_base,
                default_headers=self._settings.default_headers,
            )
        return self._client

    @property
    def usage_summary(self) -> TokenUsageSummary:
        return self._usage_summary

    def reset_usage(self) -> None:
        self._usage_summary = TokenUsageSummary()

    @staticmethod
    @lru_cache(maxsize=16)
    def _load_prompt(filename: str) -> str:
        """从 prompts/ 目录加载提示词文件（带缓存）"""
        filepath = PROMPTS_DIR / filename
        try:
            return filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise AIAPIError(f"提示词文件不存在: {filepath}")

    @staticmethod
    def _parse_article_response(response: str) -> ArticleResult:
        """
        解析 AI 返回的文章响应：第一行为标题，其余为 HTML 正文。

        抛出:
            AIResponseParseError: 返回内容为空或缺少标题/正文
        """
        lines = [line for line in response.split("\n") if line.strip()]
        if not lines:
            raise AIResponseParseError("AI 返回内容为空")

        title = lines[0].replace("#", "").strip()
        title = title.replace("<h1>", "").replace("</h1>", "").strip()
        title = title.replace("<p>", "").replace("</p>", "").strip()

        # 标题过长时截断：取第一个句号/破折号前的部分
        max_title_len = 40
        if len(title) > max_title_len:
            for sep in ("——", "—", "。", "，", "："):
                idx = title.find(sep)
                if 0 < idx <= max_title_len:
                    title = title[:idx]
                    break
            else:
                title = title[:max_title_len]
        body = "\n".join(lines[1:]).strip()
        body = body.replace("```html", "").replace("```", "")

        if not title or not body:
            raise AIResponseParseError("AI 返回内容缺少标题或正文")

        return ArticleResult(title=title, html_body=body)

    def _get_writer_system_prompt(
        self, category_name: str | None, context: bool = False
    ) -> str:
        """按大类加载 writer system prompt，找不到时回退到通用版本"""
        prefix = "writer_context_system" if context else "writer_system"
        if category_name:
            name = category_name.lower()
            try:
                return self._load_prompt(f"{prefix}_{name}.txt")
            except AIAPIError:
                pass
        return self._load_prompt(f"{prefix}.txt")

    def call_claude(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 4000,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        """API 调用（带重试 + 模型回退）"""
        try:
            return self._call_claude_with_retry(
                prompt, system, model, max_tokens, temperature,
            )
        except AIAPIError:
            # 尝试回退模型
            fallback = self._get_fallback_model(model)
            if fallback:
                logger.warning(f"主模型失败，切换到备用模型: {fallback}")
                return self._call_claude_with_retry(
                    prompt, system, fallback, max_tokens, temperature,
                )
            raise

    def _get_fallback_model(self, model: str | None) -> str | None:
        """获取备用模型名称"""
        primary = model or self._settings.model_writer
        if primary == self._settings.model_writer and self._settings.model_writer_fallback:
            return self._settings.model_writer_fallback
        if primary == self._settings.model_promo and self._settings.model_promo_fallback:
            return self._settings.model_promo_fallback
        # reviewer 和其他使用 promo 模型的任务，回退到 promo fallback
        reviewer = self._settings.model_reviewer
        if reviewer and primary == reviewer and self._settings.model_promo_fallback:
            return self._settings.model_promo_fallback
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable_ai_error),
        reraise=True,
    )
    def _call_claude_with_retry(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 4000,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        """
        通用 API 调用（带重试）。

        抛出:
            AIAPIError: API 调用失败
        """
        try:
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=model or self._settings.model_writer,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            result = response.choices[0].message.content
            usage = response.usage
            effective_model = model or self._settings.model_writer
            logger.info(
                f"API 调用成功 | 模型: {effective_model} | "
                f"输入: {usage.prompt_tokens} | "
                f"输出: {usage.completion_tokens}"
            )
            token_usage = TokenUsage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                model=effective_model,
            )
            self._usage_summary.add(token_usage)
            return result

        except Exception as e:
            raise AIAPIError(f"API 调用出错: {e}") from e

    def generate_blog_post(
        self, raw_text: str, category_name: str | None = None,
        exemplar_context: str = "",
    ) -> ArticleResult:
        """
        生成博客文章 (使用 Writer 模型)。

        Args:
            exemplar_context: 高质量文章示例文本，追加到 system prompt 末尾

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: 返回内容解析失败
        """
        logger.info(f"[Writer] 正在生成博客文章... (原文 {len(raw_text)} 字符)")

        system_prompt = self._get_writer_system_prompt(category_name)
        if exemplar_context:
            system_prompt = system_prompt + "\n" + exemplar_context
        user_template = self._load_prompt("writer_user.txt")
        user_prompt = user_template.format(
            raw_text=sanitize_input(raw_text, AI_WRITER_INPUT_LIMIT)
        )

        temp = CATEGORY_TEMPERATURE.get(category_name, DEFAULT_TEMPERATURE) if category_name else DEFAULT_TEMPERATURE
        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_writer,
            max_tokens=self._settings.writer_max_tokens,
            temperature=temp,
        )

        article = self._parse_article_response(response)

        _warn_unclosed_tags(article.html_body)
        logger.info(
            f"文章生成完成 | 标题: {article.title} | 正文长度: {len(article.html_body)} 字符"
        )
        return article

    def generate_blog_post_with_context(
        self,
        raw_text: str,
        associations: list[AssociationResult] | None = None,
        category_name: str | None = None,
        exemplar_context: str = "",
    ) -> ArticleResult:
        """
        增强版文章生成：注入关联文章上下文。

        有关联文章时使用 writer_context_*.txt 模板，
        无关联文章时回退到原有 writer_*.txt 模板。
        """
        if not associations:
            return self.generate_blog_post(
                raw_text, category_name=category_name,
                exemplar_context=exemplar_context,
            )

        logger.info(
            f"[Writer] 正在生成增强文章... "
            f"(原文 {len(raw_text)} 字符, {len(associations)} 篇关联)"
        )

        context = build_relation_context(associations)

        system_prompt = self._get_writer_system_prompt(category_name, context=True)
        if exemplar_context:
            system_prompt = system_prompt + "\n" + exemplar_context
        user_template = self._load_prompt("writer_context_user.txt")
        user_prompt = user_template.format(
            raw_text=sanitize_input(raw_text, AI_WRITER_INPUT_LIMIT),
            strong_relations=context["strong_relations"],
            medium_relations=context["medium_relations"],
            weak_relations=context["weak_relations"],
        )

        temp = CATEGORY_TEMPERATURE.get(category_name, DEFAULT_TEMPERATURE) if category_name else DEFAULT_TEMPERATURE
        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_writer,
            max_tokens=self._settings.writer_max_tokens,
            temperature=temp,
        )

        article = self._parse_article_response(response)

        _warn_unclosed_tags(article.html_body)
        logger.info(
            f"增强文章生成完成 | 标题: {article.title} | 正文长度: {len(article.html_body)} 字符"
        )
        _log_link_coverage(article.html_body, associations)
        return article

    def generate_summary(self, title: str, html_body: str) -> str:
        """
        生成结构化摘要（200-300 字），用于关联上下文增强。

        抛出:
            AIAPIError: API 调用失败
        """
        logger.info(f"[Summary] 正在生成结构化摘要... (标题: {title})")

        system_prompt = self._load_prompt("summary_system.txt")
        user_prompt = f"标题: {title}\n\n正文:\n{html_body[:SUMMARY_INPUT_PREVIEW_LIMIT]}"

        summary = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_promo,
            max_tokens=self._settings.promo_max_tokens,
        )

        summary = summary.strip()
        logger.info(f"摘要生成完成 | 长度: {len(summary)} 字符")
        return summary

    def generate_promo(
        self,
        title: str,
        blog_content: str,
        hashtag: str | None = None,
    ) -> str:
        """
        生成 Telegram 推广文案 (使用 Promo 模型)。

        抛出:
            AIAPIError: API 调用失败
        """
        logger.info("[Promo] 正在生成推广文案...")

        system_prompt = self._load_prompt("promo_system.txt")
        user_template = self._load_prompt("promo_user.txt")
        user_prompt = user_template.format(
            title=title,
            content_preview=blog_content[:AI_PROMO_PREVIEW_LIMIT],
        )

        promo_text = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_promo,
            max_tokens=self._settings.promo_max_tokens,
        )

        if hashtag:
            promo_text = f"{promo_text}\n\n{hashtag}"

        return promo_text

    # ── SEO 元数据提取 ──

    def extract_seo_metadata(self, title: str, html_body: str) -> SEOMetadata:
        """
        调用 AI 提取 SEO 元数据（meta description / slug / wp_tags）。

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: JSON 解析失败
            SEOExtractionError: 验证失败
        """
        logger.info(f"[SEO] 正在提取 SEO 元数据... (标题: {title})")

        system_prompt = self._load_prompt("seo_system.txt")
        user_template = self._load_prompt("seo_user.txt")
        user_prompt = user_template.format(
            title=title,
            content_preview=html_body[:SEO_INPUT_PREVIEW_LIMIT],
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_promo,
            max_tokens=self._settings.promo_max_tokens,
        )

        data = _parse_seo_response(response)
        seo = _validate_seo_metadata(data)

        logger.info(
            f"SEO 提取完成 | slug: {seo.slug} | "
            f"tags: {', '.join(seo.wp_tags)}"
        )
        return seo

    # ── 质量审核 ──

    def review_quality(
        self, title: str, html_body: str, source_text: str,
        pass_threshold: int | None = None,
        rewrite_threshold: int | None = None,
        calibration_context: str = "",
    ) -> QualityReview:
        """
        调用 AI 对生成的文章进行质量审核。

        Args:
            calibration_context: 历史评分校准文本，追加到 system prompt 末尾

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: JSON 解析失败
            QualityReviewError: 审核验证失败
        """
        logger.info(f"[Review] 正在审核文章质量... (标题: {title})")

        system_prompt = self._load_prompt("review_system.txt")

        # 自审偏差检测
        is_self_review = detect_self_review_bias(self._settings)
        if is_self_review:
            logger.warning("[Review] 检测到自审偏差: writer 和 reviewer 使用相同模型")
            system_prompt += format_self_review_warning()

        if calibration_context:
            system_prompt = system_prompt + "\n" + calibration_context
        user_template = self._load_prompt("review_user.txt")
        user_prompt = user_template.format(
            source_preview=source_text[:QUALITY_INPUT_PREVIEW_LIMIT],
            title=title,
            article_preview=html_body[:QUALITY_INPUT_PREVIEW_LIMIT],
        )

        # 计算有效阈值（自审偏差时仅上调 pass 阈值，使通过更难；rewrite 阈值不变）
        effective_pass = pass_threshold if pass_threshold is not None else self._settings.quality_pass_threshold
        effective_rewrite = rewrite_threshold if rewrite_threshold is not None else self._settings.quality_rewrite_threshold
        if is_self_review:
            effective_pass = min(effective_pass + SELF_REVIEW_THRESHOLD_ADJUSTMENT, 10)

        model = self._settings.model_reviewer or self._settings.model_promo
        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=model,
            max_tokens=self._settings.reviewer_max_tokens,
        )

        data = _parse_review_response(response)
        review = _validate_review(
            data,
            pass_threshold=effective_pass,
            rewrite_threshold=effective_rewrite,
        )

        logger.info(
            f"审核完成 | 一致性: {review.consistency_score} | "
            f"事实性: {review.factuality_score} | "
            f"可读性: {review.readability_score} | "
            f"AI痕迹: {review.ai_cliche_score} | "
            f"综合: {review.overall_score} | 判定: {review.verdict}"
            + (" | 自审偏差补偿已启用" if is_self_review else "")
        )
        return review

    def rewrite_with_feedback(
        self,
        title: str,
        html_body: str,
        source_text: str,
        review: QualityReview,
        category_name: str | None = None,
        previous_review: QualityReview | None = None,
        attempt: int = 1,
        exemplar_context: str = "",
    ) -> ArticleResult:
        """
        根据审核反馈重写文章。

        Args:
            previous_review: 上一次审核结果（用于渐进式反馈对比）
            attempt: 当前重写次数（1-based）
            exemplar_context: 高质量文章示例文本，追加到 system prompt 末尾

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: 返回内容解析失败
        """
        logger.info(f"[Rewrite] 正在根据审核反馈重写文章... (标题: {title}, 第 {attempt} 次)")

        system_prompt = self._get_writer_system_prompt(category_name)
        if exemplar_context:
            system_prompt = system_prompt + "\n" + exemplar_context
        user_template = self._load_prompt("rewrite_feedback_user.txt")

        # 增强反馈信息
        dimensional_scores = format_dimensional_scores(review)
        focus_areas = identify_focus_areas(review)
        progressive = format_progressive_feedback(review, previous_review, attempt)

        user_prompt = user_template.format(
            dimensional_scores=dimensional_scores,
            focus_areas=focus_areas,
            review_summary=review.summary,
            issues_text=format_issues_for_rewrite(review.issues),
            progressive_feedback=progressive,
            source_preview=source_text[:QUALITY_INPUT_PREVIEW_LIMIT],
            title=title,
            article_body=sanitize_input(html_body, AI_WRITER_INPUT_LIMIT),
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_writer,
            max_tokens=self._settings.writer_max_tokens,
        )

        article = self._parse_article_response(response)

        logger.info(
            f"重写完成 | 标题: {article.title} | 正文长度: {len(article.html_body)} 字符"
        )
        return article

    # ── 标签提取 ──

    def extract_tags_and_promo(
        self, article_content: str
    ) -> tuple[TagSet, str, str]:
        """
        调用 AI 提取文章四级标签和 TG 推广文案。

        返回:
            (tags, tg_promo, title) — 四级标签、推广文案、文章标题

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: JSON 解析失败
            TagExtractionError: 标签验证失败
        """
        logger.info(
            f"[Tagger] 正在提取标签... (内容 {len(article_content)} 字符)"
        )

        system_prompt = self._load_prompt("tagger_system.txt")
        # 动态注入注册表标签参考
        tag_ref = build_tagger_prompt_section()
        if tag_ref:
            system_prompt = system_prompt.replace("{tag_reference}", tag_ref)
        else:
            # 注册表不可用时使用内联回退（含候选值示例）
            system_prompt = system_prompt.replace("{tag_reference}", _TAG_REFERENCE_FALLBACK)
        user_template = self._load_prompt("tagger_user.txt")
        user_prompt = user_template.format(
            article_content=sanitize_input(article_content, AI_WRITER_INPUT_LIMIT)
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_promo,
            max_tokens=self._settings.promo_max_tokens,
        )

        data = _parse_tagger_response(response)

        tags = TagSet(
            tag_magazine=data["tag_magazine"],
            tag_science=data["tag_science"],
            tag_topic=data["tag_topic"],
            tag_content=data["tag_content"],
        )
        tags = validate_tags(tags)
        tags = validate_tags_against_registry(tags)
        from blog_autopilot.tag_normalizer import normalize_synonym
        tags = TagSet(
            tag_magazine=normalize_synonym(tags.tag_magazine),
            tag_science=normalize_synonym(tags.tag_science),
            tag_topic=normalize_synonym(tags.tag_topic),
            tag_content=normalize_synonym(tags.tag_content),
        )

        tg_promo = data["tg_promo"].strip()
        promo_len = len(tg_promo)
        if promo_len < TG_PROMO_MIN_LENGTH or promo_len > TG_PROMO_MAX_LENGTH:
            logger.warning(
                f"推广文案长度 {promo_len} 字, "
                f"建议范围 {TG_PROMO_MIN_LENGTH}-{TG_PROMO_MAX_LENGTH}"
            )

        logger.info(
            f"标签提取完成 | {tags.tag_magazine}/{tags.tag_science}/"
            f"{tags.tag_topic}/{tags.tag_content}"
        )
        return tags, tg_promo, data["title"]

    def review_tags(
        self,
        tags: TagSet,
        neighbor_tags: list[dict],
        article_summary: str,
    ) -> TagSet:
        """
        用 AI 审核标签准确性，参考相似文章的标签惯例修正。

        Args:
            tags: 当前四级标签
            neighbor_tags: 最近邻文章的标签列表
            article_summary: 文章摘要/推广文案

        Returns:
            修正后的 TagSet（如 AI 认为无需修正则返回原值）
        """
        logger.info("[TagReview] 标签一致性偏低，调用 AI 复核...")

        system_prompt = self._load_prompt("tag_review_system.txt")
        # 动态注入注册表标签参考
        tag_ref = build_tagger_prompt_section()
        system_prompt = system_prompt.replace(
            "{tag_reference}", tag_ref if tag_ref else _TAG_REFERENCE_FALLBACK
        )
        user_template = self._load_prompt("tag_review_user.txt")

        # 格式化邻居标签
        neighbor_lines = []
        for i, nb in enumerate(neighbor_tags, 1):
            neighbor_lines.append(
                f"  {i}. {nb.get('tag_magazine', '')}"
                f" / {nb.get('tag_science', '')}"
                f" / {nb.get('tag_topic', '')}"
                f" / {nb.get('tag_content', '')}"
            )
        neighbor_text = "\n".join(neighbor_lines) if neighbor_lines else "（无相似文章）"

        user_prompt = user_template.format(
            tag_magazine=tags.tag_magazine,
            tag_science=tags.tag_science,
            tag_topic=tags.tag_topic,
            tag_content=tags.tag_content,
            neighbor_tags=neighbor_text,
            article_summary=sanitize_input(article_summary, AI_PROMO_PREVIEW_LIMIT),
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_promo,
            max_tokens=500,
            temperature=0.3,
        )

        _TAG_REVIEW_REQUIRED = ("tag_magazine", "tag_science", "tag_topic", "tag_content")

        def _validate_tag_review(d: dict) -> None:
            missing = [f for f in _TAG_REVIEW_REQUIRED if f not in d]
            if missing:
                from blog_autopilot.exceptions import AIResponseParseError
                raise AIResponseParseError(
                    f"标签复核响应缺少字段: {', '.join(missing)}"
                )

        data = _parse_json_response(
            response, _validate_tag_review, "标签复核 JSON 解析失败",
        )

        if not data.get("changed", False):
            logger.info("[TagReview] AI 确认标签无需修正")
            return tags

        reviewed = TagSet(
            tag_magazine=data.get("tag_magazine", tags.tag_magazine),
            tag_science=data.get("tag_science", tags.tag_science),
            tag_topic=data.get("tag_topic", tags.tag_topic),
            tag_content=data.get("tag_content", tags.tag_content),
        )
        reviewed = validate_tags(reviewed)
        reviewed = validate_tags_against_registry(reviewed)
        from blog_autopilot.tag_normalizer import normalize_synonym
        reviewed = TagSet(
            tag_magazine=normalize_synonym(reviewed.tag_magazine),
            tag_science=normalize_synonym(reviewed.tag_science),
            tag_topic=normalize_synonym(reviewed.tag_topic),
            tag_content=normalize_synonym(reviewed.tag_content),
        )

        reason = data.get("reason", "")
        logger.info(
            f"[TagReview] 标签已修正: "
            f"{tags.tag_magazine}/{tags.tag_science}/{tags.tag_topic}/{tags.tag_content}"
            f" -> {reviewed.tag_magazine}/{reviewed.tag_science}/"
            f"{reviewed.tag_topic}/{reviewed.tag_content}"
            f" | 原因: {reason}"
        )
        return reviewed
