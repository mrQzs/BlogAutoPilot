# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Blog Autopilot** — Python 自动化博客发布系统。监控 `input/` 目录中的文件，通过 AI 生成博客文章，自动发布到 WordPress 并推送 Telegram 推广文案。支持基于 PostgreSQL + pgvector 的文章关联推荐和内容去重。

## Architecture

处理流水线（`blog_autopilot/pipeline.py` 中的 `Pipeline.process_file()`）：

```
文件放入 input/大类/子类_分类ID/
  → 文件锁（fcntl.flock，防止多进程并发处理同一文件）
  → extractor 提取文本
  → AIWriter 提取标签 + embedding 生成（若 DB 可用）
  → 标签同义词归一化（tag_normalizer，基于 tag_synonyms.json 映射）
  → 内容去重检查（若 DB 可用，相似度阈值 0.95）
  → 查找关联文章（若 DB 可用，标签过滤 + 向量排序）
  → 系列检测（若 DB 可用，标签匹配 + 向量相似度 + LLM 辅助判断）
  → AIWriter 生成博客文章（支持带上下文/不带上下文两种模式，分类动态 temperature）
  → 质量审核（三维度评分 → pass/rewrite/draft，分类自适应阈值，审核结果入库）
  → SEO 元数据提取（meta description / slug / wp_tags，含搜索意图分类）
  → 封面图生成 + 上传（可选）
  → 注入系列导航 HTML（若检测到系列，html.escape 防 XSS）
  → HTML 安全清洗（sanitize_html，移除 script/iframe/事件属性/javascript: 协议）
  → 发布时段检查（可选，支持跨午夜窗口）
  → publisher 发布到 WordPress (按目录中的分类 ID，返回 PublishResult)
  → 回溯更新上一篇文章的系列导航（若有系列）
  → 文章入库（若 DB 可用，含 series_id/series_order/wp_post_id；失败时保存到 failed_ingests/ 供重试）
  → AIWriter 生成推广文案
  → telegram 推送到 Telegram 频道
  → Token 用量汇总日志
  → 归档到 processed/
```

### 包结构

```
blog_autopilot/
├── __init__.py        # 版本号 (2.0.0)
├── __main__.py        # python -m blog_autopilot 入口
├── config.py          # Pydantic BaseSettings（从 .env 读取凭据，含 field_validator 校验）
├── models.py          # dataclass 数据模型（含 TokenUsage/TokenUsageSummary）
├── exceptions.py      # 自定义异常层级（WordPressError 含 retryable 标记）
├── constants.py       # 命名常量（含分类质量阈值、分类 temperature）
├── pipeline.py        # Pipeline 类（主流水线编排，文件锁，发布时段，失败入库重试）
├── scanner.py         # 目录扫描 + 路径解析（从 categories.json 加载分类）
├── ai_writer.py       # AIWriter 类（延迟初始化，模型回退，Token 追踪，标签提取，质量审核）
├── publisher.py       # WordPress REST API 发布 + HTML 安全清洗（sanitize_html）
├── telegram.py        # Telegram Bot API 推送
├── extractor.py       # 文本提取（PDF/MD/TXT）
├── db.py              # PostgreSQL + pgvector 数据库管理（含审核日志表 article_reviews）
├── embedding.py       # OpenAI Embedding API 客户端（LRU 缓存）
├── ingest.py          # 文章入库工作流（单文件/目录扫描）
├── recommender.py     # 智能选题推荐（标签缺口 + 向量稀疏分析）
├── series.py          # 文章系列检测（向量 + LLM 辅助）+ 导航 HTML 生成 + 回溯更新
├── tag_normalizer.py  # 标签同义词归一化（基于 tag_synonyms.json）
└── prompts/           # 提示词模板
    ├── writer_system.txt          # 通用写作系统提示
    ├── writer_system_{category}.txt  # 分类专属写作提示（5 个分类）
    ├── writer_user.txt
    ├── writer_context_system.txt  # 带上下文的写作系统提示（含链接密度控制）
    ├── writer_context_system_{category}.txt  # 分类专属上下文提示
    ├── writer_context_user.txt
    ├── promo_system.txt
    ├── promo_user.txt
    ├── tagger_system.txt          # 标签提取系统提示（含正反示例）
    ├── tagger_user.txt
    ├── seo_system.txt             # SEO 元数据提取提示（含搜索意图分类）
    ├── seo_user.txt
    ├── review_system.txt          # 质量审核系统提示（含评分示例）
    ├── review_user.txt
    ├── rewrite_feedback_user.txt  # 审核反馈重写提示
    ├── recommend_system.txt       # 选题推荐系统提示
    ├── recommend_user.txt         # 选题推荐用户提示
    ├── series_check_system.txt    # LLM 系列判断系统提示
    └── series_check_user.txt      # LLM 系列判断用户提示
```

