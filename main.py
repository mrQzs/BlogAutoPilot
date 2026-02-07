"""
🔄 自动化主流水线
监控 input 目录，自动完成: 提取 → AI写作 → 发布 → 推广 → 归档

用法:
    python main.py              # 启动持续监控模式
    python main.py --once       # 只处理一次就退出（适合 cron 调度）
    python main.py --test       # 测试所有连接
"""

import os
import sys
import time
import shutil
import re

from config import INPUT_FOLDER, PROCESSED_FOLDER, logger
from extract_text import extract_text_from_file
from ai_writer import generate_blog_post, generate_promo
from publish_wp import post_to_wordpress, test_wp_connection
from push_telegram import send_to_telegram, test_tg_connection

# 监控间隔（秒）
POLL_INTERVAL = 600  # 10 分钟

# 允许的大类列表
ALLOWED_CATEGORIES = ['Articles', 'Books', 'Magazine', 'News']


def parse_directory_structure(filepath: str) -> dict | None:
    """
    解析文件路径，提取分类信息

    参数:
        filepath: 文件完整路径
    返回:
        包含分类信息的字典，格式错误时返回 None
        {
            'category_name': 'Magazine',      # 大类
            'subcategory_name': 'Science',    # 子类
            'category_id': 28,                # 分类 ID
            'hashtag': '#Magazine_Science'    # hashtag
        }
    """
    try:
        # 获取相对于 INPUT_FOLDER 的路径
        rel_path = os.path.relpath(filepath, INPUT_FOLDER)

        # 获取目录部分
        dir_path = os.path.dirname(rel_path)

        # 如果是根目录文件（没有子目录），跳过
        if not dir_path or dir_path == '.':
            filename = os.path.basename(filepath)
            logger.warning(f"⏭️ 跳过根目录文件: {filename}")
            return None

        # 分割路径
        parts = dir_path.split(os.sep)

        # 验证路径层级是否为 2（大类/子类）
        if len(parts) != 2:
            logger.warning(f"⏭️ 跳过格式错误的目录: {dir_path}")
            return None

        category_name = parts[0]
        subcategory_dir = parts[1]

        # 验证大类是否在允许列表中
        if category_name not in ALLOWED_CATEGORIES:
            logger.warning(f"⏭️ 跳过未知大类: {category_name}")
            return None

        # 使用正则表达式解析子类目录名（格式：子类名_数字）
        match = re.match(r'^(.+)_(\d+)$', subcategory_dir)
        if not match:
            logger.warning(f"⏭️ 跳过格式错误的目录: {dir_path}")
            return None

        subcategory_name = match.group(1)
        category_id = int(match.group(2))

        # 验证分类 ID 是否有效
        if category_id <= 0:
            logger.warning(f"⏭️ 跳过无效的分类 ID: {category_id} in {dir_path}")
            return None

        # 构造 hashtag
        hashtag = f"#{category_name}_{subcategory_name}"

        return {
            'category_name': category_name,
            'subcategory_name': subcategory_name,
            'category_id': category_id,
            'hashtag': hashtag
        }

    except Exception as e:
        logger.error(f"解析目录结构时出错: {e}")
        return None


def scan_input_directory() -> list[dict]:
    """
    递归扫描 input 目录，返回所有有效文件及其元数据

    返回:
        文件列表，每个元素包含 filepath, filename, metadata
        [
            {
                'filepath': '/root/blog-autopilot/input/Magazine/Science_28/article.pdf',
                'filename': 'article.pdf',
                'metadata': {
                    'category_name': 'Magazine',
                    'subcategory_name': 'Science',
                    'category_id': 28,
                    'hashtag': '#Magazine_Science'
                }
            },
            ...
        ]
    """
    file_list = []

    # 递归遍历目录
    for root, dirs, files in os.walk(INPUT_FOLDER):
        for filename in files:
            # 跳过隐藏文件
            if filename.startswith('.'):
                continue

            filepath = os.path.join(root, filename)

            # 解析目录结构
            metadata = parse_directory_structure(filepath)

            # 如果返回 None，跳过该文件
            if metadata is None:
                continue

            # 添加到结果列表
            file_list.append({
                'filepath': filepath,
                'filename': filename,
                'metadata': metadata
            })

    return file_list


