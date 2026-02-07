# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Blog Autopilot** — Python 自动化博客发布系统。监控 `input/` 目录中的文件，通过 AI 生成博客文章，自动发布到 WordPress 并推送 Telegram 推广文案。

## Architecture

处理流水线（`blog_autopilot/pipeline.py` 中的 `Pipeline.process_file()`）：

```
文件放入 input/大类/子类_分类ID/
  → extractor 提取文本
  → AIWriter (Opus) 生成博客文章 (标题 + HTML)
  → publisher 发布到 WordPress (按目录中的分类 ID)
  → AIWriter (Haiku) 生成推广文案
  → telegram 推送到 Telegram 频道
  → 归档到 processed/
```

### 包结构

```
blog_autopilot/
├── __init__.py        # 版本号
├── __main__.py        # python -m blog_autopilot 入口
├── config.py          # Pydantic BaseSettings（从 .env 读取凭据）
├── models.py          # dataclass 数据模型
├── exceptions.py      # 自定义异常层级
├── constants.py       # 命名常量
├── pipeline.py        # Pipeline 类（主流水线编排）
├── scanner.py         # 目录扫描 + 路径解析
├── ai_writer.py       # AIWriter 类（延迟初始化，依赖注入）
├── publisher.py       # WordPress REST API 发布
├── telegram.py        # Telegram Bot API 推送
├── extractor.py       # 文本提取（PDF/MD/TXT）
└── prompts/           # 提示词模板
    ├── writer_system.txt
    ├── writer_user.txt
    ├── promo_system.txt
    └── promo_user.txt
```

### 关键设计

- **目录即配置**：文件路径 `input/大类/子类_数字/` 决定 WordPress 分类 ID 和 Telegram hashtag
- **允许的大类**：`Articles`, `Books`, `Magazine`, `News`（定义在 `constants.py`）
- **凭据管理**：所有敏感信息在 `.env` 文件中，通过 Pydantic BaseSettings 加载，SecretStr 保护
- **延迟初始化**：AIWriter 的 OpenAI client 在首次调用时才创建
- **重试机制**：AI API (3次指数退避)、WordPress (2次固定5s)、Telegram (2次固定3s)
- **自定义异常**：BlogAutoPilotError 基类，各模块有专属异常类型
- `file_bot.py` 保留在根目录，是独立进程

## Commands

```bash
# 安装依赖
pip install -e ".[dev]"

# 单次处理 input 目录中的所有文件
python -m blog_autopilot --once

# 持续监控模式（每 10 分钟扫描一次）
python -m blog_autopilot

# 测试 WordPress 和 Telegram 连接
python -m blog_autopilot --test

# 运行测试
pytest tests/ -v
```

## Dependencies

定义在 `pyproject.toml`：
- `openai` — AI API 调用（OpenAI 兼容模式）
- `requests` — WordPress REST API 和 Telegram Bot API
- `pypdf` — PDF 文本提取
- `pydantic-settings` — 配置管理
- `tenacity` — 重试机制
- `python-dotenv` — .env 文件加载
- `pytest` / `pytest-mock` — 开发依赖

## Configuration

所有配置通过 `.env` 文件管理（参考 `.env.example`）：
- `WP_USER` / `WP_APP_PASSWORD` — WordPress 认证
- `TG_BOT_TOKEN` / `TG_CHANNEL_ID` — Telegram Bot
- `AI_API_KEY` / `AI_API_BASE` — AI API 端点

## Important Notes

- `file_bot.py` 是独立进程，与主流水线分开运行，保留在根目录
- `.env` 文件包含实际凭据，已在 `.gitignore` 中排除
- WordPress 发布失败时，草稿保存到 `./drafts/` 目录
