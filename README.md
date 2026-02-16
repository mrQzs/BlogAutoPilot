# Blog Autopilot

**自动化博客发布系统** — 监控目录中的文件，通过 AI 生成博客文章，自动发布到 WordPress 并推送 Telegram 推广文案。

**Automated blog publishing system** — monitors files in a directory, generates blog posts via AI, publishes to WordPress, and sends promotional messages to Telegram.

---

## 功能概览 / Features

- **文件监控** — 每 60 秒扫描 `input/` 目录，自动处理新增的 PDF / Markdown / TXT 文件
- **AI 写作** — 使用 Claude Opus 根据原始资料生成高质量博客文章（HTML 格式）
- **分类提示词** — 五大内容类型（Articles / Books / Magazine / News / Paper）各有专属写作风格
- **分类动态 temperature** — News 0.4（准确性优先）、Paper 0.5、Books/Magazine 0.8（创意优先）
- **文章关联** — 基于 pgvector 向量检索 + 四级标签匹配，自动引用站内相关文章
- **标签归一化** — 同义词自动合并（如 `AI应用` → `人工智能应用`），提升关联准确性
- **内容去重** — Embedding 相似度检测，防止重复发布
- **质量审核** — 三维度自动评分（一致性 / 可读性 / AI 痕迹），分类自适应阈值，审核结果入库
- **SEO 优化** — 自动提取 meta description、slug、WordPress 标签，含搜索意图分类
- **封面图生成** — AI 根据文章标题生成抽象风格封面图并上传到 WordPress
- **HTML 安全清洗** — 发布前自动移除 XSS 风险内容（script/iframe/事件属性/javascript: 协议）
- **系列检测** — 自动识别系列文章（向量 + LLM 辅助判断），注入上下篇导航链接
- **模型回退** — 主模型不可用时自动切换备用模型，认证错误不重试
- **Token 追踪** — 每篇文章的 API 调用 token 用量汇总日志
- **发布时段** — 可配置发布窗口（如 8:00-22:00），支持跨午夜
- **文件锁** — 多进程安全，防止并发处理同一文件
- **失败重试** — 入库失败自动保存，下次启动重试
- **WordPress 发布** — 通过 REST API 发布文章，按目录结构自动归类
- **Telegram 推送** — AI 生成推广文案，自动推送到 Telegram 频道
- **Telegram Bot 收件** — 通过 Telegram Bot 远程上传文件到对应分类目录
- **配置校验** — 启动时校验 URL 格式、端口范围、必填字段，错误立即报告
- **systemd 服务** — 支持作为系统服务持续运行

---

## 架构 / Architecture

```
文件放入 input/大类/子类_分类ID/
  → 文件锁 (防止多进程并发)
  → extractor 提取文本 (PDF / MD / TXT)
  → tagger 提取四级标签 + Embedding (Haiku) + 同义词归一化
  → 内容去重检查
  → 关联文章查询
  → 系列检测 (向量 + LLM 辅助判断)
  → AIWriter 生成博客文章 (Opus, 按大类选择提示词和 temperature)
  → 质量审核 (三维度评分, 分类自适应阈值, 审核结果入库)
  → SEO 元数据提取 (含搜索意图分类) + 封面图生成
  → HTML 安全清洗 (移除 XSS 风险内容)
  → 发布时段检查 (可选)
  → publisher 发布到 WordPress (按目录中的分类 ID)
  → AIWriter 生成推广文案 (Haiku)
  → telegram 推送到频道
  → Token 用量汇总
  → 归档到 processed/
```

### 目录结构 / Project Structure