def process_file(filepath: str, filename: str, metadata: dict | None = None):
    """
    处理单个文件的完整流水线

    参数:
        filepath: 文件完整路径
        filename: 文件名
        metadata: 文件元数据（包含分类 ID 和 hashtag），None 表示使用默认值
    返回:
        (success: bool, title: str | None) - 是否成功和文章标题
    """
    logger.info(f"\n{'='*50}")
    logger.info(f"📄 开始处理: {filename}")

    # 输出分类信息
    if metadata:
        logger.info(f"📁 分类: {metadata['category_name']}/{metadata['subcategory_name']} (ID: {metadata['category_id']})")
        logger.info(f"🏷️ Hashtag: {metadata['hashtag']}")

    logger.info(f"{'='*50}")

    # ① 提取文本
    raw_text = extract_text_from_file(filepath)
    if not raw_text:
        logger.warning(f"⏭️ 跳过 {filename}: 内容为空或无法读取")
        return False, None  # <--- 修改：返回 None

    # ② AI 生成文章
    title, blog_html = generate_blog_post(raw_text)
    if not title or not blog_html:
        logger.error(f"⏭️ 跳过 {filename}: AI 生成内容失败")
        return False, None  # <--- 修改：返回 None

    # ③ 发布到 WordPress
    category_id = metadata['category_id'] if metadata else None
    blog_link = post_to_wordpress(title, blog_html, category_id=category_id)
    if not blog_link:
        logger.error(f"⏭️ {filename}: WordPress 发布失败")
        _save_draft(filename, title, blog_html)
        return False, title # <--- 即使发布失败，但AI生成成功了，我们也可以用标题归档

    # ④ 推广
    hashtag = metadata['hashtag'] if metadata else None
    promo_text = generate_promo(title, blog_html, hashtag=hashtag)
    send_to_telegram(promo_text, blog_link)

    logger.info(f"🎉 {filename} 处理完成! → {blog_link}")
    return True, title  # <--- 修改：返回标题


def _save_draft(filename: str, title: str, html: str):
    """发布失败时, 把草稿保存到本地"""
    draft_dir = "./drafts"
    os.makedirs(draft_dir, exist_ok=True)
    draft_path = os.path.join(draft_dir, f"{filename}.html")

    with open(draft_path, 'w', encoding='utf-8') as f:
        f.write(f"<!-- 标题: {title} -->\n{html}")

    logger.info(f"💾 草稿已保存到: {draft_path}")


def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符 (如 / \ : * ? " < > |)"""
    # 替换非法字符为空格，去掉首尾空格
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    # 限制长度避免过长
    return cleaned[:100]

def archive_file(filepath: str, original_filename: str, article_title: str = None):
    """归档文件：如果有标题，就重命名为 [标题.后缀]"""
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)
    
    # 获取原文件后缀 (如 .pdf, .docx)
    _, ext = os.path.splitext(original_filename)

    if article_title:
        # 如果有文章标题，使用标题作为文件名
        safe_title = sanitize_filename(article_title)
        new_name = f"{safe_title}{ext}"
    else:
        # 如果处理失败没标题，使用时间戳+原名
        timestamp = int(time.time())
        new_name = f"{timestamp}_{original_filename}"

    dest = os.path.join(PROCESSED_FOLDER, new_name)
    
    # 防止重名覆盖：如果目标文件已存在，追加时间戳
    if os.path.exists(dest):
        timestamp = int(time.time())
        new_name = f"{safe_title if article_title else original_filename}_{timestamp}{ext}"
        dest = os.path.join(PROCESSED_FOLDER, new_name)

    try:
        shutil.move(filepath, dest)
        logger.info(f"📦 已归档: {new_name}")
    except Exception as e:
        logger.error(f"❌ 归档失败: {e}")


def scan_and_process():
    """扫描 input 目录并处理所有文件（支持多级目录结构）"""
    os.makedirs(INPUT_FOLDER, exist_ok=True)

    # 使用新的递归扫描函数
    file_list = scan_input_directory()

    if not file_list:
        return 0

    logger.info(f"📂 发现 {len(file_list)} 个文件待处理")
    processed = 0

    for file_info in sorted(file_list, key=lambda x: x['filepath']):
        filepath = file_info['filepath']
        filename = file_info['filename']
        metadata = file_info['metadata']
        article_title = None

        try:
            # 传递元数据到处理函数
            success, article_title = process_file(filepath, filename, metadata)
            if success:
                processed += 1
        except Exception as e:
            logger.error(f"💥 处理 {filename} 时发生异常: {e}", exc_info=True)

        # 归档时传入标题
        archive_file(filepath, filename, article_title)

    return processed


def run_test():
    """测试所有外部连接"""
    print("\n🔧 连接测试\n" + "="*40)

    print("\n[1/2] WordPress...")
    wp_ok = test_wp_connection()

    print("\n[2/2] Telegram...")
    tg_ok = test_tg_connection()

    print("\n" + "="*40)
    print(f"WordPress: {'✅ OK' if wp_ok else '❌ FAIL'}")
    print(f"Telegram:  {'✅ OK' if tg_ok else '❌ FAIL'}")
    print(f"\n💡 AI 模块测试请运行: python ai_writer.py <文件路径>")


def main():
    # 参数解析
    if "--test" in sys.argv:
        run_test()
        return

    once_mode = "--once" in sys.argv

    os.makedirs(INPUT_FOLDER, exist_ok=True)
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)

    logger.info("🚀 Blog Autopilot 启动!")
    logger.info(f"   监控目录: {os.path.abspath(INPUT_FOLDER)}")
    logger.info(f"   归档目录: {os.path.abspath(PROCESSED_FOLDER)}")
    logger.info(f"   运行模式: {'单次' if once_mode else f'持续监控 (每 {POLL_INTERVAL}s)'}")

    if once_mode:
        count = scan_and_process()
        logger.info(f"✅ 单次处理完成, 共处理 {count} 篇文章")
    else:
        while True:
            try:
                scan_and_process()
            except KeyboardInterrupt:
                logger.info("\n👋 收到中断信号, 退出...")
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

