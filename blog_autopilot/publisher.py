"""WordPress 发布模块"""

import base64
import logging
import re as _re
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from tenacity import retry, retry_if_exception, retry_if_result, stop_after_attempt, wait_fixed

from blog_autopilot.config import WordPressSettings
from blog_autopilot.exceptions import WordPressError

logger = logging.getLogger("blog-autopilot")


# ── HTML 清洗 ──

# 危险标签（直接移除整个标签及内容）
# 注意：闭合标签允许空白 </script > 以防绕过
_DANGEROUS_TAGS_RE = _re.compile(
    r"<(script|iframe|object|embed|form|input|textarea|button|select|link|meta|base)"
    r"[\s>].*?</\1\s*>|"
    r"<(script|iframe|object|embed|form|input|textarea|button|select|link|meta|base)"
    r"[^>]*/?>",
    _re.IGNORECASE | _re.DOTALL,
)

# 未闭合的危险标签（移除标签本身，防止浏览器解析执行）
_UNCLOSED_DANGEROUS_RE = _re.compile(
    r"<(script|iframe|object|embed)[^>]*>",
    _re.IGNORECASE,
)

# 事件属性（on* 属性）
_EVENT_ATTR_RE = _re.compile(
    r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
    _re.IGNORECASE,
)

# javascript: 协议
_JS_PROTOCOL_RE = _re.compile(
    r'(href|src|action)\s*=\s*["\']?\s*javascript:',
    _re.IGNORECASE,
)

# data: 协议（除了 data:image）
_DATA_PROTOCOL_RE = _re.compile(
    r'(href|src|action)\s*=\s*["\']?\s*data:(?!image/)',
    _re.IGNORECASE,
)


def sanitize_html(html: str) -> str:
    """
    清洗 AI 生成的 HTML，移除潜在的 XSS/注入内容。

    - 移除 script/iframe/object/embed/form 等危险标签
    - 移除 on* 事件属性
    - 移除 javascript: 和非图片 data: 协议
    """
    if not html:
        return html

    original_len = len(html)

    # 1. 移除危险标签（含内容）
    html = _DANGEROUS_TAGS_RE.sub("", html)

    # 1.5 移除残留的未闭合危险标签
    html = _UNCLOSED_DANGEROUS_RE.sub("", html)

    # 2. 移除事件属性
    html = _EVENT_ATTR_RE.sub("", html)

    # 3. 移除 javascript: 协议
    html = _JS_PROTOCOL_RE.sub(r'\1=""', html)

    # 4. 移除非图片 data: 协议
    html = _DATA_PROTOCOL_RE.sub(r'\1=""', html)

    if len(html) != original_len:
        logger.warning(
            f"HTML 清洗移除了 {original_len - len(html)} 字符的潜在危险内容"
        )

    return html


@dataclass(frozen=True)
class PublishResult:
    """WordPress 发布结果"""
    url: str
    post_id: int


def _is_server_error(result: str | None) -> bool:
    """tenacity 重试条件：仅当返回 None（即 5xx 失败）时重试"""
    return result is None


def _is_retryable_wp_error(exc: BaseException) -> bool:
    """tenacity 重试条件：仅当 WordPressError.retryable=True 时重试"""
    return isinstance(exc, WordPressError) and exc.retryable


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
    retry=retry_if_exception(_is_retryable_wp_error),
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
    content = sanitize_html(content)
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
        # 5xx 抛出可重试异常
        if status_code >= 500:
            logger.warning(f"WordPress 服务器错误 ({status_code}), 将重试...")
            raise WordPressError(
                f"WordPress 服务器错误 ({status_code})",
                status_code=status_code,
                retryable=True,
            ) from e
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
    if not post_id or not post_link:
        raise WordPressError(
            f"WordPress 返回数据不完整: id={post_id}, link={post_link}"
        )
    logger.info(f"博客发布成功! ID: {post_id} | URL: {post_link}")
    return PublishResult(url=post_link, post_id=int(post_id))


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
