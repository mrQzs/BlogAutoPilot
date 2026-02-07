"""主流水线模块 — Pipeline 类"""

import logging
import os
import re
import shutil
import time

from blog_autopilot.ai_writer import AIWriter
from blog_autopilot.config import Settings
from blog_autopilot.constants import MAX_FILENAME_LENGTH, POLL_INTERVAL
from blog_autopilot.exceptions import (
    AIAPIError,
    AIResponseParseError,
    ExtractionError,
    TelegramError,
    WordPressError,
)
from blog_autopilot.extractor import extract_text_from_file
from blog_autopilot.models import FileTask, PipelineResult
from blog_autopilot.publisher import post_to_wordpress, test_wp_connection
from blog_autopilot.scanner import scan_input_directory
from blog_autopilot.telegram import send_to_telegram, test_tg_connection

logger = logging.getLogger("blog-autopilot")


class Pipeline:
    """主流水线，编排完整的文件处理流程"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._writer = AIWriter(settings.ai)

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

        # ② AI 生成文章
        try:
            article = self._writer.generate_blog_post(raw_text)
        except (AIAPIError, AIResponseParseError) as e:
            logger.error(f"跳过 {task.filename}: AI 生成内容失败 - {e}")
            return PipelineResult(
                filename=task.filename, success=False, error=str(e)
            )

        # ③ 发布到 WordPress
        try:
            blog_link = post_to_wordpress(
                title=article.title,
                content=article.html_body,
                settings=self._settings.wp,
                category_id=meta.category_id,
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

        # ④ 推广
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

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名，移除非法字符"""
        cleaned = re.sub(r'[\\/*?:"<>|]', "", name).strip()
        return cleaned[:MAX_FILENAME_LENGTH]

    def _archive_file(
        self,
        filepath: str,
        original_filename: str,
        article_title: str | None = None,
    ) -> None:
        """归档文件：如果有标题，就重命名为 [标题.后缀]"""
        processed_dir = self._settings.paths.processed_folder
        os.makedirs(processed_dir, exist_ok=True)

        _, ext = os.path.splitext(original_filename)

        if article_title:
            safe_title = self._sanitize_filename(article_title)
            new_name = f"{safe_title}{ext}"
        else:
            timestamp = int(time.time())
            new_name = f"{timestamp}_{original_filename}"

        dest = os.path.join(processed_dir, new_name)

        # 防止重名覆盖
        if os.path.exists(dest):
            timestamp = int(time.time())
            base = safe_title if article_title else original_filename
            new_name = f"{base}_{timestamp}{ext}"
            dest = os.path.join(processed_dir, new_name)

        try:
            shutil.move(filepath, dest)
            logger.info(f"已归档: {new_name}")
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
            try:
                result = self.process_file(task)
                if result.success:
                    processed += 1
                self._archive_file(
                    task.filepath, task.filename, result.title
                )
            except Exception as e:
                logger.error(
                    f"处理 {task.filename} 时发生异常: {e}", exc_info=True
                )
                self._archive_file(task.filepath, task.filename)

        return processed

    def run(self, once: bool = False) -> None:
        """主循环入口"""
        paths = self._settings.paths
        os.makedirs(paths.input_folder, exist_ok=True)
        os.makedirs(paths.processed_folder, exist_ok=True)

        logger.info("Blog Autopilot 启动!")
        logger.info(f"  监控目录: {os.path.abspath(paths.input_folder)}")
        logger.info(f"  归档目录: {os.path.abspath(paths.processed_folder)}")
        logger.info(
            f"  运行模式: {'单次' if once else f'持续监控 (每 {POLL_INTERVAL}s)'}"
        )

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
        print("\n连接测试\n" + "=" * 40)

        print("\n[1/2] WordPress...")
        wp_ok = test_wp_connection(self._settings.wp)

        print("\n[2/2] Telegram...")
        tg_ok = test_tg_connection(self._settings.tg)

        print("\n" + "=" * 40)
        print(f"WordPress: {'OK' if wp_ok else 'FAIL'}")
        print(f"Telegram:  {'OK' if tg_ok else 'FAIL'}")
        print("\nAI 模块测试请运行: python -m blog_autopilot.ai_writer <文件路径>")
