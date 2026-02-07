"""
📦 全局配置文件
所有密钥、路径、模型参数集中管理
"""

import os
import logging

# ==================== 📁 文件夹路径 ====================
INPUT_FOLDER = "./input"
PROCESSED_FOLDER = "./processed"

# ==================== 🌐 WordPress 配置 ====================
WP_URL = "https://wo.city/index.php?rest_route=/wp/v2/posts"
WP_USER = os.environ.get("WP_USER", "rootad")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "Tund fCYo dc3o cjXK 8PvW abEX")
WP_TARGET_CATEGORY_ID = 15  # 🔴 在WP后台查看分类ID

# ==================== ✈️ Telegram 配置 ====================
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8504811149:AAELbMB9KKeYmyjdY4XiaR7d1afE2g2ZsnY")
TG_CHANNEL_ID = os.environ.get("TG_CHANNEL_ID", "@gooddayupday")

# ==================== 🤖 AI 模型配置 ====================
AI_API_KEY = "sk-4L2iIeDdRXeIOMP44PzLzvt3803m8F2xIMCFJh4C4B3Aa8OV"  # 你的第三方 Key
AI_API_BASE = "https://api.ikuncode.cc/v1" # 填入第三方提供的 Base URL

# 高质量文章生成 → Opus (强推理、深度写作)
AI_MODEL_WRITER = "claude-opus-4-5-20251101"
AI_WRITER_MAX_TOKENS = 200000  # Opus 输出上限可以给高一些

# 新增：自定义 Headers
AI_DEFAULT_HEADERS = {
    # "Content-Type": "application/json", # SDK 默认会带，一般不用写
    # "x-custom-header": "custom-value",  # 如果有特殊需求在这里加
    "User-Agent": "MyBlogWriter/1.0"
}

# 摘要 / 推广文案生成 → Haiku (快速、低成本)
AI_MODEL_PROMO = "claude-haiku-4-5-20251001"
AI_PROMO_MAX_TOKENS = 10000

# ==================== 📝 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("blog-autopilot")

