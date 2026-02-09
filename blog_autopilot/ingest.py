"""文章入库工作流 — 文本 → 标签提取 → Embedding → 存库"""

import logging
import os
from collections.abc import Callable

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import Settings
from blog_autopilot.db import Database
from blog_autopilot.embedding import EmbeddingClient
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    DatabaseError,
    EmbeddingError,
    TagExtractionError,
)
from blog_autopilot.extractor import extract_text_from_file
from blog_autopilot.models import ArticleRecord, IngestionResult, TagSet

logger = logging.getLogger("blog-autopilot")


class ArticleIngestor:
    """文章入库器，编排完整的入库流程"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._writer = AIWriter(settings.ai)
        self._embedding = EmbeddingClient(settings.embedding)
        self._db = Database(settings.database)

    @property
    def database(self) -> Database:
        return self._db

    def ingest_article(
        self,
        content: str,
        url: str | None = None,
        article_id: str | None = None,
    ) -> IngestionResult:
        """
        完整入库流程：文本 → 标签提取 → Embedding → 存库。

        任一步骤失败时返回 IngestionResult(success=False)。
        """
        # URL 去重检查
        if url:
            try:
                existing = self._db.get_article_by_url(url)
                if existing:
                    logger.info(f"文章已存在 (URL: {url}), 跳过入库")
                    return IngestionResult(
                        article_id=existing.id,
                        title=existing.title,
                        tags=existing.tags,
                        success=True,
                    )
            except DatabaseError as e:
                logger.warning(f"URL 去重检查失败: {e}")

        # Step 1: 标签提取 + TG 推广文案
        try:
            tags, tg_promo, title = self._writer.extract_tags_and_promo(content)
        except (AIAPIError, AIResponseParseError, TagExtractionError) as e:
            logger.error(f"标签提取失败: {e}")
            return IngestionResult(
                article_id=article_id or "",
                title="",
                error=f"标签提取失败: {e}",
                success=False,
            )

        # Step 2: Embedding
        try:
            embedding = self._embedding.get_embedding(tg_promo)
        except (EmbeddingError, ValueError) as e:
            logger.error(f"Embedding 失败: {e}")
            return IngestionResult(
                article_id=article_id or "",
                title="",
                tags=tags,
                error=f"Embedding 失败: {e}",
                success=False,
            )

        # Step 3: 构建记录
        record = ArticleRecord(
            id=article_id or Database._generate_id(),
            title=title,
            tags=tags,
            tg_promo=tg_promo,
            embedding=embedding,
            url=url,
        )

        # Step 4: 写入数据库
        try:
            saved_id = self._db.insert_article(record)
        except DatabaseError as e:
            logger.error(f"数据库写入失败: {e}")
            return IngestionResult(
                article_id=record.id,
                title=record.title,
                tags=tags,
                error=f"数据库写入失败: {e}",
                success=False,
            )

        logger.info(f"文章入库成功: {saved_id} - {record.title}")
        return IngestionResult(
            article_id=saved_id,
            title=record.title,
            tags=tags,
            success=True,
        )

    def ingest_batch(
        self,
        articles: list[dict],
        on_progress: Callable | None = None,
    ) -> list[IngestionResult]:
        """
        批量入库多篇文章。

        每条 dict 包含: content (必需), url (可选), id (可选)。
        单篇失败不影响后续文章。
        """
        results: list[IngestionResult] = []
        total = len(articles)

        for i, article in enumerate(articles, 1):
            content = article.get("content", "")
            url = article.get("url")
            article_id = article.get("id")

            if not content:
                result = IngestionResult(
                    article_id=article_id or "",
                    title="",
                    error="内容为空",
                    success=False,
                )
            else:
                result = self.ingest_article(
                    content=content, url=url, article_id=article_id
                )

            results.append(result)
            logger.info(
                f"批量入库进度: {i}/{total} | "
                f"{'成功' if result.success else '失败'}"
            )

            if on_progress:
                on_progress(i, total, result)

        # 汇总报告
        success_count = sum(1 for r in results if r.success)
        fail_count = total - success_count
        logger.info(
            f"批量入库完成: 共 {total} 篇, "
            f"成功 {success_count}, 失败 {fail_count}"
        )

        return results

    def ingest_from_directory(
        self, directory: str
    ) -> list[IngestionResult]:
        """
        扫描目录下所有 .md / .txt / .pdf 文件并逐个入库。
        """
        supported_ext = {".md", ".txt", ".pdf"}
        files = []

        for entry in sorted(os.listdir(directory)):
            _, ext = os.path.splitext(entry)
            if ext.lower() in supported_ext:
                files.append(os.path.join(directory, entry))

        if not files:
            logger.info(f"目录 {directory} 中没有找到可入库的文件")
            return []

        logger.info(f"在 {directory} 中找到 {len(files)} 个文件待入库")

        results: list[IngestionResult] = []
        for i, filepath in enumerate(files, 1):
            filename = os.path.basename(filepath)
            logger.info(f"[{i}/{len(files)}] 正在入库: {filename}")

            try:
                content = extract_text_from_file(filepath)
            except Exception as e:
                logger.error(f"文本提取失败 {filename}: {e}")
                results.append(IngestionResult(
                    article_id="",
                    title=filename,
                    error=f"文本提取失败: {e}",
                    success=False,
                ))
                continue

            result = self.ingest_article(content=content)
            results.append(result)

        # 汇总
        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count
        print(f"\n入库汇总: 共 {len(results)} 篇")
        print(f"  成功: {success_count}")
        print(f"  失败: {fail_count}")
        for r in results:
            if not r.success:
                print(f"  - {r.title or r.article_id}: {r.error}")

        return results
