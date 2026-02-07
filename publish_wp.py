"""
🚀 WordPress 发布模块
可独立运行测试: python publish_wp.py "测试标题" "<p>测试内容</p>"
"""

import base64
import requests
from config import (
    WP_URL, WP_USER, WP_APP_PASSWORD, WP_TARGET_CATEGORY_ID, logger
)


def post_to_wordpress(title: str, content: str, status: str = "publish",
                      category_id: int | None = None) -> str | None:
    """
    发布文章到 WordPress

    参数:
        title: 文章标题
        content: HTML 正文
        status: 发布状态 ("publish" | "draft" | "pending")
        category_id: WordPress 分类 ID，None 时使用配置文件中的默认值
    返回:
        文章链接 URL, 失败返回 None
    """
    logger.info(f"🚀 正在发布到博客: 《{title}》 (状态: {status}, 分类ID: {category_id or WP_TARGET_CATEGORY_ID})")

    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode('utf-8')

    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "title": title,
        "content": content,
        "status": status,
        "categories": [category_id if category_id else WP_TARGET_CATEGORY_ID]
    }

    try:
        resp = requests.post(WP_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        post_id = data.get('id')
        post_link = data.get('link')
        logger.info(f"✅ 博客发布成功! ID: {post_id} | URL: {post_link}")
        return post_link

    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ 博客发布失败 (HTTP {e.response.status_code}): {e}")
        logger.error(f"   服务器返回: {e.response.text[:500]}")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("❌ 无法连接到 WordPress, 请检查 WP_URL")
        return None
    except Exception as e:
        logger.error(f"❌ 博客发布异常: {e}")
        return None


def test_wp_connection() -> bool:
    """测试 WordPress 连接和认证"""
    logger.info("🔍 测试 WordPress 连接...")

    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode('utf-8')

    headers = {"Authorization": f"Basic {token}"}

    try:
        # 修改点：使用 params 参数，让 requests 自动处理 ? 或 &
        params = {"per_page": 1} 
        
        # 此时 requests 会自动识别 WP_URL 里是否有问号，并正确拼接
        resp = requests.get(WP_URL, headers=headers, params=params, timeout=10)

        if resp.status_code == 200:
            logger.info("✅ WordPress 连接成功, 认证有效")
            return True
        elif resp.status_code == 401:
            logger.error("❌ WordPress 认证失败, 请检查用户名和应用密码")
            return False
        else:
            logger.warning(f"⚠️ WordPress 返回状态码: {resp.status_code}")
            return False

    except Exception as e:
        logger.error(f"❌ 连接测试失败: {e}")
        return False


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        # 模式1: 直接发布测试
        title = sys.argv[1]
        content = sys.argv[2]
        status = sys.argv[3] if len(sys.argv) > 3 else "draft"  # 默认草稿

        print(f"📝 准备发布测试文章 (状态: {status})")
        link = post_to_wordpress(title, content, status=status)
        if link:
            print(f"🎉 发布成功: {link}")
        else:
            print("💥 发布失败")
    else:
        # 模式2: 仅测试连接
        print("用法: python publish_wp.py <标题> <HTML内容> [draft|publish]")
        print("无参数时仅测试连接...\n")
        test_wp_connection()
