"""主流水线模块 — Pipeline 类"""

import json
import logging
import os
import shutil
import time

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import Settings
from blog_autopilot.constants import (
    DUPLICATE_SIMILARITY_THRESHOLD,
    POLL_INTERVAL,
    QUALITY_MAX_REWRITE_ATTEMPTS,
)
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    CoverImageError,
    ExtractionError,
    QualityReviewError,
    SEOExtractionError,
    TelegramError,
    WordPressError,
)
from blog_autopilot.extractor import extract_text_from_file
from blog_autopilot.models import FileTask, PipelineResult
from blog_autopilot.publisher import (
    ensure_wp_tags,
    post_to_wordpress,
    test_wp_connection,
)
from blog_autopilot.scanner import scan_input_directory
from blog_autopilot.telegram import send_to_telegram, test_tg_connection

logger = logging.getLogger("blog-autopilot")


class Pipeline:
    """主流水线，编排完整的文件处理流程"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._writer = AIWriter(settings.ai)
        # 封面图生成器（可选）
        self._cover_image_generator = None
        if settings.ai.cover_image_enabled:
            try:
                from blog_autopilot.cover_image import CoverImageGenerator
                self._cover_image_generator = CoverImageGenerator(settings.ai)
            except Exception as e:
                logger.warning(f"封面图生成器初始化失败: {e}")
        # 关联系统组件（可选，未配置数据库时为 None）
        self._database = None
        self._embedding_client = None
        self._ingestor = None
        self._init_association_components()

    def _init_association_components(self) -> None:
        """尝试初始化关联系统组件（数据库未配置时静默跳过）"""
        db_settings = self._settings.database
        emb_settings = self._settings.embedding

        # 检查数据库是否实际配置了有效凭据
        if not db_settings or not db_settings.user:
            logger.info("数据库未配置，关联系统已禁用")
            return

        try:
            from blog_autopilot.db import Database
            from blog_autopilot.embedding import EmbeddingClient
            from blog_autopilot.ingest import ArticleIngestor

            self._database = Database(db_settings)
            if emb_settings and emb_settings.api_key.get_secret_value():
                self._embedding_client = EmbeddingClient(emb_settings)
            self._ingestor = ArticleIngestor(self._settings)
            logger.info("关联系统组件初始化成功")
        except Exception as e:
            logger.warning(f"关联系统初始化失败，将使用原有模式: {e}")
            self._database = None
            self._embedding_client = None
            self._ingestor = None

    @property
    def _association_enabled(self) -> bool:
        """关联系统是否可用"""
        return (
            self._database is not None
            and self._embedding_client is not None
        )

    def process_file(self, task: FileTask) -> PipelineResult:
        """处理单个文件的完整流水线"""
        meta = task.metadata
        logger.info(f"\n{'='*50}")
        logger.info(f"开始处理: {task.filename}")
        logger.info(
            f"分类: {meta.category_name}/{meta.subcategory_name} "
            f"(ID: {meta.category_id})"
        )
        logger.info(f"Hashtag: {meta.hashtag}")
        logger.info(f"{'='*50}")

        # ① 提取文本
        try:
            raw_text = extract_text_from_file(task.filepath)
        except ExtractionError as e:
            logger.warning(f"跳过 {task.filename}: {e}")
            return PipelineResult(
                filename=task.filename, success=False, error=str(e)
            )

        # ② 关联查询（如果数据库可用）
        associations = None
        pre_tags = None
        pre_tg_promo = None
        pre_embedding = None
        if self._association_enabled:
            try:
                pre_tags, pre_tg_promo, _ = self._writer.extract_tags_and_promo(
                    raw_text
                )
                pre_embedding = self._embedding_client.get_embedding(
                    pre_tg_promo
                )

                # 内容去重：检查数据库中是否已有高度相似的文章
                dup = self._database.find_duplicate(
                    pre_embedding, DUPLICATE_SIMILARITY_THRESHOLD
                )
                if dup:
                    logger.info(
                        f"跳过重复内容: {task.filename} "
                        f"(与《{dup['title']}》相似度 {dup['similarity']:.2%})"
                    )
                    return PipelineResult(
                        filename=task.filename,
                        success=False,
                        error=f"内容重复: {dup['title']}",
                    )

                associations = self._database.find_related_articles(
                    tags=pre_tags,
                    embedding=pre_embedding,
                )
                if associations:
                    logger.info(
                        f"找到 {len(associations)} 篇关联文章"
                    )
            except Exception as e:
                logger.warning(f"关联查询失败，回退到原有模式: {e}")
                associations = None

        # ③ AI 生成文章（有关联时使用增强模式）
        try:
            article = self._writer.generate_blog_post_with_context(
                raw_text, associations=associations,
                category_name=meta.category_name,
            )
        except (AIAPIError, AIResponseParseError) as e:
            logger.error(f"跳过 {task.filename}: AI 生成内容失败 - {e}")
            return PipelineResult(
                filename=task.filename, success=False, error=str(e)
            )

        # ③.2 质量审核（失败不阻断发布）
        if self._settings.ai.quality_review_enabled:
            try:
                review = self._writer.review_quality(
                    article.title, article.html_body, raw_text,
                )
                if review.verdict == "draft":
                    self._save_draft(task.filename, article.title, article.html_body)
                    return PipelineResult(
                        filename=task.filename, success=False, title=article.title,
                        error=f"质量审核未通过 (综合分: {review.overall_score})",
                    )

                rewrite_count = 0
                while review.verdict == "rewrite" and rewrite_count < QUALITY_MAX_REWRITE_ATTEMPTS:
                    rewrite_count += 1
                    logger.info(
                        f"质量审核: 第 {rewrite_count}/{QUALITY_MAX_REWRITE_ATTEMPTS} 次重写"
                    )
                    article = self._writer.rewrite_with_feedback(
                        article.title, article.html_body, raw_text,
                        review, category_name=meta.category_name,
                    )
                    review = self._writer.review_quality(
                        article.title, article.html_body, raw_text,
                    )

                if review.verdict == "rewrite":
                    self._save_draft(task.filename, article.title, article.html_body)
                    return PipelineResult(
                        filename=task.filename, success=False, title=article.title,
                        error=f"重写 {QUALITY_MAX_REWRITE_ATTEMPTS} 次后仍未通过 (综合分: {review.overall_score})",
                    )

                if review.verdict == "draft":
                    self._save_draft(task.filename, article.title, article.html_body)
                    return PipelineResult(
                        filename=task.filename, success=False, title=article.title,
                        error=f"重写后质量审核未通过 (综合分: {review.overall_score})",
                    )

            except (AIAPIError, AIResponseParseError, QualityReviewError) as e:
                logger.warning(f"质量审核失败（不影响发布）: {e}")

        # ③.5 SEO 元数据提取（失败不阻断发布）
        seo = None
        wp_tag_ids = None
        try:
            seo = self._writer.extract_seo_metadata(
                article.title, article.html_body
            )
            if seo.wp_tags:
                wp_tag_ids = ensure_wp_tags(seo.wp_tags, self._settings.wp)
        except (AIAPIError, AIResponseParseError, SEOExtractionError) as e:
            logger.warning(f"SEO 提取失败（不影响发布）: {e}")

        # ③.8 封面图生成+上传（失败不阻断发布）
        featured_media_id = None
        if self._cover_image_generator:
            try:
                from blog_autopilot.cover_image import upload_media_to_wordpress

                image_data = self._cover_image_generator.generate_image(
                    article.title, article.html_body
                )
                slug = seo.slug if seo else task.filename.rsplit(".", 1)[0]
                featured_media_id = upload_media_to_wordpress(
                    image_data,
                    f"cover-{slug}.png",
                    self._settings.wp,
                )
            except CoverImageError as e:
                logger.warning(f"封面图生成/上传失败（不影响发布）: {e}")
            except Exception as e:
                logger.warning(f"封面图步骤异常（不影响发布）: {e}")

        # ④ 发布到 WordPress
        try:
            blog_link = post_to_wordpress(
                title=article.title,
                content=article.html_body,
                settings=self._settings.wp,
                category_id=meta.category_id,
                excerpt=seo.meta_description if seo else None,
                slug=seo.slug if seo else None,
                tag_ids=wp_tag_ids,
                featured_media=featured_media_id,
            )
        except WordPressError as e:
            logger.error(f"{task.filename}: WordPress 发布失败 - {e}")
            self._save_draft(task.filename, article.title, article.html_body)
            return PipelineResult(
                filename=task.filename,
                success=False,
                title=article.title,
                error=str(e),
            )

        # ⑤ 新文章入库（如果数据库可用）
        if self._association_enabled and self._ingestor:
            try:
                from blog_autopilot.models import ArticleRecord
                from blog_autopilot.db import Database

                tags = pre_tags
                embedding = pre_embedding
                # 如果之前关联查询失败，这里重新提取
                if tags is None:
                    tags, tg_promo, _ = self._writer.extract_tags_and_promo(
                        article.html_body
                    )
                    embedding = self._embedding_client.get_embedding(
                        tg_promo
                    )
                else:
                    tg_promo = pre_tg_promo

                record = ArticleRecord(
                    id=Database._generate_id(),
                    title=article.title,
                    tags=tags,
                    tg_promo=tg_promo,
                    embedding=embedding,
                    url=blog_link,
                )
                self._database.insert_article(record)
                logger.info(f"新文章已入库: {article.title}")
            except Exception as e:
                logger.warning(f"文章入库失败（不影响发布）: {e}")

        # ⑥ 推广
        try:
            promo_text = self._writer.generate_promo(
                article.title, article.html_body, hashtag=meta.hashtag
            )
            send_to_telegram(promo_text, blog_link, self._settings.tg)
        except (AIAPIError, TelegramError) as e:
            logger.warning(f"推广失败（文章已发布）: {e}")

        logger.info(f"{task.filename} 处理完成! -> {blog_link}")
        return PipelineResult(
            filename=task.filename,
            success=True,
            title=article.title,
            blog_link=blog_link,
        )

    def _save_draft(self, filename: str, title: str, html: str) -> None:
        """发布失败时，把草稿保存到本地"""
        draft_dir = self._settings.paths.drafts_folder
        os.makedirs(draft_dir, exist_ok=True)
        draft_path = os.path.join(draft_dir, f"{filename}.html")

        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(f"<!-- 标题: {title} -->\n{html}")

        logger.info(f"草稿已保存到: {draft_path}")

    def _get_archive_path(self, filepath: str) -> str:
        """根据 input 中的相对路径，计算 processed 中的对应路径"""
        input_folder = self._settings.paths.input_folder
        processed_dir = self._settings.paths.processed_folder
        rel_path = os.path.relpath(filepath, input_folder)
        return os.path.join(processed_dir, rel_path)

    def _archive_file(self, filepath: str) -> None:
        """归档文件：保持原目录结构和原文件名"""
        dest = self._get_archive_path(filepath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # 同名文件直接覆盖
        try:
            shutil.move(filepath, dest)
            logger.info(f"已归档: {os.path.relpath(dest, self._settings.paths.processed_folder)}")
        except Exception as e:
            logger.error(f"归档失败: {e}")

    def scan_and_process(self) -> int:
        """扫描 input 目录并处理所有文件"""
        input_folder = self._settings.paths.input_folder
        os.makedirs(input_folder, exist_ok=True)

        file_list = scan_input_directory(input_folder)

        if not file_list:
            return 0

        logger.info(f"发现 {len(file_list)} 个文件待处理")
        processed = 0

        for task in sorted(file_list, key=lambda t: t.filepath):
            # 检查 processed 中是否已有同名文件（重复投递）
            archive_path = self._get_archive_path(task.filepath)
            if os.path.exists(archive_path):
                logger.info(
                    f"跳过重复文件: {task.filename}（已处理过，直接删除）"
                )
                os.remove(task.filepath)
                continue

            try:
                result = self.process_file(task)
                if result.success:
                    processed += 1
                    self._archive_file(task.filepath)
                elif result.error and result.error.startswith("内容重复"):
                    # 内容重复：直接删除源文件
                    os.remove(task.filepath)
                else:
                    self._archive_file(task.filepath)
            except Exception as e:
                logger.error(
                    f"处理 {task.filename} 时发生异常: {e}", exc_info=True
                )
                self._archive_file(task.filepath)

        return processed

    def _ensure_category_dirs(self) -> None:
        """根据 categories.json 自动创建 input 子目录"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "categories.json"
        )
        if not os.path.exists(config_path):
            return

        with open(config_path, encoding="utf-8") as f:
            categories = json.load(f)

        input_folder = self._settings.paths.input_folder
        for category, subs in categories.items():
            if category.startswith("_") or not isinstance(subs, list):
                continue
            for sub in subs:
                dir_path = os.path.join(
                    input_folder, category, f"{sub['name']}_{sub['id']}"
                )
                os.makedirs(dir_path, exist_ok=True)

    def run(self, once: bool = False) -> None:
        """主循环入口"""
        paths = self._settings.paths
        os.makedirs(paths.input_folder, exist_ok=True)
        os.makedirs(paths.processed_folder, exist_ok=True)
        self._ensure_category_dirs()

        logger.info("Blog Autopilot 启动!")
        logger.info(f"  监控目录: {os.path.abspath(paths.input_folder)}")
        logger.info(f"  归档目录: {os.path.abspath(paths.processed_folder)}")
        logger.info(
            f"  运行模式: {'单次' if once else f'持续监控 (每 {POLL_INTERVAL}s)'}"
        )
        if self._association_enabled:
            logger.info("  关联系统: 已启用")
        else:
            logger.info("  关联系统: 未启用（数据库未配置）")
        if self._cover_image_generator:
            logger.info("  封面图生成: 已启用")
        else:
            logger.info("  封面图生成: 未启用")
        if self._settings.ai.quality_review_enabled:
            logger.info("  质量审核: 已启用")
        else:
            logger.info("  质量审核: 未启用")

        if once:
            count = self.scan_and_process()
            logger.info(f"单次处理完成, 共处理 {count} 篇文章")
            return

        while True:
            try:
                self.scan_and_process()
            except KeyboardInterrupt:
                logger.info("\n收到中断信号, 退出...")
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL)

    def run_test(self) -> None:
        """测试所有外部连接"""
        steps = 2
        if self._association_enabled:
            steps = 3

        print("\n连接测试\n" + "=" * 40)

        print(f"\n[1/{steps}] WordPress...")
        wp_ok = test_wp_connection(self._settings.wp)

        print(f"\n[2/{steps}] Telegram...")
        tg_ok = test_tg_connection(self._settings.tg)

        db_ok = None
        if self._association_enabled:
            print(f"\n[3/{steps}] Database...")
            db_ok = self._database.test_connection()

        print("\n" + "=" * 40)
        print(f"WordPress: {'OK' if wp_ok else 'FAIL'}")
        print(f"Telegram:  {'OK' if tg_ok else 'FAIL'}")
        if db_ok is not None:
            print(f"Database:  {'OK' if db_ok else 'FAIL'}")
        print("\nAI 模块测试请运行: python -m blog_autopilot.ai_writer <文件路径>")