```
BlogAutoPilot/
├── blog_autopilot/
│   ├── __init__.py          # 版本号
│   ├── __main__.py          # CLI 入口
│   ├── config.py            # Pydantic BaseSettings 配置管理 (含 field_validator 校验)
│   ├── models.py            # dataclass 数据模型 (含 TokenUsage)
│   ├── exceptions.py        # 自定义异常层级
│   ├── constants.py         # 命名常量 (含分类阈值/temperature)
│   ├── pipeline.py          # Pipeline 主流水线编排 (文件锁/发布时段/失败重试)
│   ├── scanner.py           # 目录扫描 + 路径解析
│   ├── ai_writer.py         # AI 写作 (模型回退/Token 追踪/标签提取/质量审核)
│   ├── cover_image.py       # 封面图生成 + WordPress 上传
│   ├── publisher.py         # WordPress REST API 发布 + HTML 安全清洗
│   ├── telegram.py          # Telegram Bot API 推送
│   ├── extractor.py         # 文本提取 (PDF / MD / TXT)
│   ├── db.py                # PostgreSQL + pgvector 数据库 (含审核日志表)
│   ├── embedding.py         # Embedding 向量生成
│   ├── ingest.py            # 文章入库
│   ├── recommender.py       # 智能选题推荐
│   ├── series.py            # 系列检测 (向量 + LLM) + 导航 HTML
│   ├── tag_normalizer.py    # 标签同义词归一化
│   └── prompts/             # AI 提示词模板
│       ├── writer_system.txt                  # 通用写作提示词
│       ├── writer_system_articles.txt         # 深度专栏风格
│       ├── writer_system_books.txt            # 书评风格
│       ├── writer_system_magazine.txt         # 技术博客风格
│       ├── writer_system_news.txt             # 新闻解读风格
│       ├── writer_system_paper.txt            # 论文摘选风格
│       ├── writer_context_system_*.txt        # 带关联引用的版本 (含链接密度控制)
│       ├── writer_user.txt                    # 用户提示词
│       ├── promo_system.txt / promo_user.txt  # 推广文案提示词
│       ├── tagger_system.txt / tagger_user.txt # 标签提取提示词 (含正反示例)
│       ├── seo_system.txt / seo_user.txt      # SEO 元数据提取 (含搜索意图分类)
│       ├── review_system.txt / review_user.txt / rewrite_feedback_user.txt # 质量审核 (含评分示例)
│       ├── recommend_system.txt / recommend_user.txt # 选题推荐
│       └── series_check_system.txt / series_check_user.txt # LLM 系列判断
├── file_bot.py              # Telegram Bot 文件接收 (独立进程)
├── categories.json          # 分类配置 + Bot 配置
├── tag_synonyms.json        # 标签同义词映射
├── tests/                   # 测试 (251 个用例)
├── input/                   # 待处理文件 (按分类放置)
├── processed/               # 已处理归档
├── drafts/                  # WordPress 发布失败时的草稿
├── failed_ingests/          # 入库失败记录 (自动重试)
├── .env                     # 凭据配置 (不入 git)
├── .env.example             # 凭据配置示例
└── pyproject.toml           # 项目元数据 + 依赖
```

---

## 快速开始 / Quick Start

### 1. 环境要求 / Prerequisites

- Python >= 3.11
- PostgreSQL >= 14（含 pgvector 扩展，可选，不配置则关联系统禁用）
- WordPress 站点（已启用 REST API + Application Passwords）
- Telegram Bot Token
- AI API（OpenAI 兼容接口，如 Claude API）

### 2. 安装 / Installation

```bash
git clone https://github.com/mrQzs/BlogAutoPilot.git
cd BlogAutoPilot

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"
```

### 3. 配置 / Configuration

```bash
cp .env.example .env
```

编辑 `.env` 填入你的凭据：

