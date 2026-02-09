"""python -m blog_autopilot 入口"""

import os
import sys

from blog_autopilot.config import get_settings, setup_logging
from blog_autopilot.pipeline import Pipeline


def main() -> None:
    logger = setup_logging()

    # --test: 测试外部连接
    if "--test" in sys.argv:
        settings = get_settings()
        Pipeline(settings).run_test()
        return

    # --test-db: 仅测试数据库连接
    if "--test-db" in sys.argv:
        settings = get_settings()
        from blog_autopilot.db import Database
        db = Database(settings.database)
        if db.test_connection():
            print("数据库连接成功")
            count = db.count_articles()
            print(f"当前文章数: {count}")
        else:
            print("数据库连接失败")
            sys.exit(1)
        return

    # --init-db: 初始化数据库 schema
    if "--init-db" in sys.argv:
        settings = get_settings()
        from blog_autopilot.db import Database
        db = Database(settings.database)
        db.initialize_schema()
        print("数据库 schema 初始化完成")
        return

    # --ingest: 入库文件或目录
    if "--ingest" in sys.argv:
        idx = sys.argv.index("--ingest")
        if idx + 1 >= len(sys.argv):
            print("用法: python -m blog_autopilot --ingest <文件或目录>")
            sys.exit(1)
        target = sys.argv[idx + 1]

        # 可选的 --ingest-url 参数
        url = None
        if "--ingest-url" in sys.argv:
            url_idx = sys.argv.index("--ingest-url")
            if url_idx + 1 < len(sys.argv):
                url = sys.argv[url_idx + 1]

        settings = get_settings()
        from blog_autopilot.ingest import ArticleIngestor
        ingestor = ArticleIngestor(settings)

        if os.path.isdir(target):
            ingestor.ingest_from_directory(target)
        elif os.path.isfile(target):
            from blog_autopilot.extractor import extract_text_from_file
            content = extract_text_from_file(target)
            result = ingestor.ingest_article(content=content, url=url)
            if result.success:
                print(f"入库成功: {result.article_id} - {result.title}")
            else:
                print(f"入库失败: {result.error}")
                sys.exit(1)
        else:
            print(f"路径不存在: {target}")
            sys.exit(1)
        return

    # --once / 默认: 正常运行流水线
    once_mode = "--once" in sys.argv

    try:
        settings = get_settings()
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        sys.exit(1)

    Pipeline(settings).run(once=once_mode)


if __name__ == "__main__":
    main()
