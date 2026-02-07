"""AI 写作模块 — AIWriter 类，延迟初始化，依赖注入"""

import logging
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from blog_autopilot.config import AISettings
from blog_autopilot.constants import AI_WRITER_INPUT_LIMIT, AI_PROMO_PREVIEW_LIMIT
from blog_autopilot.exceptions import AIAPIError, AIResponseParseError
from blog_autopilot.models import ArticleResult

logger = logging.getLogger("blog-autopilot")

# 提示词目录：基于本文件所在位置
PROMPTS_DIR = Path(__file__).parent / "prompts"


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