### 关键设计

- **目录即配置**：文件路径 `input/大类/子类_数字/` 决定 WordPress 分类 ID 和 Telegram hashtag
- **分类配置**：`categories.json` 定义分类结构和 Telegram Bot 配置，scanner 优先从此文件加载
- **允许的大类**：`Articles`, `Books`, `Magazine`, `News`, `Paper`（定义在 `constants.py`）
- **凭据管理**：所有敏感信息在 `.env` 文件中，通过 Pydantic BaseSettings 加载，SecretStr 保护
- **配置校验**：`@field_validator` 校验 URL 格式（http/https）、端口范围（1-65535）、非空字符串、小时范围（0-23）等
- **延迟初始化**：AIWriter 的 OpenAI client 在首次调用时才创建
- **重试机制**：AI API (3次指数退避，认证错误不重试)、WordPress (2次固定5s，仅 retryable 异常)、Telegram (2次固定3s)
- **模型回退**：主模型 3 次失败后自动切换备用模型（`AI_MODEL_WRITER_FALLBACK` / `AI_MODEL_PROMO_FALLBACK`），reviewer 回退到 promo fallback
- **自定义异常**：BlogAutoPilotError 基类，各模块有专属异常类型（含 DatabaseError、EmbeddingError、TagExtractionError、QualityReviewError、RecommendationError、SeriesDetectionError）；WordPressError 含 `retryable` 标记和 `status_code`
- **文章关联系统**（可选，依赖 DB）：四级标签体系（magazine/science/topic/content）+ 向量相似度搜索，两阶段检索（标签过滤 + embedding 排序）
- **内容去重**：基于 embedding 相似度检测（阈值 0.95），防止重复发布
- **封面图生成**（可选，默认启用）：基于文章标题生成抽象风格封面图（仅传标题给 DALL-E，避免原文内容触发安全过滤），上传到 WordPress 媒体库作为特色图片；失败不阻断发布
- **标签同义词归一化**：`tag_normalizer.py` 基于 `tag_synonyms.json` 映射表，在标签提取后自动归一化（如 `AI应用` → `人工智能应用`），懒加载
- **质量审核系统**（可选，默认启用）：三维度评分（consistency/readability/ai_cliche），加权综合分；分类自适应阈值（News 放宽 6/4，Paper/Books 收紧 8/6）；自动重写最多 2 次，失败存草稿；审核结果入库 `article_reviews` 表；审核异常降级发布
- **分类专属提示词**：每个大类有独立的写作系统提示，支持带上下文和不带上下文两种模式；提示词含正反示例（tagger/review/seo）
- **分类动态 temperature**：News 0.4（准确性优先）、Paper 0.5、Articles 0.7、Books/Magazine 0.8（创意优先），定义在 `constants.py` 的 `CATEGORY_TEMPERATURE`
- **Token 用量追踪**：每次 API 调用记录 prompt/completion/total tokens，每文件处理完输出汇总日志，`TokenUsage` / `TokenUsageSummary` dataclass
- **HTML 安全清洗**：`publisher.py` 的 `sanitize_html()` 在发布前移除 script/iframe/object/embed 等危险标签、on* 事件属性、javascript:/data: 协议；支持闭合标签空格绕过防护和未闭合标签清理
- **智能选题推荐**（可选，依赖 DB）：分析标签分布缺口 + 向量空间稀疏区域，AI 生成具体选题建议，避免内容同质化
- **文章系列检测**（可选，依赖 DB）：自动检测系列文章（标签匹配 + 向量相似度 + LLM 辅助判断），注入系列导航 HTML（上下篇链接，html.escape 防 XSS），回溯更新已发布文章的导航；标题模式识别（Part N / 第X篇 / 上中下 / 系列）放宽阈值；`article_series` 表存储系列元数据，`articles` 表新增 `series_id`/`series_order`/`wp_post_id` 列
- **文件锁**：`fcntl.flock(LOCK_EX | LOCK_NB)` 防止多进程同时处理同一文件
- **发布时段调度**（可选）：`ScheduleSettings` 配置发布窗口（如 8:00-22:00），支持跨午夜窗口（如 22:00-06:00），非窗口期跳过处理
- **失败入库重试**：入库失败时保存到 `failed_ingests/` 目录（JSON），下次启动时自动重试；损坏文件和无标签记录自动清理
- **数据库事务安全**：`insert_article()` 使用 `get_connection()` 包装事务，embedding 插入失败时整体回滚
- `file_bot.py` 保留在根目录，是独立的 Telegram 文件接收进程

