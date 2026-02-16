"""主流水线模块 — Pipeline 类"""

import fcntl
import hashlib
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
    SeriesDetectionError,
    TelegramError,
    WordPressError,
)
from blog_autopilot.extractor import extract_text_from_file
from blog_autopilot.models import FileTask, PipelineResult
from blog_autopilot.publisher import (
    PublishResult,
    ensure_wp_tags,
    get_wp_post_content,
    post_to_wordpress,
    test_wp_connection,
    update_wp_post_content,
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
        # 文件锁：防止多进程同时处理同一文件
        try:
            lock_fd = open(task.filepath, "r")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            logger.info(f"跳过 {task.filename}: 文件正被其他进程处理")
            return PipelineResult(
                filename=task.filename, success=False,
                error="文件被锁定，跳过处理",
            )

        try:
            return self._process_file_impl(task)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def _process_file_impl(self, task: FileTask) -> PipelineResult:
        """处理单个文件的实际逻辑（已持有文件锁）"""
        self._writer.reset_usage()
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
        pre_title = None
        if self._association_enabled:
            try:
                pre_tags, pre_tg_promo, pre_title = self._writer.extract_tags_and_promo(
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

        # ②.5 系列检测（如果数据库可用）
        series_info = None
        if self._association_enabled and pre_tags and pre_embedding:
            try:
                from blog_autopilot.series import detect_series
                series_info = detect_series(
                    self._database, pre_tags, pre_embedding,
                    pre_title or "",
                    ai_writer=self._writer,
                )
            except SeriesDetectionError as e:
                logger.warning(f"系列检测失败（不影响发布）: {e}")
            except Exception as e:
                logger.warning(f"系列检测异常（不影响发布）: {e}")

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
                # 获取分类专属质量阈值
                from blog_autopilot.constants import CATEGORY_QUALITY_THRESHOLDS
                cat_thresholds = CATEGORY_QUALITY_THRESHOLDS.get(meta.category_name)
                review = self._writer.review_quality(
                    article.title, article.html_body, raw_text,
                    pass_threshold=cat_thresholds[0] if cat_thresholds else None,
                    rewrite_threshold=cat_thresholds[1] if cat_thresholds else None,
                )
                # 审核结果入库
                if self._database:
                    self._database.insert_review(
                        article.title, review,
                        category_name=meta.category_name,
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
                        pass_threshold=cat_thresholds[0] if cat_thresholds else None,
                        rewrite_threshold=cat_thresholds[1] if cat_thresholds else None,
                    )
                    # 审核结果入库
                    if self._database:
                        self._database.insert_review(
                            article.title, review,
                            category_name=meta.category_name,
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

        # ③.9 注入系列导航（如果检测到系列）
        if series_info:
            try:
                from blog_autopilot.series import inject_series_navigation
                from blog_autopilot.models import ArticleResult as _AR
                html_with_nav = inject_series_navigation(
                    article.html_body, series_info,
                )
                article = _AR(title=article.title, html_body=html_with_nav)
                logger.info(
                    f"已注入系列导航: 《{series_info.series_title}》"
                    f"第 {series_info.order}/{series_info.total} 篇"
                )
            except Exception as e:
                logger.warning(f"系列导航注入失败（不影响发布）: {e}")

        # ④ 发布到 WordPress
        try:
            publish_result = post_to_wordpress(
                title=article.title,
                content=article.html_body,
                settings=self._settings.wp,
                category_id=meta.category_id,
                excerpt=seo.meta_description if seo else None,
                slug=seo.slug if seo else None,
                tag_ids=wp_tag_ids,
                featured_media=featured_media_id,
            )
            blog_link = publish_result.url
            wp_post_id = publish_result.post_id
        except WordPressError as e:
            logger.error(f"{task.filename}: WordPress 发布失败 - {e}")
            self._save_draft(task.filename, article.title, article.html_body)
            return PipelineResult(
                filename=task.filename,
                success=False,
                title=article.title,
                error=str(e),
            )

        # ④.5 回溯更新上一篇文章的系列导航
        if series_info and series_info.prev_article:
            try:
                from blog_autopilot.series import (
                    build_backfill_navigation,
                    replace_series_navigation,
                )
                prev_wp_id = self._database.get_wp_post_id(
                    series_info.prev_article.id,
                )
                if prev_wp_id:
                    old_content = get_wp_post_content(
                        prev_wp_id, self._settings.wp,
                    )
                    if old_content:
                        # 上一篇的 prev_article
                        prev_of_prev = None
                        if series_info.order > 2:
                            members = self._database.get_series_articles(
                                series_info.series_id,
                            )
                            for i, m in enumerate(members):
                                if m.id == series_info.prev_article.id and i > 0:
                                    prev_of_prev = members[i - 1]
                                    break

                        new_nav = build_backfill_navigation(
                            series_title=series_info.series_title,
                            order=series_info.order - 1,
                            total=series_info.total,
                            prev_article=prev_of_prev,
                            next_article_title=article.title,
                            next_article_url=blog_link,
                        )
                        updated_content = replace_series_navigation(
                            old_content, new_nav,
                        )
                        update_wp_post_content(
                            prev_wp_id, updated_content, self._settings.wp,
                        )
                        logger.info(
                            f"已回溯更新上一篇导航 (post_id={prev_wp_id})"
                        )
            except Exception as e:
                logger.warning(f"回溯更新导航失败（不影响发布）: {e}")

        # ⑤ 新文章入库（如果数据库可用）
        if self._association_enabled and self._ingestor:
            try:
                from blog_autopilot.models import ArticleRecord
                from blog_autopilot.db import Database

                tags = pre_tags
                embedding = pre_embedding
                # 如果之前关联查询失败或 embedding 缺失，重新提取
                if tags is None or embedding is None:
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
                self._database.insert_article(
                    record,
                    series_id=series_info.series_id if series_info else None,
                    series_order=series_info.order if series_info else None,
                    wp_post_id=wp_post_id,
                )
                logger.info(f"新文章已入库: {article.title}")
            except Exception as e:
                logger.warning(f"文章入库失败（不影响发布）: {e}")
                self._save_failed_ingest(
                    article.title, blog_link, wp_post_id,
                    tags, embedding, tg_promo,
                    series_info,
                )

        # ⑥ 推广
        try:
            promo_text = self._writer.generate_promo(
                article.title, article.html_body, hashtag=meta.hashtag
            )
            send_to_telegram(promo_text, blog_link, self._settings.tg)
        except (AIAPIError, TelegramError) as e:
            logger.warning(f"推广失败（文章已发布）: {e}")

        logger.info(f"{task.filename} 处理完成! -> {blog_link}")
        # Token 用量汇总
        logger.info(self._writer.usage_summary.summary_str())
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

    def _save_failed_ingest(
        self,
        title: str,
        url: str,
        wp_post_id: int,
        tags,
        embedding,
        tg_promo: str,
        series_info,
    ) -> None:
        """入库失败时保存记录，供后续重试"""
        failed_dir = os.path.join(
            os.path.dirname(self._settings.paths.drafts_folder),
            "failed_ingests",
        )
        os.makedirs(failed_dir, exist_ok=True)

        record = {
            "title": title,
            "url": url,
            "wp_post_id": wp_post_id,
            "tg_promo": tg_promo,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if tags:
            record["tags"] = {
                "tag_magazine": tags.tag_magazine,
                "tag_science": tags.tag_science,
                "tag_topic": tags.tag_topic,
                "tag_content": tags.tag_content,
            }
        if series_info:
            record["series_id"] = series_info.series_id
            record["series_order"] = series_info.order
        # embedding 太大不存 JSON，重试时重新生成

        filename = hashlib.md5(title.encode()).hexdigest()[:12] + ".json"
        filepath = os.path.join(failed_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            logger.info(f"入库失败记录已保存: {filepath}")
        except Exception as save_err:
            logger.error(f"保存入库失败记录也失败了: {save_err}")

    def retry_failed_ingests(self) -> int:
        """重试之前入库失败的记录"""
        failed_dir = os.path.join(
            os.path.dirname(self._settings.paths.drafts_folder),
            "failed_ingests",
        )
        if not os.path.isdir(failed_dir):
            return 0

        if not self._association_enabled or not self._ingestor:
            return 0

        retried = 0
        for fname in os.listdir(failed_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(failed_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    record = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"入库重试文件损坏，已删除 ({fname}): {e}")
                os.remove(fpath)
                continue

            try:
                from blog_autopilot.models import ArticleRecord, TagSet
                from blog_autopilot.db import Database

                tags_data = record.get("tags")
                if not tags_data:
                    logger.warning(f"入库重试记录缺少标签，已删除 ({fname})")
                    os.remove(fpath)
                    continue

                tags = TagSet(**tags_data)
                tg_promo = record.get("tg_promo", "")
                embedding = self._embedding_client.get_embedding(tg_promo)

                article_record = ArticleRecord(
                    id=Database._generate_id(),
                    title=record["title"],
                    tags=tags,
                    tg_promo=tg_promo,
                    embedding=embedding,
                    url=record.get("url"),
                )
                self._database.insert_article(
                    article_record,
                    series_id=record.get("series_id"),
                    series_order=record.get("series_order"),
                    wp_post_id=record.get("wp_post_id"),
                )
                os.remove(fpath)
                retried += 1
                logger.info(f"入库重试成功: {record['title']}")
            except Exception as e:
                logger.warning(f"入库重试失败 ({fname}): {e}")

        return retried

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

        # 发布时段检查
        if self._settings.schedule.publish_window_enabled:
            from datetime import datetime
            now = datetime.now()
            start = self._settings.schedule.publish_window_start
            end = self._settings.schedule.publish_window_end
            if start <= end:
                in_window = start <= now.hour < end
            else:
                # 跨午夜窗口 (如 22:00 - 06:00)
                in_window = now.hour >= start or now.hour < end
            if not in_window:
                logger.info(
                    f"当前时间 {now.strftime('%H:%M')} 不在发布窗口 "
                    f"({start}:00-{end}:00)，跳过处理"
                )
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

        # 重试之前入库失败的记录
        if self._association_enabled:
            retried = self.retry_failed_ingests()
            if retried:
                logger.info(f"入库重试完成: {retried} 篇文章")

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
