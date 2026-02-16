"""封面图生成与上传模块"""

import base64
import logging
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from openai import OpenAI
from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential, wait_fixed

from blog_autopilot.config import AISettings, WordPressSettings
from blog_autopilot.exceptions import CoverImageError

logger = logging.getLogger("blog-autopilot")

# 封面图生成提示词（仅基于标题，避免原文内容触发安全过滤）
_COVER_IMAGE_PROMPT = (
    "Generate a blog cover image inspired by the following title. "
    "Style: modern, clean, minimalist with vibrant colors. "
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

    def _get_client(self) -> OpenAI:
        if self._client is None:
            # 优先使用封面图专属 API key/base，否则回退到通用配置
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def generate_image(self, title: str, html_body: str) -> bytes:
        """
        调用 DALL-E API 生成封面图。

        返回 PNG 图片 bytes。
        抛出 CoverImageError 当生成失败时。
        """
        prompt = f"{_COVER_IMAGE_PROMPT}\n\nTitle: {title}"

        try:
            client = self._get_client()
            resp = client.images.generate(
                model=self._settings.model_cover_image,
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
        except CoverImageError:
            raise
        except Exception as e:
            raise CoverImageError(f"封面图生成失败: {e}") from e


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
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "image/png",
    }

    try:
        resp = requests.post(
            media_url,
            headers=headers,
            data=image_data,
            timeout=30,
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