```env
# WordPress 配置
WP_URL=https://your-site.com/index.php?rest_route=/wp/v2/posts
WP_USER=your_wp_username
WP_APP_PASSWORD=your_wp_app_password
WP_TARGET_CATEGORY_ID=15

# Telegram 配置
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHANNEL_ID=@your_channel_id

# AI API 配置 (OpenAI 兼容接口)
AI_API_KEY=your_ai_api_key
AI_API_BASE=https://api.example.com/v1
AI_MODEL_WRITER=claude-opus-4-5-20251101
AI_MODEL_PROMO=claude-haiku-4-5-20251001
AI_MODEL_WRITER_FALLBACK=          # 可选，主模型失败时的备用模型
AI_MODEL_PROMO_FALLBACK=           # 可选
AI_WRITER_MAX_TOKENS=200000
AI_PROMO_MAX_TOKENS=10000

# 质量审核配置 (可选，默认启用)
AI_QUALITY_REVIEW_ENABLED=true
AI_MODEL_REVIEWER=              # 空则回退到 AI_MODEL_PROMO
AI_REVIEWER_MAX_TOKENS=4096

# 封面图配置 (可选，默认启用，仅基于标题生成)
AI_COVER_IMAGE_ENABLED=true
AI_MODEL_COVER_IMAGE=dall-e-3
AI_COVER_IMAGE_API_KEY=         # 可选，空则使用 AI_API_KEY
AI_COVER_IMAGE_API_BASE=https://api.openai.com/v1

# 数据库配置 (可选，不配置则关联系统禁用)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=blog_articles
DB_USER=your_db_user
DB_PASSWORD=your_db_password

# Embedding 配置 (可选，配合数据库使用)
EMBEDDING_API_KEY=your_openai_api_key
EMBEDDING_API_BASE=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072

# 发布时段配置 (可选)
SCHEDULE_PUBLISH_WINDOW_ENABLED=false
SCHEDULE_PUBLISH_WINDOW_START=8    # 开始小时 (0-23)
SCHEDULE_PUBLISH_WINDOW_END=22     # 结束小时 (0-23)
```

### 4. 数据库设置 / Database Setup (Optional)

如需文章关联和内容去重功能：

```bash
# 安装 pgvector 扩展
sudo apt install postgresql-14-pgvector

# 创建数据库和用户
sudo -u postgres psql
CREATE USER blog_user WITH PASSWORD 'your_password';
CREATE DATABASE blog_articles OWNER blog_user;
\c blog_articles
CREATE EXTENSION vector;
\q

# 初始化表结构
python -m blog_autopilot --init-db
```

### 5. 分类配置 / Category Configuration

编辑 `categories.json` 定义你的内容分类：

```json
{
  "_bots": {
    "admin_id": 123456789,
    "main_token": "your_main_bot_token",
    "main_save_path": "/path/to/BlogAutoPilot/input"
  },
  "Articles": [
    {"name": "Featured", "id": 39}
  ],
  "Books": [
    {"name": "Readed", "id": 40},
    {"name": "Recommend", "id": 41}
  ],
  "Magazine": [
    {"name": "Science", "id": 28, "bot_token": "optional_dedicated_bot_token"},
    {"name": "Technology", "id": 31}
  ],
  "News": [
    {"name": "GoodNews", "id": 42}
  ],
  "Paper": [
    {"name": "Excerpt", "id": 43}
  ]
}
```

| 字段 / Field | 说明 / Description |
|---|---|
| `_bots.admin_id` | 允许上传文件的 Telegram 用户 ID |
| `_bots.main_token` | 主 Bot Token（文件存入 input 根目录） |
| `name` | 子类名称，与目录名对应 |
| `id` | WordPress 分类 ID |
| `bot_token` | 可选，该子类专属 Bot（文件直接存入对应目录） |

---

## 使用方法 / Usage

### 目录投放文件

将文件放入对应的分类目录即可：

```
input/
├── Articles/Featured_39/    ← 深度专栏文章
├── Books/Readed_40/         ← 书评
├── Magazine/Science_28/     ← 技术博客
├── News/GoodNews_42/        ← 新闻解读
└── Paper/Excerpt_43/        ← 论文摘选
```

支持的文件格式：`.pdf` `.md` `.txt`

### CLI 命令

