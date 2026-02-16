"""WordPress 发布模块"""

import base64
import logging
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError

logger = logging.getLogger("blog-autopilot")


@dataclass(frozen=True)
class PublishResult:
    """WordPress 发布结果"""
    url: str
    post_id: int


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


def _get_tags_url(posts_url: str) -> str:
    """从 posts URL 推导 tags endpoint URL"""
    parsed = urlparse(posts_url)
    qs = parse_qs(parsed.query)
    if "rest_route" in qs:
        # ?rest_route=/wp/v2/posts → ?rest_route=/wp/v2/tags
        route = qs["rest_route"][0].rsplit("/", 1)[0] + "/tags"
        new_qs = urlencode({"rest_route": route})
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_qs}"
    # Pretty permalink: .../wp-json/wp/v2/posts → .../wp-json/wp/v2/tags
    return posts_url.rsplit("/", 1)[0] + "/tags"


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(3),
    retry=retry_if_result(_is_server_error),
    reraise=True,
)
def _create_or_get_wp_tag(
    tag_name: str,
    tags_url: str,
    headers: dict[str, str],
) -> int | None:
    """
    创建或获取 WordPress 标签 ID。

    返回标签 ID，失败返回 None（不阻断流程）。
    """
    try:
        resp = requests.post(
            tags_url,
            headers=headers,
            json={"name": tag_name},
            timeout=15,
        )

        if resp.status_code == 201:
            return resp.json()["id"]

        if resp.status_code == 400:
            # term_exists: 标签已存在
            data = resp.json()
            term_id = data.get("data", {}).get("term_id")
            if term_id:
                return int(term_id)
            # 回退：搜索标签
            search_resp = requests.get(
                tags_url,
                headers=headers,
                params={"search": tag_name, "per_page": 1},
                timeout=10,
            )
            if search_resp.status_code == 200:
                results = search_resp.json()
                if results:
                    return results[0]["id"]
            return None

        if resp.status_code >= 500:
            logger.warning(f"标签创建服务器错误 ({resp.status_code}), 将重试...")
            return None  # 触发 tenacity 重试

        logger.warning(f"标签创建失败 ({resp.status_code}): {tag_name}")
        return None

    except requests.exceptions.RequestException as e:
        logger.warning(f"标签创建请求异常: {tag_name} - {e}")
        return None


def ensure_wp_tags(
    tag_names: tuple[str, ...] | list[str],
    settings: WordPressSettings,
) -> list[int]:
    """
    批量创建/获取 WordPress 标签 ID。

    跳过失败的标签，返回成功的 ID 列表。
    """
    tags_url = _get_tags_url(settings.url)
    headers = _build_auth_header(settings)
    tag_ids = []

    for name in tag_names:
        tag_id = _create_or_get_wp_tag(name, tags_url, headers)
        if tag_id is not None:
            tag_ids.append(tag_id)
        else:
            logger.warning(f"跳过标签: {name}")

    logger.info(f"WordPress 标签就绪: {len(tag_ids)}/{len(tag_names)}")
    return tag_ids


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
    excerpt: str | None = None,
    slug: str | None = None,
    tag_ids: list[int] | None = None,
    featured_media: int | None = None,
) -> PublishResult:
    """
    发布文章到 WordPress。

    返回 PublishResult（含文章链接和 post_id）。
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
    if excerpt:
        payload["excerpt"] = excerpt
    if slug:
        payload["slug"] = slug
    if tag_ids:
        payload["tags"] = tag_ids
    if featured_media:
        payload["featured_media"] = featured_media

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
    return PublishResult(url=post_link, post_id=post_id)


def _build_post_url(post_id: int, settings: WordPressSettings) -> str:
    """从 posts URL 构造单篇文章 URL"""
    parsed = urlparse(settings.url)
    qs = parse_qs(parsed.query)
    if "rest_route" in qs:
        route = qs["rest_route"][0].rstrip("/") + f"/{post_id}"
        new_qs = urlencode({"rest_route": route})
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_qs}"
    return settings.url.rstrip("/") + f"/{post_id}"


def get_wp_post_content(post_id: int, settings: WordPressSettings) -> str | None:
    """获取 WordPress 文章内容（raw HTML）"""
    headers = _build_auth_header(settings)
    url = _build_post_url(post_id, settings)

    try:
        resp = requests.get(
            url, headers=headers, params={"context": "edit"}, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", {})
        # WP REST API 在 context=edit 时返回 {"raw": "...", "rendered": "..."}
        if isinstance(content, dict):
            return content.get("raw") or content.get("rendered", "")
        return str(content)
    except Exception as e:
        logger.warning(f"获取文章内容失败 (post_id={post_id}): {e}")
        return None


def update_wp_post_content(
    post_id: int, content: str, settings: WordPressSettings,
) -> bool:
    """更新 WordPress 文章内容"""
    headers = _build_auth_header(settings)
    url = _build_post_url(post_id, settings)

    try:
        resp = requests.post(
            url, headers=headers, json={"content": content}, timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"文章内容更新成功 (post_id={post_id})")
        return True
    except Exception as e:
        logger.warning(f"文章内容更新失败 (post_id={post_id}): {e}")
        return False


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
