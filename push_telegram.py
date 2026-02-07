"""
✈️ Telegram 推送模块
可独立运行测试: python push_telegram.py "测试消息内容" "https://example.com"
"""

import requests
from config import TG_BOT_TOKEN, TG_CHANNEL_ID, logger


def send_to_telegram(promo_text: str, link: str) -> bool:
    # ... (前面的代码不变) ...
    logger.info("✈️ 正在推送到 Telegram...")

    if not promo_text:
        promo_text = "📢 新文章发布！"

    # 👇👇👇 在这里加上这一句 👇👇👇
    promo_text = promo_text.replace("# 📌 Telegram 频道推广文案", "").strip()

    msg = f"{promo_text}\n\n👉 **阅读全文**: {link}"

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHANNEL_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if data.get("ok"):
            logger.info("✅ Telegram 推送成功!")
            return True
        else:
            logger.error(f"❌ Telegram 推送失败: {data.get('description', '未知错误')}")
            return False

    except Exception as e:
        logger.error(f"❌ Telegram 推送异常: {e}")
        return False


def test_tg_connection() -> bool:
    """测试 Telegram Bot 连接"""
    logger.info("🔍 测试 Telegram Bot 连接...")

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getMe"

    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()

        if data.get("ok"):
            bot_name = data["result"].get("username", "unknown")
            logger.info(f"✅ Telegram Bot 连接成功: @{bot_name}")
            return True
        else:
            logger.error(f"❌ Bot Token 无效: {data.get('description')}")
            return False

    except Exception as e:
        logger.error(f"❌ 连接测试失败: {e}")
        return False


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        text = sys.argv[1]
        link = sys.argv[2]
        print(f"📤 发送测试消息到 Telegram...")
        send_to_telegram(text, link)
    else:
        print("用法: python push_telegram.py <推广文案> <文章链接>")
        print("无参数时仅测试 Bot 连接...\n")
        test_tg_connection()