```bash
# 单次处理 input 目录中的所有文件
python -m blog_autopilot --once

# 持续监控模式 (每 60 秒扫描)
python -m blog_autopilot

# 测试 WordPress 和 Telegram 连接
python -m blog_autopilot --test

# 测试数据库连接
python -m blog_autopilot --test-db

# 初始化数据库表结构
python -m blog_autopilot --init-db

# 手动入库文章 (用于历史文章导入)
python -m blog_autopilot --ingest /path/to/file.pdf
python -m blog_autopilot --ingest /path/to/directory/
```

### Telegram Bot 远程上传

```bash
# 启动文件接收 Bot (独立进程)
python file_bot.py
```

向对应的 Telegram Bot 发送文件，Bot 会自动保存到对应的 `input/` 子目录。

### systemd 服务

```bash
# 创建服务文件
sudo tee /etc/systemd/system/blog-autopilot.service << 'EOF'
[Unit]
Description=Blog Autopilot
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/path/to/BlogAutoPilot
ExecStart=/path/to/BlogAutoPilot/.venv/bin/python -m blog_autopilot
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable --now blog-autopilot

# 查看日志
journalctl -u blog-autopilot -f
```

如需同时运行 file_bot：

```bash
sudo tee /etc/systemd/system/blog-filebot.service << 'EOF'
[Unit]
Description=Blog Autopilot File Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/BlogAutoPilot
ExecStart=/path/to/BlogAutoPilot/.venv/bin/python file_bot.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now blog-filebot
```

---

## 分类写作风格 / Category Writing Styles

| 大类 / Category | 风格 / Style | Temperature | 质量阈值 pass/rewrite |
|---|---|---|---|
| **Articles** | 深度专栏 | 0.7 | 7 / 5 |
| **Books** | 书评 | 0.8 | 8 / 6 |
| **Magazine** | 技术博客 | 0.8 | 7 / 5 |
| **News** | 新闻解读 | 0.4 | 6 / 4 |
| **Paper** | 论文摘选 | 0.5 | 8 / 6 |

AI 会根据文件所在的大类目录自动选择对应的写作提示词。如果找不到分类专属提示词，会回退到通用版本。

---

## 重试与安全机制 / Retry & Safety

| 组件 / Component | 重试次数 / Retries | 策略 / Strategy |
|---|---|---|
| AI API | 3 次 | 指数退避 (2s ~ 30s)，认证错误不重试 |
| AI 模型回退 | 1 次 | 主模型失败后切换备用模型 |
| 质量审核 | 最多 2 次重写 | 分类自适应阈值，未通过存草稿，异常降级发布 |
| WordPress | 2 次 | 固定 5s (仅 retryable 5xx 错误) |
| Telegram | HTML → 纯文本降级 | 解析失败时自动降级 |
| 入库失败 | 下次启动重试 | 保存到 failed_ingests/，自动清理损坏记录 |
| HTML 清洗 | 发布前自动执行 | 移除 script/iframe/on*/javascript: |
| 文件锁 | 跳过 | 被锁文件跳过，下次扫描再处理 |

---

## 测试 / Testing

```bash
# 运行全部测试 (251 个用例)
pytest tests/ -v

# 运行特定模块测试
pytest tests/test_pipeline.py -v
pytest tests/test_ai_writer.py -v
pytest tests/test_quality_review.py -v
pytest tests/test_config.py -v
pytest tests/test_html_sanitize.py -v
pytest tests/test_models.py -v
```

---

## 依赖 / Dependencies

| 包 / Package | 用途 / Purpose |
|---|---|
| `openai` | AI API 调用 (OpenAI 兼容模式) |
| `requests` | WordPress REST API / Telegram Bot API |
| `pypdf` | PDF 文本提取 |
| `pydantic-settings` | 配置管理 (.env) |
| `tenacity` | 重试机制 |
| `psycopg2-binary` | PostgreSQL 连接 |
| `pgvector` | 向量检索 |
| `python-dotenv` | .env 文件加载 |

---

## License

MIT
