"""AI 写作模块 — AIWriter 类，延迟初始化，依赖注入"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from blog_autopilot.config import AISettings
from blog_autopilot.constants import (
    AI_PROMO_PREVIEW_LIMIT,
    AI_WRITER_INPUT_LIMIT,
    QUALITY_INPUT_PREVIEW_LIMIT,
    QUALITY_PASS_THRESHOLD,
    QUALITY_REQUIRED_FIELDS,
    QUALITY_REWRITE_THRESHOLD,
    QUALITY_WEIGHT_AI_CLICHE,
    QUALITY_WEIGHT_CONSISTENCY,
    QUALITY_WEIGHT_READABILITY,
    SEO_INPUT_PREVIEW_LIMIT,
    SEO_META_DESC_MAX_LENGTH,
    SEO_META_DESC_MIN_LENGTH,
    SEO_SLUG_MAX_LENGTH,
    SEO_WP_TAG_MAX_LENGTH,
    SEO_WP_TAGS_MAX_COUNT,
    SEO_WP_TAGS_MIN_COUNT,
    TAG_CONTENT_MAX_LENGTH,
    TAG_MAX_LENGTH,
    TG_PROMO_MAX_LENGTH,
    TG_PROMO_MIN_LENGTH,
)
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    QualityReviewError,
    SEOExtractionError,
    TagExtractionError,
)
from blog_autopilot.models import (
    ArticleResult,
    AssociationResult,
    QualityIssue,
    QualityReview,
    SEOMetadata,
    TagSet,
)

logger = logging.getLogger("blog-autopilot")

# 提示词目录：基于本文件所在位置
PROMPTS_DIR = Path(__file__).parent / "prompts"

# 标签提取 JSON 必需字段
_TAGGER_REQUIRED_FIELDS = (
    "title", "tag_magazine", "tag_science",
    "tag_topic", "tag_content", "tg_promo",
)

# SEO 提取 JSON 必需字段
_SEO_REQUIRED_FIELDS = ("meta_description", "slug", "wp_tags")


class AIWriter:
    """AI 写作器，延迟创建 OpenAI client"""

    def __init__(self, settings: AISettings) -> None:
        self._settings = settings
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.api_key.get_secret_value(),
                base_url=self._settings.api_base,
                default_headers=self._settings.default_headers,
            )
        return self._client

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def call_claude(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 4000,
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
                temperature=0.7,
            )

            result = response.choices[0].message.content
            usage = response.usage
            logger.info(
                f"API 调用成功 | 模型: {model} | "
                f"输入: {usage.prompt_tokens} | "
                f"输出: {usage.completion_tokens}"
            )
            return result

        except Exception as e:
            raise AIAPIError(f"API 调用出错: {e}") from e

    def generate_blog_post(
        self, raw_text: str, category_name: str | None = None
    ) -> ArticleResult:
        """
        生成博客文章 (使用 Writer 模型)。

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: 返回内容解析失败
        """
        logger.info(f"[Writer] 正在生成博客文章... (原文 {len(raw_text)} 字符)")

        system_prompt = self._get_writer_system_prompt(category_name)
        user_template = self._load_prompt("writer_user.txt")
        user_prompt = user_template.format(
            raw_text=raw_text[:AI_WRITER_INPUT_LIMIT]
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_writer,
            max_tokens=self._settings.writer_max_tokens,
        )

        article = self._parse_article_response(response)

        logger.info(
            f"文章生成完成 | 标题: {article.title} | 正文长度: {len(article.html_body)} 字符"
        )
        return article

    def generate_blog_post_with_context(
        self,
        raw_text: str,
        associations: list[AssociationResult] | None = None,
        category_name: str | None = None,
    ) -> ArticleResult:
        """
        增强版文章生成：注入关联文章上下文。

        有关联文章时使用 writer_context_*.txt 模板，
        无关联文章时回退到原有 writer_*.txt 模板。
        """
        if not associations:
            return self.generate_blog_post(raw_text, category_name=category_name)

        logger.info(
            f"[Writer] 正在生成增强文章... "
            f"(原文 {len(raw_text)} 字符, {len(associations)} 篇关联)"
        )

        context = build_relation_context(associations)

        system_prompt = self._get_writer_system_prompt(category_name, context=True)
        user_template = self._load_prompt("writer_context_user.txt")
        user_prompt = user_template.format(
            raw_text=raw_text[:AI_WRITER_INPUT_LIMIT],
            strong_relations=context["strong_relations"],
            medium_relations=context["medium_relations"],
            weak_relations=context["weak_relations"],
        )

        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=self._settings.model_writer,
            max_tokens=self._settings.writer_max_tokens,
        )

        article = self._parse_article_response(response)

        logger.info(
            f"增强文章生成完成 | 标题: {article.title} | 正文长度: {len(article.html_body)} 字符"
        )
        _log_link_coverage(article.html_body, associations)
        return article

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
    ) -> QualityReview:
        """
        调用 AI 对生成的文章进行质量审核。

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: JSON 解析失败
            QualityReviewError: 审核验证失败
        """
        logger.info(f"[Review] 正在审核文章质量... (标题: {title})")

        system_prompt = self._load_prompt("review_system.txt")
        user_template = self._load_prompt("review_user.txt")
        user_prompt = user_template.format(
            source_preview=source_text[:QUALITY_INPUT_PREVIEW_LIMIT],
            title=title,
            article_preview=html_body[:QUALITY_INPUT_PREVIEW_LIMIT],
        )

        model = self._settings.model_reviewer or self._settings.model_promo
        response = self.call_claude(
            prompt=user_prompt,
            system=system_prompt,
            model=model,
            max_tokens=self._settings.reviewer_max_tokens,
        )

        data = _parse_review_response(response)
        review = _validate_review(data)

        logger.info(
            f"审核完成 | 一致性: {review.consistency_score} | "
            f"可读性: {review.readability_score} | "
            f"AI痕迹: {review.ai_cliche_score} | "
            f"综合: {review.overall_score} | 判定: {review.verdict}"
        )
        return review

    def rewrite_with_feedback(
        self,
        title: str,
        html_body: str,
        source_text: str,
        review: QualityReview,
        category_name: str | None = None,
    ) -> ArticleResult:
        """
        根据审核反馈重写文章。

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: 返回内容解析失败
        """
        logger.info(f"[Rewrite] 正在根据审核反馈重写文章... (标题: {title})")

        system_prompt = self._get_writer_system_prompt(category_name)
        user_template = self._load_prompt("rewrite_feedback_user.txt")
        user_prompt = user_template.format(
            review_summary=review.summary,
            issues_text=format_issues_for_rewrite(review.issues),
            source_preview=source_text[:QUALITY_INPUT_PREVIEW_LIMIT],
            title=title,
            article_body=html_body[:AI_WRITER_INPUT_LIMIT],
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

    # ── 标签提取 (Task 11) ──

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
        user_template = self._load_prompt("tagger_user.txt")
        user_prompt = user_template.format(
            article_content=article_content[:AI_WRITER_INPUT_LIMIT]
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


# ── 通用 JSON 解析 ──


def _parse_json_response(
    response_text: str,
    validate_fn,
    error_prefix: str,
) -> dict:
    """
    通用 JSON 解析：处理 AI 返回的各种格式变体。

    尝试顺序：
    1. 直接解析
    2. 提取 markdown 代码块中的 JSON
    3. 提取第一个 { 到最后一个 } 之间的子串

    抛出:
        AIResponseParseError: 解析失败或缺少必需字段
    """
    text = response_text.strip()

    # 尝试 1: 直接解析
    try:
        data = json.loads(text)
        validate_fn(data)
        return data
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 markdown 代码块
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1).strip())
            validate_fn(data)
            return data
        except json.JSONDecodeError:
            pass

    # 尝试 3: 提取 { ... } 子串
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            data = json.loads(text[first_brace:last_brace + 1])
            validate_fn(data)
            return data
        except json.JSONDecodeError:
            pass

    raise AIResponseParseError(
        f"{error_prefix}。响应内容前 200 字符: "
        f"{text[:200]}"
    )


# ── 标签提取 JSON 解析 (Task 13) ──


def _parse_tagger_response(response_text: str) -> dict:
    """解析标签提取 AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_tagger_fields,
        "无法从 AI 响应中解析 JSON",
    )


