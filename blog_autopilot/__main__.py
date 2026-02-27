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

    # --recommend: 智能选题推荐
    if "--recommend" in sys.argv:
        settings = get_settings()
        top_n = 5
        if "--top" in sys.argv:
            idx = sys.argv.index("--top")
            if idx + 1 < len(sys.argv):
                top_n = int(sys.argv[idx + 1])
        from blog_autopilot.recommender import TopicRecommender
        recommender = TopicRecommender(settings)
        recommendations = recommender.recommend(top_n=top_n)
        print(recommender.format_output(recommendations))
        return

    # --tag-audit: 标签治理审计
    if "--tag-audit" in sys.argv:
        settings = get_settings()
        from blog_autopilot.tag_governance import TagAuditor
        auditor = TagAuditor(settings)
        report = auditor.audit()
        print(auditor.format_output(report))
        if "--json" in sys.argv:
            print(auditor.export_json(report))
        if "--auto-merge" in sys.argv:
            merged = auditor.merge_suggestions(report)
            if merged:
                print(f"\n已自动合并 {len(merged)} 条同义词:")
                for s in merged:
                    print(f"  {s.synonym} -> {s.canonical} (相似度: {s.similarity:.4f})")
            else:
                print("\n无需合并的同义词建议")
        return

    # --update-cliches: 更新套话检测库
    if "--update-cliches" in sys.argv:
        settings = get_settings()
        from blog_autopilot.cliche_library import ClicheUpdater
        updater = ClicheUpdater(settings)
        report = updater.update()
        print(updater.format_output(report))
        return

    # --backfill-summaries: 为缺少 summary 的旧文章生成摘要
    if "--backfill-summaries" in sys.argv:
        settings = get_settings()
        if not settings.database or not settings.database.user:
            print("错误: 数据库未配置，无法执行摘要回填")
            sys.exit(1)

        limit = 50
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            if idx + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[idx + 1])
                except ValueError:
                    print(f"错误: --limit 参数必须为整数，收到: {sys.argv[idx + 1]}")
                    sys.exit(1)

        from blog_autopilot.ai_writer import AIWriter
        from blog_autopilot.db import Database

        db = Database(settings.database)
        writer = AIWriter(settings.ai)
        articles = db.fetch_articles_without_summary(limit=limit)
        if not articles:
            print("所有文章均已有摘要，无需回填")
            return

        print(f"待回填摘要: {len(articles)} 篇")
        success = 0
        for art in articles:
            try:
                # 优先使用 content_excerpt，回退到 tg_promo
                content = art.get("content_excerpt") or art.get("tg_promo", "")
                if not content:
                    print(f"  跳过 [{art['id']}] {art['title']}: 无可用内容")
                    continue
                summary = writer.generate_summary(art["title"], content)
                db.update_article_summary(art["id"], summary)
                success += 1
                print(f"  [{success}] {art['title']}")
            except Exception as e:
                print(f"  失败 [{art['id']}] {art['title']}: {e}")
        print(f"\n回填完成: {success}/{len(articles)} 篇")
        return

    # --generate-survey: 综述文章生成 + 发布 + 推广
    if "--generate-survey" in sys.argv:
        settings = get_settings()
        from blog_autopilot.survey import SurveyGenerator
        from blog_autopilot.publisher import post_to_wordpress, ensure_wp_tags
        from blog_autopilot.telegram import send_to_telegram
        from blog_autopilot.ai_writer import AIWriter

        gen = SurveyGenerator(settings)
        candidates = gen.detect_candidates()
        if not candidates:
            print("未发现可生成综述的文章组")
            return
        print(gen.format_candidates(candidates))
        result = gen.generate(candidates[0])
        print(f"\n综述标题: {result.title}")
        print(f"源文章数: {result.source_count}")

        writer = AIWriter(settings.ai)

        # SEO 元数据提取
        seo = None
        wp_tag_ids = None
        try:
            seo = writer.extract_seo_metadata(result.title, result.html_body)
            if seo.wp_tags:
                wp_tag_ids = ensure_wp_tags(seo.wp_tags, settings.wp)
        except Exception as e:
            logger.warning(f"SEO 提取失败（不影响发布）: {e}")

        # 发布到 WordPress Featured 分类 (ID 39)
        pub = post_to_wordpress(
            title=result.title,
            content=result.html_body,
            settings=settings.wp,
            category_id=39,
            excerpt=seo.meta_description if seo else None,
            slug=seo.slug if seo else None,
            tag_ids=wp_tag_ids,
        )
        print(f"发布成功: {pub.url}")

        # 推广文案 + Telegram 推送
        try:
            promo_text = writer.generate_promo(
                result.title, result.html_body, hashtag="#综述"
            )
            send_to_telegram(promo_text, pub.url, settings.tg)
            print("Telegram 推送完成")
        except Exception as e:
            logger.warning(f"推广/推送失败（文章已发布）: {e}")

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
