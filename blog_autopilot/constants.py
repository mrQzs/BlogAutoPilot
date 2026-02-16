"""命名常量，消除魔法数字"""

import re

# 允许的大类列表
ALLOWED_CATEGORIES = ("Articles", "Books", "Magazine", "News", "Paper")

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
POLL_INTERVAL = 60

# WordPress 默认分类 ID
DEFAULT_WP_CATEGORY_ID = 15

# ── 文章关联系统常量 ──

# Embedding 向量维度
EMBEDDING_DIMENSIONS = 3072

# 默认 Embedding 模型
EMBEDDING_MODEL = "text-embedding-3-large"

# 标签匹配最低阈值（低于此值的候选文章被过滤）
TAG_MATCH_THRESHOLD = 2

# 关联查询返回数量
ASSOCIATION_TOP_K = 5

# TG 推广文案长度范围
TG_PROMO_MIN_LENGTH = 150
TG_PROMO_MAX_LENGTH = 250

# 标签长度限制
TAG_MAX_LENGTH = 50
TAG_CONTENT_MAX_LENGTH = 100

# Embedding 批量处理每批大小
EMBEDDING_BATCH_SIZE = 100

# Embedding 缓存容量
EMBEDDING_CACHE_SIZE = 1000

# 关联强度分类
RELATION_STRONG = "强关联"
RELATION_MEDIUM = "中关联"
RELATION_WEAK = "弱关联"

# 内容去重相似度阈值（高于此值视为重复文章）
DUPLICATE_SIMILARITY_THRESHOLD = 0.95

# ── SEO 元数据常量 ──
SEO_META_DESC_MIN_LENGTH = 120
SEO_META_DESC_MAX_LENGTH = 160
SEO_SLUG_MAX_LENGTH = 75
SEO_WP_TAGS_MIN_COUNT = 3
SEO_WP_TAGS_MAX_COUNT = 8
SEO_WP_TAG_MAX_LENGTH = 30
SEO_INPUT_PREVIEW_LIMIT = 5000

# ── 封面图常量 ──
COVER_IMAGE_INPUT_LIMIT = 2000

# ── 质量审核常量 ──
QUALITY_WEIGHT_CONSISTENCY = 0.35
QUALITY_WEIGHT_READABILITY = 0.30
QUALITY_WEIGHT_AI_CLICHE = 0.35
QUALITY_PASS_THRESHOLD = 7
QUALITY_REWRITE_THRESHOLD = 5
QUALITY_MAX_REWRITE_ATTEMPTS = 2
QUALITY_INPUT_PREVIEW_LIMIT = 5000
QUALITY_REQUIRED_FIELDS = (
    "consistency", "readability", "ai_cliche", "issues", "summary",
)

# ── 主题推荐常量 ──
RECOMMEND_DEFAULT_TOP_N = 5
RECOMMEND_TAG_GAP_WEIGHT = 0.6
RECOMMEND_VECTOR_GAP_WEIGHT = 0.4
RECOMMEND_RECENCY_CAP = 3.0           # 时间衰减上限倍数
RECOMMEND_SPARSE_THRESHOLD = 0.7      # nn_similarity 低于此值视为稀疏
RECOMMEND_FRONTIER_MULTIPLIER = 2     # frontier 查询数 = top_n * multiplier
RECOMMEND_MIN_ARTICLES = 10           # 最少文章数
RECOMMEND_RECENT_TITLES_COUNT = 20    # 传给 AI 的最近标题数

# ── 文章系列检测常量 ──
SERIES_SIMILARITY_THRESHOLD = 0.80
SERIES_TITLE_PATTERN_THRESHOLD = 0.70
SERIES_NEW_THRESHOLD = 0.85
SERIES_LOOKBACK_DAYS = 30
SERIES_NAV_CSS_CLASS = "blog-series-nav"
SERIES_TITLE_PATTERNS = (
    r"[Pp]art\s*\d+",
    r"第.{1,3}[部篇章节]",
    r"[（(][上中下][）)]",
    r"系列|连载|[Ss]eries",
    r"[（(]\d+[）)]$",
)
