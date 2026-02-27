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

# 监控间隔（秒）
POLL_INTERVAL = 60

# ── 文章关联系统常量 ──

# 标签匹配最低阈值（低于此值的候选文章被过滤）
TAG_MATCH_THRESHOLD = 2

# 关联查询返回数量
ASSOCIATION_TOP_K = 5

# 关联文章时间衰减
ASSOCIATION_RECENCY_WEIGHT = 0.5        # 时间衰减最大加成
ASSOCIATION_RECENCY_WINDOW_DAYS = 180   # 衰减窗口（天）

# TG 推广文案长度范围
TG_PROMO_MIN_LENGTH = 150
TG_PROMO_MAX_LENGTH = 250

# 标签长度限制
TAG_MAX_LENGTH = 50
TAG_CONTENT_MAX_LENGTH = 100

# 标签注册表模糊匹配阈值
TAG_REGISTRY_FUZZY_THRESHOLD = 0.6

# Embedding 缓存容量
EMBEDDING_CACHE_SIZE = 1000

# 关联强度分类
RELATION_STRONG = "强关联"
RELATION_MEDIUM = "中关联"
RELATION_WEAK = "弱关联"

# 内容去重相似度阈值（高于此值视为重复文章）
DUPLICATE_SIMILARITY_THRESHOLD = 0.95

# 标题相似度去重警告阈值（Level 3: 标题相似 + 标签完全匹配）
TITLE_SIMILARITY_THRESHOLD = 0.85

# 关联文章正文摘录上限（字符数）
CONTENT_EXCERPT_MAX_LENGTH = 500

# ── SEO 元数据常量 ──
SEO_META_DESC_MIN_LENGTH = 120
SEO_META_DESC_MAX_LENGTH = 160
SEO_SLUG_MAX_LENGTH = 75
SEO_WP_TAGS_MIN_COUNT = 3
SEO_WP_TAGS_MAX_COUNT = 8
SEO_WP_TAG_MAX_LENGTH = 30
SEO_INPUT_PREVIEW_LIMIT = 5000

# ── 质量审核常量（4 维度） ──
QUALITY_WEIGHT_CONSISTENCY = 0.25
QUALITY_WEIGHT_FACTUALITY = 0.20
QUALITY_WEIGHT_READABILITY = 0.25
QUALITY_WEIGHT_AI_CLICHE = 0.30
QUALITY_PASS_THRESHOLD = 7
QUALITY_REWRITE_THRESHOLD = 5
QUALITY_MAX_REWRITE_ATTEMPTS = 2
QUALITY_INPUT_PREVIEW_LIMIT = 5000
QUALITY_REQUIRED_FIELDS = (
    "consistency", "factuality", "readability", "ai_cliche", "issues", "summary",
)

# ── 分类专属质量阈值 ──
# 格式: {category_name: (pass_threshold, rewrite_threshold)}
# 未配置的分类使用默认值 (QUALITY_PASS_THRESHOLD, QUALITY_REWRITE_THRESHOLD)
CATEGORY_QUALITY_THRESHOLDS: dict[str, tuple[int, int]] = {
    "News": (6, 4),       # 新闻时效性优先，适当放宽
    "Paper": (8, 6),      # 论文类要求更严格
    "Books": (8, 6),      # 书评类要求更严格
    "Articles": (7, 5),   # 默认标准
    "Magazine": (7, 5),   # 默认标准
}

# ── 分类专属 AI temperature ──
# 未配置的分类使用默认值 0.7
CATEGORY_TEMPERATURE: dict[str, float] = {
    "News": 0.4,          # 新闻准确性优先
    "Paper": 0.5,         # 论文严谨性优先
    "Books": 0.8,         # 书评创意优先
    "Articles": 0.7,      # 默认
    "Magazine": 0.8,      # 杂志创意优先
}

DEFAULT_TEMPERATURE = 0.7

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
# ── 自审偏差检测常量 ──
SELF_REVIEW_THRESHOLD_ADJUSTMENT = 1  # writer == reviewer 时 pass/rewrite 阈值上调值

# ── 审核校准膨胀警告阈值 ──
REVIEW_INFLATION_WARNING_THRESHOLD = 8.0  # avg_overall >= 此值时发出膨胀警告

# ── 套话自动刷新间隔 ──
CLICHE_AUTO_REFRESH_HOURS = 168  # 7 天

# ── 审核反馈学习常量 ──
REVIEW_CALIBRATION_SAMPLE_SIZE = 50    # 校准统计采样量
REVIEW_EXEMPLAR_MIN_SCORE = 8         # 高质量示例最低综合分
REVIEW_EXEMPLAR_COUNT = 3             # 注入写作提示词的示例数量

# ── 套话动态检测库常量 ──
CLICHE_MIN_REVIEWS = 5                # 最少审核记录数
CLICHE_MIN_FREQUENCY = 2             # 套话最低出现次数（低于此值不入库）
CLICHE_MAX_PHRASES = 50              # 套话库最大条目数
CLICHE_INJECT_LIMIT = 30             # 注入提示词的最大套话数

# ── 标签一致性检查常量 ──
TAG_CONSISTENCY_NEIGHBORS = 5
TAG_CONSISTENCY_WARN_THRESHOLD = 0.25

# ── 标签治理审计常量 ──
TAG_AUDIT_MIN_ARTICLES = 5
TAG_AUDIT_SIMILARITY_THRESHOLD = 0.85
TAG_AUDIT_TOP_COOCCURRENCES = 20
TAG_AUDIT_MIN_TAG_COUNT = 3  # 模糊分组合并计数后，组总数 < 3 的不生成建议

# ── 摘要生成常量 ──
SUMMARY_INPUT_PREVIEW_LIMIT = 3000  # 摘要生成时截取的正文长度

# ── 封面图分类风格 ──
CATEGORY_COVER_STYLE: dict[str, str] = {
    "News": "journalistic, bold, newspaper-inspired layout, strong contrast",
    "Paper": "scientific, data visualization, clean academic, structured",
    "Books": "literary, warm tones, reading atmosphere, soft lighting",
    "Articles": "modern tech, gradient, geometric shapes, digital feel",
    "Magazine": "editorial, vibrant, magazine cover inspired, dynamic composition",
}
DEFAULT_COVER_STYLE = (
    "modern, clean, minimalist with vibrant colors, "
    "abstract shapes, gradients, or symbolic imagery"
)

SERIES_TITLE_PATTERNS = (
    r"[Pp]art\s*\d+",
    r"第.{1,3}[部篇章节]",
    r"[（(][上中下][）)]",
    r"系列|连载|[Ss]eries",
    r"[（(]\d+[）)]$",
)

# ── 综述文章生成常量 ──
SURVEY_MIN_ARTICLES = 2           # 触发综述的最少文章数
SURVEY_LOOKBACK_DAYS = 90         # 候选文章回溯天数
SURVEY_MAX_SOURCE_ARTICLES = 8    # 综述最多引用的源文章数
SURVEY_CHECK_INTERVAL = 24 * 3600  # 综述检查间隔（秒），默认 24 小时
SURVEY_TOPIC_SIMILARITY = 0.80    # topic 模糊分组相似度阈值
SURVEY_SCIENCE_SIMILARITY = 0.75  # science 模糊分组相似度阈值（短文本需更宽松）
