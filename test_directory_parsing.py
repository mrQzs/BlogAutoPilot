#!/usr/bin/env python3
"""
测试目录解析功能
"""

import os
import sys

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import parse_directory_structure, scan_input_directory
from config import INPUT_FOLDER

def test_parse_directory_structure():
    """测试 parse_directory_structure() 函数"""
    print("=" * 60)
    print("测试 parse_directory_structure() 函数")
    print("=" * 60)

    test_cases = [
        # (filepath, expected_result_description)
        (f"{INPUT_FOLDER}/Magazine/Science_28/article.pdf", "应该返回正确的元数据"),
        (f"{INPUT_FOLDER}/Articles/Tech_10/test.md", "应该返回正确的元数据"),
        (f"{INPUT_FOLDER}/Books/Fiction_15/book.txt", "应该返回正确的元数据"),
        (f"{INPUT_FOLDER}/News/World_20/news.pdf", "应该返回正确的元数据"),
        (f"{INPUT_FOLDER}/root_file.pdf", "根目录文件，应该返回 None"),
        (f"{INPUT_FOLDER}/Magazine/Science/article.pdf", "格式错误（缺少数字），应该返回 None"),
        (f"{INPUT_FOLDER}/Unknown/Tech_10/file.pdf", "未知大类，应该返回 None"),
        (f"{INPUT_FOLDER}/Magazine/Science_0/file.pdf", "无效分类 ID（0），应该返回 None"),
        (f"{INPUT_FOLDER}/Magazine/Science_28/Sub/file.pdf", "层级过深，应该返回 None"),
    ]

    for filepath, description in test_cases:
        print(f"\n测试: {filepath}")
        print(f"预期: {description}")
        result = parse_directory_structure(filepath)
        if result:
            print(f"✅ 结果: {result}")
        else:
            print(f"⏭️ 结果: None (跳过)")

    print("\n" + "=" * 60)

def test_scan_input_directory():
    """测试 scan_input_directory() 函数"""
    print("\n" + "=" * 60)
    print("测试 scan_input_directory() 函数")
    print("=" * 60)

    # 创建一些测试文件
    test_files = [
        f"{INPUT_FOLDER}/Magazine/Science_28/test1.txt",
        f"{INPUT_FOLDER}/Articles/Tech_10/test2.txt",
        f"{INPUT_FOLDER}/root_test.txt",  # 应该被跳过
    ]

    print("\n创建测试文件...")
    for filepath in test_files:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            f.write("Test content")
        print(f"  创建: {filepath}")

    print("\n扫描目录...")
    file_list = scan_input_directory()

    print(f"\n找到 {len(file_list)} 个有效文件:")
    for file_info in file_list:
        print(f"\n  文件: {file_info['filename']}")
        print(f"  路径: {file_info['filepath']}")
        print(f"  元数据: {file_info['metadata']}")

    # 清理测试文件
    print("\n清理测试文件...")
    for filepath in test_files:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"  删除: {filepath}")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    test_parse_directory_structure()
    test_scan_input_directory()
    print("\n✅ 所有测试完成!")
