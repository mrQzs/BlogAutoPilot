"""Telegram 推送模块"""

import logging

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from blog_autopilot.config import TelegramSettings
from blog_autopilot.exceptions import TelegramError

logger = logging.getLogger("blog-autopilot")


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(3),
    reraise=True,
)
def send_to_telegram(
    promo_text: str,
    link: str,
    settings: TelegramSettings,
) -> bool:
    """
    推送消息到 Telegram 频道。

    抛出 TelegramError 当推送失败时。
    """
    logger.info("正在推送到 Telegram...")

    if not promo_text:
        promo_text = "新文章发布！"

    promo_text = promo_text.replace(
        "# 📌 Telegram 频道推广文案", ""
    ).strip()

    msg = f"{promo_text}\n\n👉 **阅读全文**: {link}"

    token = settings.bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": settings.channel_id,
        "text": msg,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
    except Exception as e:
        raise TelegramError(f"Telegram 推送异常: {e}") from e

    if data.get("ok"):
        logger.info("Telegram 推送成功!")
        return True

    raise TelegramError(
        f"Telegram 推送失败: {data.get('description', '未知错误')}"
    )


def test_tg_connection(settings: TelegramSettings) -> bool:
    """测试 Telegram Bot 连接"""
    logger.info("测试 Telegram Bot 连接...")

    token = settings.bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{token}/getMe"

    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()

        if data.get("ok"):
            bot_name = data["result"].get("username", "unknown")
            logger.info(f"Telegram Bot 连接成功: @{bot_name}")
            return True
        else:
            logger.error(f"Bot Token 无效: {data.get('description')}")
            return False
    except Exception as e:
        logger.error(f"连接测试失败: {e}")
        return False