## Commands

```bash
# 安装依赖
pip install -e ".[dev]"

# 单次处理 input 目录中的所有文件
python -m blog_autopilot --once

# 持续监控模式（每 60 秒扫描一次）
python -m blog_autopilot

# 测试 WordPress、Telegram、DB 连接
python -m blog_autopilot --test

# 仅测试数据库连接
python -m blog_autopilot --test-db

# 初始化数据库 schema
python -m blog_autopilot --init-db

# 入库文件或目录（可选 --ingest-url 指定来源 URL）
python -m blog_autopilot --ingest <path> [--ingest-url <url>]

# 智能选题推荐（默认推荐 5 个选题）
python -m blog_autopilot --recommend

# 指定推荐数量
python -m blog_autopilot --recommend --top 3

# 运行测试
pytest tests/ -v
```

## Dependencies

定义在 `pyproject.toml`：
- `openai` — AI API 调用（OpenAI 兼容模式，含 Embedding）
- `requests` — WordPress REST API 和 Telegram Bot API
- `pypdf` — PDF 文本提取
- `pydantic-settings` — 配置管理
- `tenacity` — 重试机制
- `python-dotenv` — .env 文件加载
- `psycopg2-binary` — PostgreSQL 数据库驱动
- `pgvector` — 向量相似度搜索扩展
- `pytest` / `pytest-mock` — 开发依赖

## Configuration

所有配置通过 `.env` 文件管理（参考 `.env.example`）：
- `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` — WordPress 认证（URL 必须 http/https 开头，user 不能为空）
- `TG_BOT_TOKEN` / `TG_CHANNEL_ID` — Telegram Bot
- `AI_API_KEY` / `AI_API_BASE` / `AI_MODEL_WRITER` / `AI_MODEL_PROMO` — AI API 端点和模型
- `AI_MODEL_WRITER_FALLBACK` / `AI_MODEL_PROMO_FALLBACK` — 备用模型（可选，主模型失败时自动切换）
- `AI_QUALITY_REVIEW_ENABLED` / `AI_MODEL_REVIEWER` / `AI_REVIEWER_MAX_TOKENS` — 质量审核（可选，默认启用）
- `AI_COVER_IMAGE_ENABLED` / `AI_MODEL_COVER_IMAGE` / `AI_COVER_IMAGE_API_KEY` / `AI_COVER_IMAGE_API_BASE` — 封面图生成（可选，默认启用，仅基于标题生成）
- `DB_URL` 或 `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` — PostgreSQL 数据库（可选，端口校验 1-65535）
- `EMBEDDING_API_KEY` / `EMBEDDING_API_BASE` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` — Embedding API（可选，dimensions 必须正整数）
- `SCHEDULE_PUBLISH_WINDOW_ENABLED` / `SCHEDULE_PUBLISH_WINDOW_START` / `SCHEDULE_PUBLISH_WINDOW_END` — 发布时段调度（可选，小时 0-23）

分类结构通过根目录 `categories.json` 配置，定义各大类的子分类 ID 和 Telegram Bot Token。
标签同义词通过根目录 `tag_synonyms.json` 配置，定义标签归一化映射。

## Important Notes

- `file_bot.py` 是独立的 Telegram 文件接收进程，与主流水线分开运行，保留在根目录
- `.env` 文件包含实际凭据，已在 `.gitignore` 中排除
- WordPress 发布失败时，草稿保存到 `./drafts/` 目录
- 质量审核未通过（verdict=draft 或重写次数用尽）时，草稿也保存到 `./drafts/` 目录
- 文章入库失败时，记录保存到 `./failed_ingests/` 目录，下次启动自动重试
- 数据库功能为可选，未配置时流水线自动跳过关联/去重步骤
- 监控间隔为 60 秒（`POLL_INTERVAL` 定义在 `constants.py`）
- 配置校验在启动时执行，URL 格式、端口范围、必填字段等错误会立即报错
- HTML 清洗在每次发布前自动执行，无需手动调用
- Token 用量按文件粒度追踪，每文件处理完重置计数器
