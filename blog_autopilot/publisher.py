"""WordPress 发布模块"""

import base64
import logging

import requests
from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError

logger = logging.getLogger("blog-autopilot")


def _is_server_error(result: str | None) -> bool:
    """tenacity 重试条件：仅当返回 None（即 5xx 失败）时重试"""
    return result is None


def _build_auth_header(settings: WordPressSettings) -> dict[str, str]:
    credentials = f"{settings.user}:{settings.app_password.get_secret_value()}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(5),
    retry=retry_if_result(_is_server_error),
    reraise=True,
)
def post_to_wordpress(
    title: str,
    content: str,
    settings: WordPressSettings,
    status: str = "publish",
    category_id: int | None = None,
) -> str:
    """
    发布文章到 WordPress。

    返回文章链接 URL。
    抛出 WordPressError 当发布失败时。
    """
    effective_category = category_id or settings.target_category_id
    logger.info(
        f"正在发布到博客: 《{title}》 "
        f"(状态: {status}, 分类ID: {effective_category})"
    )

    headers = _build_auth_header(settings)
    payload = {
        "title": title,
        "content": content,
        "status": status,
        "categories": [effective_category],
    }

    try:
        resp = requests.post(
            settings.url, headers=headers, json=payload, timeout=30
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        body = e.response.text[:500]
        # 5xx 返回 None 触发重试
        if status_code >= 500:
            logger.warning(f"WordPress 服务器错误 ({status_code}), 将重试...")
            return None  # type: ignore[return-value]
        raise WordPressError(
            f"博客发布失败 (HTTP {status_code}): {body}",
            status_code=status_code,
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise WordPressError("无法连接到 WordPress, 请检查 WP_URL") from e
    except Exception as e:
        raise WordPressError(f"博客发布异常: {e}") from e

    data = resp.json()
    post_id = data.get("id")
    post_link = data.get("link")
    logger.info(f"博客发布成功! ID: {post_id} | URL: {post_link}")
    return post_link


def test_wp_connection(settings: WordPressSettings) -> bool:
    """测试 WordPress 连接和认证"""
    logger.info("测试 WordPress 连接...")

    credentials = f"{settings.user}:{settings.app_password.get_secret_value()}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {"Authorization": f"Basic {token}"}

    try:
        resp = requests.get(
            settings.url, headers=headers, params={"per_page": 1}, timeout=10
        )
        if resp.status_code == 200:
            logger.info("WordPress 连接成功, 认证有效")
            return True
        elif resp.status_code == 401:
            logger.error("WordPress 认证失败, 请检查用户名和应用密码")
            return False
        else:
            logger.warning(f"WordPress 返回状态码: {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"连接测试失败: {e}")
        return False