def _validate_tagger_fields(data: dict) -> None:
    """验证解析后的 dict 包含所有必需字段"""
    missing = [f for f in _TAGGER_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"AI 响应缺少必需字段: {', '.join(missing)}"
        )


# ── SEO 响应解析与验证 ──


def _parse_seo_response(response_text: str) -> dict:
    """解析 SEO AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_seo_fields,
        "无法从 SEO 响应中解析 JSON",
    )


def _validate_seo_fields(data: dict) -> None:
    """验证 SEO 响应包含所有必需字段"""
    missing = [f for f in _SEO_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"SEO 响应缺少必需字段: {', '.join(missing)}"
        )


def _validate_seo_metadata(data: dict) -> SEOMetadata:
    """
    验证并规范化 SEO 元数据。

    抛出:
        SEOExtractionError: 验证失败
    """
    # meta_description
    desc = str(data.get("meta_description", "")).strip()
    if not desc:
        raise SEOExtractionError("meta_description 不能为空")
    if len(desc) < SEO_META_DESC_MIN_LENGTH:
        logger.warning(
            f"meta_description 偏短: {len(desc)} 字符 "
            f"(建议 {SEO_META_DESC_MIN_LENGTH}-{SEO_META_DESC_MAX_LENGTH})"
        )
    if len(desc) > SEO_META_DESC_MAX_LENGTH:
        desc = desc[:SEO_META_DESC_MAX_LENGTH]

    # slug
    slug = str(data.get("slug", "")).strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    if not slug:
        raise SEOExtractionError("slug 规范化后为空")
    if len(slug) > SEO_SLUG_MAX_LENGTH:
        slug = slug[:SEO_SLUG_MAX_LENGTH].rstrip("-")

    # wp_tags
    raw_tags = data.get("wp_tags")
    if not isinstance(raw_tags, list):
        raise SEOExtractionError("wp_tags 必须是数组")
    tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    tags = [t[:SEO_WP_TAG_MAX_LENGTH] for t in tags]
    if len(tags) < SEO_WP_TAGS_MIN_COUNT:
        raise SEOExtractionError(
            f"wp_tags 数量不足: {len(tags)} < {SEO_WP_TAGS_MIN_COUNT}"
        )
    tags = tags[:SEO_WP_TAGS_MAX_COUNT]

    return SEOMetadata(
        meta_description=desc,
        slug=slug,
        wp_tags=tuple(tags),
    )


# ── 质量审核 JSON 解析与验证 ──


def _parse_review_response(response_text: str) -> dict:
    """解析质量审核 AI 响应 JSON"""
    return _parse_json_response(
        response_text, _validate_review_fields,
        "无法从审核响应中解析 JSON",
    )


def _validate_review_fields(data: dict) -> None:
    """验证审核响应包含所有必需字段"""
    missing = [f for f in QUALITY_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"审核响应缺少必需字段: {', '.join(missing)}"
        )


def _validate_review(data: dict) -> QualityReview:
    """
    验证并构建 QualityReview 对象。

    - 分数 clamp 到 1-10（容错，不抛异常）
    - Python 端重算 overall_score（LLM 算术不可靠）
    - 根据阈值推导 verdict

    抛出:
        QualityReviewError: 分数不是整数
    """
    def _clamp_score(value, field_name: str) -> int:
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            raise QualityReviewError(
                f"{field_name} 必须是整数，实际值: {value!r}"
            )
        return max(1, min(10, score))

    consistency = _clamp_score(data["consistency"], "consistency")
    readability = _clamp_score(data["readability"], "readability")
    ai_cliche = _clamp_score(data["ai_cliche"], "ai_cliche")

    overall = round(
        consistency * QUALITY_WEIGHT_CONSISTENCY
        + readability * QUALITY_WEIGHT_READABILITY
        + ai_cliche * QUALITY_WEIGHT_AI_CLICHE
    )

    if overall >= QUALITY_PASS_THRESHOLD:
        verdict = "pass"
    elif overall >= QUALITY_REWRITE_THRESHOLD:
        verdict = "rewrite"
    else:
        verdict = "draft"

    # 解析 issues
    raw_issues = data.get("issues", [])
    issues = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, dict):
                issues.append(QualityIssue(
                    category=str(item.get("category", "")),
                    severity=str(item.get("severity", "medium")),
                    description=str(item.get("description", "")),
                    suggestion=str(item.get("suggestion", "")),
                ))

    summary = str(data.get("summary", ""))[:200]

    return QualityReview(
        consistency_score=consistency,
        readability_score=readability,
        ai_cliche_score=ai_cliche,
        overall_score=overall,
        verdict=verdict,
        issues=tuple(issues),
        summary=summary,
    )


def format_issues_for_rewrite(issues: tuple[QualityIssue, ...]) -> str:
    """将问题列表格式化为重写提示文本"""
    if not issues:
        return "无具体问题记录。"
    lines = []
    for i, issue in enumerate(issues, 1):
        lines.append(
            f"{i}. [{issue.severity}] {issue.category}: "
            f"{issue.description}\n   建议: {issue.suggestion}"
        )
    return "\n".join(lines)


# ── 标签验证与规范化 (Task 12) ──


def normalize_tag(tag: str) -> str:
    """规范化单个标签：去除空白、合并多余空格"""
    tag = tag.strip()
    # 全角空格 → 半角
    tag = tag.replace("\u3000", " ")
    # 合并连续空格
    tag = re.sub(r"\s+", " ", tag)
    return tag


def validate_tags(tags: TagSet) -> TagSet:
    """
    验证并规范化四级标签。

    抛出:
        TagExtractionError: 标签为空或超长
    """
    normalized = {}
    limits = {
        "tag_magazine": TAG_MAX_LENGTH,
        "tag_science": TAG_MAX_LENGTH,
        "tag_topic": TAG_MAX_LENGTH,
        "tag_content": TAG_CONTENT_MAX_LENGTH,
    }

    for field_name, max_len in limits.items():
        value = getattr(tags, field_name)
        value = normalize_tag(value)

        if not value:
            raise TagExtractionError(f"标签 {field_name} 不能为空")

        if len(value) > max_len:
            raise TagExtractionError(
                f"标签 {field_name} 超长: {len(value)} > {max_len}"
            )

        normalized[field_name] = value

    return TagSet(**normalized)


# ── 关联上下文组装 (Task 21) ──


def build_relation_context(
    associations: list[AssociationResult],
) -> dict[str, str]:
    """
    将关联查询结果按强度分组，格式化为 Prompt 上下文。

    返回:
        {"strong_relations": "...", "medium_relations": "...", "weak_relations": "..."}
    """
    groups: dict[str, list[str]] = {
        "强关联": [],
        "中关联": [],
        "弱关联": [],
    }

    for assoc in associations:
        level = assoc.relation_level
        if level in groups:
            entry = (
                f"  {len(groups[level]) + 1}. "
                f"《{assoc.article.title}》"
            )
            if assoc.article.url:
                entry += f"\n     链接: {assoc.article.url}"
            entry += f"\n     {assoc.article.tg_promo}"
            groups[level].append(entry)

    return {
        "strong_relations": "\n".join(groups["强关联"]) if groups["强关联"] else "",
        "medium_relations": "\n".join(groups["中关联"]) if groups["中关联"] else "",
        "weak_relations": "\n".join(groups["弱关联"]) if groups["弱关联"] else "",
    }


def _log_link_coverage(
    html_body: str,
    associations: list[AssociationResult],
) -> None:
    """检查生成的 HTML 中内链覆盖率并记录日志"""
    linkable = [a for a in associations if a.article.url]
    if not linkable:
        return
    linked = sum(1 for a in linkable if a.article.url in html_body)
    logger.info(f"内链覆盖: {linked}/{len(linkable)} 篇关联文章已生成内链")
    if linked == 0 and len(linkable) >= 2:
        logger.warning("AI 未生成任何内链，可能需要调整提示词")
