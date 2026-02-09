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
    TAG_CONTENT_MAX_LENGTH,
    TAG_MAX_LENGTH,
    TG_PROMO_MAX_LENGTH,
    TG_PROMO_MIN_LENGTH,
)
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    TagExtractionError,
)
from blog_autopilot.models import ArticleResult, AssociationResult, TagSet

logger = logging.getLogger("blog-autopilot")

# 提示词目录：基于本文件所在位置
PROMPTS_DIR = Path(__file__).parent / "prompts"

# 标签提取 JSON 必需字段
_TAGGER_REQUIRED_FIELDS = (
    "title", "tag_magazine", "tag_science",
    "tag_topic", "tag_content", "tg_promo",
)


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
    @lru_cache(maxsize=8)
    def _load_prompt(filename: str) -> str:
        """从 prompts/ 目录加载提示词文件（带缓存）"""
        filepath = PROMPTS_DIR / filename
        try:
            return filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise AIAPIError(f"提示词文件不存在: {filepath}")

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

    def generate_blog_post(self, raw_text: str) -> ArticleResult:
        """
        生成博客文章 (使用 Writer 模型)。

        抛出:
            AIAPIError: API 调用失败
            AIResponseParseError: 返回内容解析失败
        """
        logger.info(f"[Writer] 正在生成博客文章... (原文 {len(raw_text)} 字符)")

        system_prompt = self._load_prompt("writer_system.txt")
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

        lines = [line for line in response.split("\n") if line.strip()]
        if not lines:
            raise AIResponseParseError("AI 返回内容为空")

        # 提取标题（去掉可能残留的 # 或 HTML 标签）
        title = lines[0].replace("#", "").strip()
        title = title.replace("<h1>", "").replace("</h1>", "").strip()

        # 提取正文
        body = "\n".join(lines[1:]).strip()
        body = body.replace("```html", "").replace("```", "")

        if not title or not body:
            raise AIResponseParseError("AI 返回内容缺少标题或正文")

        logger.info(
            f"文章生成完成 | 标题: {title} | 正文长度: {len(body)} 字符"
        )
        return ArticleResult(title=title, html_body=body)

    def generate_blog_post_with_context(
        self,
        raw_text: str,
        associations: list[AssociationResult] | None = None,
    ) -> ArticleResult:
        """
        增强版文章生成：注入关联文章上下文。

        有关联文章时使用 writer_context_*.txt 模板，
        无关联文章时回退到原有 writer_*.txt 模板。
        """
        if not associations:
            return self.generate_blog_post(raw_text)

        logger.info(
            f"[Writer] 正在生成增强文章... "
            f"(原文 {len(raw_text)} 字符, {len(associations)} 篇关联)"
        )

        context = build_relation_context(associations)

        system_prompt = self._load_prompt("writer_context_system.txt")
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

        lines = [line for line in response.split("\n") if line.strip()]
        if not lines:
            raise AIResponseParseError("AI 返回内容为空")

        title = lines[0].replace("#", "").strip()
        title = title.replace("<h1>", "").replace("</h1>", "").strip()
        body = "\n".join(lines[1:]).strip()
        body = body.replace("```html", "").replace("```", "")

        if not title or not body:
            raise AIResponseParseError("AI 返回内容缺少标题或正文")

        logger.info(
            f"增强文章生成完成 | 标题: {title} | 正文长度: {len(body)} 字符"
        )
        return ArticleResult(title=title, html_body=body)

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


# ── 标签提取 JSON 解析 (Task 13) ──


def _parse_tagger_response(response_text: str) -> dict:
    """
    健壮的 JSON 解析：处理 AI 返回的各种格式变体。

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
        _validate_tagger_fields(data)
        return data
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 markdown 代码块
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1).strip())
            _validate_tagger_fields(data)
            return data
        except json.JSONDecodeError:
            pass

    # 尝试 3: 提取 { ... } 子串
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            data = json.loads(text[first_brace:last_brace + 1])
            _validate_tagger_fields(data)
            return data
        except json.JSONDecodeError:
            pass

    raise AIResponseParseError(
        f"无法从 AI 响应中解析 JSON。响应内容前 200 字符: "
        f"{text[:200]}"
    )


def _validate_tagger_fields(data: dict) -> None:
    """验证解析后的 dict 包含所有必需字段"""
    missing = [f for f in _TAGGER_REQUIRED_FIELDS if f not in data]
    if missing:
        raise AIResponseParseError(
            f"AI 响应缺少必需字段: {', '.join(missing)}"
        )


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
                f"《{assoc.article.title}》\n"
                f"     {assoc.article.tg_promo}"
            )
            groups[level].append(entry)

    return {
        "strong_relations": "\n".join(groups["强关联"]) if groups["强关联"] else "",
        "medium_relations": "\n".join(groups["中关联"]) if groups["中关联"] else "",
        "weak_relations": "\n".join(groups["弱关联"]) if groups["弱关联"] else "",
    }
