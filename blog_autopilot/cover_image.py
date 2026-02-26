"""封面图生成与上传模块"""

import base64
import logging
import re
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from openai import OpenAI
from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential, wait_fixed

from blog_autopilot.config import AISettings, WordPressSettings
from blog_autopilot.constants import CATEGORY_COVER_STYLE, DEFAULT_COVER_STYLE
from blog_autopilot.exceptions import CoverImageError

logger = logging.getLogger("blog-autopilot")

# 封面图生成提示词模板（仅基于标题，避免原文内容触发安全过滤）
_COVER_IMAGE_PROMPT_TEMPLATE = (
    "Generate a blog cover image inspired by the following title. "
    "Style: {style}. "
    "Use abstract shapes, gradients, or symbolic imagery to represent the topic. "
    "Do NOT include any text, letters, words, or characters in the image."
)


def _get_media_url(posts_url: str) -> str:
    """从 posts URL 推导 /wp/v2/media endpoint"""
    parsed = urlparse(posts_url)
    qs = parse_qs(parsed.query)
    if "rest_route" in qs:
        route = qs["rest_route"][0].rsplit("/", 1)[0] + "/media"
        new_qs = urlencode({"rest_route": route})
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_qs}"
    return posts_url.rsplit("/", 1)[0] + "/media"


def _is_server_error(result: int | None) -> bool:
    """tenacity 重试条件：返回 None 时重试"""
    return result is None


class CoverImageGenerator:
    """封面图生成器，使用 DALL-E images API，延迟初始化"""

    def __init__(self, ai_settings: AISettings) -> None:
        self._settings = ai_settings
        self._client: OpenAI | None = None
        self._fallback_client: OpenAI | None = None

    @property
    def _has_fallback(self) -> bool:
        """是否配置了备用封面图 API"""
        return bool(
            self._settings.cover_image_fallback_api_key
            and self._settings.cover_image_fallback_api_base
        )

    def _get_client(self) -> OpenAI:
        if self._client is None:
            api_key = (
                self._settings.cover_image_api_key.get_secret_value()
                if self._settings.cover_image_api_key
                else self._settings.api_key.get_secret_value()
            )
            api_base = self._settings.cover_image_api_base
            self._client = OpenAI(
                api_key=api_key,
                base_url=api_base,
                default_headers=self._settings.default_headers,
            )
        return self._client

    def _get_fallback_client(self) -> OpenAI:
        if self._fallback_client is None:
            self._fallback_client = OpenAI(
                api_key=self._settings.cover_image_fallback_api_key.get_secret_value(),
                base_url=self._settings.cover_image_fallback_api_base,
                default_headers=self._settings.default_headers,
            )
        return self._fallback_client

    def _call_image_api(self, client: OpenAI, model: str, prompt: str) -> bytes:
        """调用图片生成 API，返回 PNG bytes（DALL-E images.generate 格式）"""
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            size="1792x1024",
            quality="standard",
            response_format="b64_json",
            n=1,
        )
        b64_data = resp.data[0].b64_json
        if not b64_data:
            raise CoverImageError("DALL-E 响应中未包含图片数据")
        return base64.b64decode(b64_data)

    def _call_chat_image_api(self, client: OpenAI, model: str, prompt: str) -> bytes:
        """调用 chat completions 图片生成 API（Gemini 格式），返回图片 bytes"""
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        )
        content = resp.choices[0].message.content
        if not content:
            raise CoverImageError("Chat API 响应中未包含内容")
        # 从 markdown 格式 ![image](data:image/...;base64,...) 提取 base64
        match = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=\s]+)", content)
        if not match:
            raise CoverImageError("Chat API 响应中未包含图片数据")
        b64_data = match.group(1).replace("\n", "").replace(" ", "")
        return base64.b64decode(b64_data)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _generate_with_primary(self, prompt: str) -> bytes:
        """主 API 生成封面图（带 3 次重试），走 chat completions 格式"""
        client = self._get_client()
        return self._call_chat_image_api(client, self._settings.model_cover_image, prompt)

    def generate_image(
        self, title: str, html_body: str,
        category_name: str | None = None,
    ) -> bytes:
        """
        调用 DALL-E API 生成封面图。

        Args:
            category_name: 大类名称，用于选择分类专属视觉风格

        返回 PNG 图片 bytes。
        主 API 3 次重试全部失败后，若配置了备用 API 则尝试一次。
        抛出 CoverImageError 当生成失败时。
        """
        style = CATEGORY_COVER_STYLE.get(category_name, DEFAULT_COVER_STYLE) if category_name else DEFAULT_COVER_STYLE
        base_prompt = _COVER_IMAGE_PROMPT_TEMPLATE.format(style=style)
        prompt = f"{base_prompt}\n\nTitle: {title}"

        try:
            return self._generate_with_primary(prompt)
        except Exception as primary_err:
            if not self._has_fallback:
                if isinstance(primary_err, CoverImageError):
                    raise
                raise CoverImageError(f"封面图生成失败: {primary_err}") from primary_err
            logger.warning("主封面图 API 失败，尝试备用 API: %s", primary_err)
            try:
                fallback_model = self._settings.model_cover_image_fallback or self._settings.model_cover_image
                client = self._get_fallback_client()
                return self._call_image_api(client, fallback_model, prompt)
            except CoverImageError:
                raise
            except Exception as fb_err:
                raise CoverImageError(f"备用封面图 API 也失败: {fb_err}") from fb_err


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(5),
    retry=retry_if_result(_is_server_error),
    reraise=True,
)
def upload_media_to_wordpress(
    image_data: bytes,
    filename: str,
    settings: WordPressSettings,
) -> int | None:
    """
    上传图片到 WordPress 媒体库。

    返回 media_id，失败返回 None。
    """
    media_url = _get_media_url(settings.url)
    credentials = f"{settings.user}:{settings.app_password.get_secret_value()}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    # 文件名 ASCII 安全处理：非 ASCII 字符替换为下划线，避免 Content-Disposition header 编码失败
    safe_filename = re.sub(r"[^\x00-\x7F]", "_", filename)
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "Content-Type": "image/png",
    }

    try:
        resp = requests.post(
            media_url,
            headers=headers,
            data=image_data,
            timeout=60,
        )
        if resp.status_code == 201:
            media_id = resp.json().get("id")
            logger.info(f"封面图上传成功, media_id={media_id}")
            return media_id
        if resp.status_code >= 500:
            logger.warning(f"媒体上传服务器错误 ({resp.status_code}), 将重试...")
            return None
        raise CoverImageError(
            f"媒体上传失败 (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    except CoverImageError:
        raise
    except requests.exceptions.RequestException as e:
        raise CoverImageError(f"媒体上传请求异常: {e}") from e
