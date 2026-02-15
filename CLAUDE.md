# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Blog Autopilot** — Python 自动化博客发布系统。监控 `input/` 目录中的文件，通过 AI 生成博客文章，自动发布到 WordPress 并推送 Telegram 推广文案。支持基于 PostgreSQL + pgvector 的文章关联推荐和内容去重。

## Architecture

处理流水线（`blog_autopilot/pipeline.py` 中的 `Pipeline.process_file()`）：

```
文件放入 input/大类/子类_分类ID/
  → extractor 提取文本
  → AIWriter 提取标签 + embedding 生成（若 DB 可用）
  → 内容去重检查（若 DB 可用，相似度阈值 0.95）
  → 查找关联文章（若 DB 可用，标签过滤 + 向量排序）
  → AIWriter 生成博客文章（支持带上下文/不带上下文两种模式）
  → 质量审核（三维度评分 → pass/rewrite/draft，可选）
  → SEO 元数据提取（meta description / slug / wp_tags）
  → 封面图生成 + 上传（可选）
  → publisher 发布到 WordPress (按目录中的分类 ID)
  → 文章入库（若 DB 可用）
  → AIWriter 生成推广文案
  → telegram 推送到 Telegram 频道
  → 归档到 processed/
```

### 包结构

```
blog_autopilot/
├── __init__.py        # 版本号 (2.0.0)
├── __main__.py        # python -m blog_autopilot 入口
├── config.py          # Pydantic BaseSettings（从 .env 读取凭据）
├── models.py          # dataclass 数据模型
├── exceptions.py      # 自定义异常层级
├── constants.py       # 命名常量
├── pipeline.py        # Pipeline 类（主流水线编排）
├── scanner.py         # 目录扫描 + 路径解析（从 categories.json 加载分类）
├── ai_writer.py       # AIWriter 类（延迟初始化，依赖注入，标签提取，质量审核）
├── publisher.py       # WordPress REST API 发布
├── telegram.py        # Telegram Bot API 推送
├── extractor.py       # 文本提取（PDF/MD/TXT）
├── db.py              # PostgreSQL + pgvector 数据库管理
├── embedding.py       # OpenAI Embedding API 客户端（LRU 缓存）
├── ingest.py          # 文章入库工作流（批量/单文件）
└── prompts/           # 提示词模板
    ├── writer_system.txt          # 通用写作系统提示
    ├── writer_system_{category}.txt  # 分类专属写作提示（5 个分类）
    ├── writer_user.txt
    ├── writer_context_system.txt  # 带上下文的写作系统提示
    ├── writer_context_system_{category}.txt  # 分类专属上下文提示
    ├── writer_context_user.txt
    ├── promo_system.txt
    ├── promo_user.txt
    ├── tagger_system.txt          # 标签提取系统提示
    ├── tagger_user.txt
    ├── seo_system.txt             # SEO 元数据提取提示
    ├── seo_user.txt
    ├── review_system.txt          # 质量审核系统提示（三维度评分）
    ├── review_user.txt
    └── rewrite_feedback_user.txt  # 审核反馈重写提示
```

### 关键设计

- **目录即配置**：文件路径 `input/大类/子类_数字/` 决定 WordPress 分类 ID 和 Telegram hashtag
- **分类配置**：`categories.json` 定义分类结构和 Telegram Bot 配置，scanner 优先从此文件加载
- **允许的大类**：`Articles`, `Books`, `Magazine`, `News`, `Paper`（定义在 `constants.py`）
- **凭据管理**：所有敏感信息在 `.env` 文件中，通过 Pydantic BaseSettings 加载，SecretStr 保护
- **延迟初始化**：AIWriter 的 OpenAI client 在首次调用时才创建
- **重试机制**：AI API (3次指数退避)、WordPress (2次固定5s)、Telegram (2次固定3s)
- **自定义异常**：BlogAutoPilotError 基类，各模块有专属异常类型（含 DatabaseError、EmbeddingError、TagExtractionError、AssociationError、QualityReviewError）
- **文章关联系统**（可选，依赖 DB）：四级标签体系（magazine/science/topic/content）+ 向量相似度搜索，两阶段检索（标签过滤 + embedding 排序）
- **内容去重**：基于 embedding 相似度检测（阈值 0.95），防止重复发布
- **质量审核系统**（可选，默认启用）：三维度评分（consistency/readability/ai_cliche），加权综合分 ≥7 pass、≥5 rewrite、<5 draft；自动重写最多 2 次，失败存草稿；审核异常降级发布
- **分类专属提示词**：每个大类有独立的写作系统提示，支持带上下文和不带上下文两种模式
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
- `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` — WordPress 认证
- `TG_BOT_TOKEN` / `TG_CHANNEL_ID` — Telegram Bot
- `AI_API_KEY` / `AI_API_BASE` / `AI_MODEL_WRITER` / `AI_MODEL_PROMO` — AI API 端点和模型
- `AI_QUALITY_REVIEW_ENABLED` / `AI_MODEL_REVIEWER` / `AI_REVIEWER_MAX_TOKENS` — 质量审核（可选，默认启用）
- `DB_URL` 或 `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` — PostgreSQL 数据库（可选）
- `EMBEDDING_API_KEY` / `EMBEDDING_API_BASE` / `EMBEDDING_MODEL` — Embedding API（可选）

分类结构通过根目录 `categories.json` 配置，定义各大类的子分类 ID 和 Telegram Bot Token。

## Important Notes

- `file_bot.py` 是独立的 Telegram 文件接收进程，与主流水线分开运行，保留在根目录
- `.env` 文件包含实际凭据，已在 `.gitignore` 中排除
- WordPress 发布失败时，草稿保存到 `./drafts/` 目录
- 质量审核未通过（verdict=draft 或重写次数用尽）时，草稿也保存到 `./drafts/` 目录
- 数据库功能为可选，未配置时流水线自动跳过关联/去重步骤
- 监控间隔为 60 秒（`POLL_INTERVAL` 定义在 `constants.py`）
