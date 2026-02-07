# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Blog Autopilot** — Python 自动化博客发布系统。监控 `input/` 目录中的文件，通过 AI 生成博客文章，自动发布到 WordPress 并推送 Telegram 推广文案。

## Architecture

处理流水线（`main.py` 中的 `process_file()`）：

```
文件放入 input/大类/子类_分类ID/
  → extract_text 提取文本
  → ai_writer (Opus) 生成博客文章 (标题 + HTML)
  → publish_wp 发布到 WordPress (按目录中的分类 ID)
  → ai_writer (Haiku) 生成推广文案
  → push_telegram 推送到 Telegram 频道
  → 归档到 processed/
```

### 核心模块

| 文件 | 职责 |
|------|------|
| `main.py` | 主流水线：目录扫描、路径解析、文件处理编排、归档 |
| `ai_writer.py` | AI 写作模块，使用 OpenAI 兼容 API 调用 Claude 模型（Opus 写文章，Haiku 写推广文案） |
| `publish_wp.py` | WordPress REST API 发布模块（Basic Auth） |
| `push_telegram.py` | Telegram Bot API 推送模块 |
| `config.py` | 全局配置：API 密钥、路径、模型参数、日志 |
| `file_bot.py` | 独立的 Telegram Bot，接收文件并保存到 input 目录（使用 python-telegram-bot 库） |

### 关键设计

- **目录即配置**：文件路径 `input/大类/子类_数字/` 决定 WordPress 分类 ID 和 Telegram hashtag
- **允许的大类**：`Articles`, `Books`, `Magazine`, `News`（定义在 `main.py` 的 `ALLOWED_CATEGORIES`）
- **子类目录命名**：`子类名_数字`，正则 `r'^(.+)_(\d+)$'` 解析，数字为 WordPress 分类 ID
- AI 调用通过 OpenAI SDK 的兼容模式（`openai.OpenAI`），连接第三方 API 代理
- AI 返回的第一行被解析为文章标题，其余为 HTML 正文体
- WordPress 发布失败时，草稿保存到 `./drafts/` 目录
- 归档文件移动到 `./processed/`，以文章标题重命名

## Commands

```bash
# 单次处理 input 目录中的所有文件
python main.py --once

# 持续监控模式（每 10 分钟扫描一次）
python main.py

# 测试 WordPress 和 Telegram 连接
python main.py --test

# 单独测试 AI 写作（不发布）
python ai_writer.py <文件路径>

# 单独测试 WordPress 发布
python publish_wp.py <标题> <HTML内容> [draft|publish]

# 单独测试 Telegram 推送
python push_telegram.py <推广文案> <文章链接>

# 运行单元测试
python test_directory_parsing.py

# 运行集成测试
python test_integration.py
```

## Dependencies

- `openai` — AI API 调用（OpenAI 兼容模式）
- `requests` — WordPress REST API 和 Telegram Bot API
- `python-telegram-bot` — file_bot.py 使用的 Telegram Bot 框架
- `pypdf` — PDF 文本提取（`extract_text.py` 使用）

## Configuration

所有配置集中在 `config.py`，敏感信息支持环境变量覆盖：
- `WP_USER` / `WP_APP_PASSWORD` — WordPress 认证
- `TG_BOT_TOKEN` / `TG_CHANNEL_ID` — Telegram Bot
- `AI_API_KEY` / `AI_API_BASE` — AI API 端点（硬编码，未走环境变量）

## Important Notes

- `extract_text.py` 被 `main.py` 和 `ai_writer.py` 导入但不在仓库中，缺失会导致运行报错
- 测试脚本（`test_*.py`）不使用 unittest/pytest 框架，是独立脚本，直接 `python` 运行
- WordPress 发布失败时，草稿保存到 `./drafts/` 目录
- `file_bot.py` 是独立进程，与主流水线分开运行
