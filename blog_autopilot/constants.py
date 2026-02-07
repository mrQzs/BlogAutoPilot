"""命名常量，消除魔法数字"""

import re

# 允许的大类列表
ALLOWED_CATEGORIES = ("Articles", "Books", "Magazine", "News")

# 子类目录命名正则：子类名_数字
SUBCATEGORY_DIR_PATTERN = re.compile(r"^(.+)_(\d+)$")

# AI Writer 输入文本截取上限
AI_WRITER_INPUT_LIMIT = 80000

# AI 推广文案预览截取上限
AI_PROMO_PREVIEW_LIMIT = 3000

# 提取文本最小有效长度
MIN_EXTRACTED_TEXT_LENGTH = 50

# 归档文件名最大长度
MAX_FILENAME_LENGTH = 100

# 监控间隔（秒）
POLL_INTERVAL = 600

# WordPress 默认分类 ID
DEFAULT_WP_CATEGORY_ID = 15
