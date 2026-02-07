#!/usr/bin/env python3
"""
完整工作流演示脚本
展示新的目录结构和标签系统如何工作
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import parse_directory_structure, scan_input_directory
from config import INPUT_FOLDER

def demo_workflow():
    """演示完整的工作流"""
    print("=" * 70)
    print("博客自动化系统 - 多级目录结构和标签系统演示")
    print("=" * 70)

    # 演示场景
    demo_files = [
        {
            'path': 'Magazine/Science_28/quantum_computing.pdf',
            'description': '科学杂志文章'
        },
        {
            'path': 'Articles/Tech_10/ai_trends.md',
            'description': '技术文章'
        },
        {
            'path': 'Books/Fiction_15/novel_review.txt',
            'description': '小说书评'
        },
        {
            'path': 'News/World_20/breaking_news.pdf',
            'description': '世界新闻'
        },
    ]

    print("\n📁 创建演示目录结构...")
    for item in demo_files:
        full_path = os.path.join(INPUT_FOLDER, item['path'])
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w') as f:
            f.write(f"Demo content for {item['description']}")
        print(f"  ✅ 创建: {item['path']}")

    print("\n" + "=" * 70)
    print("🔍 扫描并解析文件...")
    print("=" * 70)

    file_list = scan_input_directory()

    for i, file_info in enumerate(file_list, 1):
        filepath = file_info['filepath']
        filename = file_info['filename']
        metadata = file_info['metadata']

        print(f"\n文件 {i}: {filename}")
        print(f"  📂 完整路径: {filepath}")
        print(f"  📁 大类: {metadata['category_name']}")
        print(f"  📂 子类: {metadata['subcategory_name']}")
        print(f"  🆔 分类 ID: {metadata['category_id']}")
        print(f"  🏷️  Hashtag: {metadata['hashtag']}")
        print(f"  ➡️  WordPress: 将发布到分类 ID {metadata['category_id']}")
        print(f"  ➡️  Telegram: 推广文案将包含 {metadata['hashtag']}")

    print("\n" + "=" * 70)
    print("🧪 测试跳过场景...")
    print("=" * 70)

    # 创建应该被跳过的文件
    skip_files = [
        ('root_file.pdf', '根目录文件'),
        ('Magazine/InvalidFormat/file.pdf', '格式错误的目录'),
        ('InvalidCategory/Test_10/file.pdf', '未知大类'),
    ]

    for path, description in skip_files:
        full_path = os.path.join(INPUT_FOLDER, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w') as f:
            f.write("Test content")

        result = parse_directory_structure(full_path)
        status = "✅ 正确跳过" if result is None else "❌ 未跳过"
        print(f"\n  {description}: {status}")
        print(f"    路径: {path}")

    print("\n" + "=" * 70)
    print("🧹 清理演示文件...")
    print("=" * 70)

    # 清理所有演示文件
    all_files = demo_files + [(path, desc) for path, desc in skip_files]
    for item in all_files:
        path = item['path'] if isinstance(item, dict) else item[0]
        full_path = os.path.join(INPUT_FOLDER, path)
        if os.path.exists(full_path):
            os.remove(full_path)
            print(f"  🗑️  删除: {path}")

    print("\n" + "=" * 70)
    print("✅ 演示完成!")
    print("=" * 70)

    print("\n📝 使用说明:")
    print("  1. 创建目录: mkdir -p input/Magazine/Science_28")
    print("  2. 放置文件: cp article.pdf input/Magazine/Science_28/")
    print("  3. 运行处理: python3 main.py --once")
    print("  4. 查看结果: 文章发布到 WordPress 分类 28，Telegram 包含 #Magazine_Science")
    print("\n📚 详细文档: 查看 QUICK_START.md 和 IMPLEMENTATION_SUMMARY.md")

if __name__ == "__main__":
    demo_workflow()
