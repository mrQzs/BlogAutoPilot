#!/usr/bin/env python3
"""
集成测试：验证完整的文件处理流程
"""

import os
import sys

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import parse_directory_structure, scan_input_directory
from config import INPUT_FOLDER

def test_integration():
    """集成测试：创建测试目录结构并验证"""
    print("=" * 60)
    print("集成测试：验证目录结构和元数据提取")
    print("=" * 60)

    # 测试场景
    test_scenarios = [
        {
            'path': 'Magazine/Science_28',
            'expected': {
                'category_name': 'Magazine',
                'subcategory_name': 'Science',
                'category_id': 28,
                'hashtag': '#Magazine_Science'
            }
        },
        {
            'path': 'Articles/Tech_10',
            'expected': {
                'category_name': 'Articles',
                'subcategory_name': 'Tech',
                'category_id': 10,
                'hashtag': '#Articles_Tech'
            }
        },
        {
            'path': 'Books/Fiction_15',
            'expected': {
                'category_name': 'Books',
                'subcategory_name': 'Fiction',
                'category_id': 15,
                'hashtag': '#Books_Fiction'
            }
        },
        {
            'path': 'News/World_20',
            'expected': {
                'category_name': 'News',
                'subcategory_name': 'World',
                'category_id': 20,
                'hashtag': '#News_World'
            }
        },
    ]

    print("\n测试各种目录结构:")
    for scenario in test_scenarios:
        path = scenario['path']
        expected = scenario['expected']

        # 创建测试文件
        full_path = os.path.join(INPUT_FOLDER, path)
        os.makedirs(full_path, exist_ok=True)
        test_file = os.path.join(full_path, 'test.txt')

        with open(test_file, 'w') as f:
            f.write("Test content")

        # 解析目录结构
        result = parse_directory_structure(test_file)

        print(f"\n  路径: {path}")
        print(f"  预期: {expected}")
        print(f"  结果: {result}")

        # 验证结果
        if result == expected:
            print(f"  ✅ 通过")
        else:
            print(f"  ❌ 失败")

        # 清理测试文件
        os.remove(test_file)

    print("\n" + "=" * 60)
    print("测试跳过场景:")
    print("=" * 60)

    # 测试应该被跳过的场景
    skip_scenarios = [
        ('root_file.txt', '根目录文件'),
        ('Magazine/InvalidFormat/file.txt', '格式错误的目录'),
        ('InvalidCategory/Test_10/file.txt', '未知大类'),
    ]

    for filename, description in skip_scenarios:
        filepath = os.path.join(INPUT_FOLDER, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w') as f:
            f.write("Test content")

        result = parse_directory_structure(filepath)

        print(f"\n  场景: {description}")
        print(f"  路径: {filename}")
        print(f"  结果: {'✅ 正确跳过 (None)' if result is None else '❌ 未跳过'}")

        # 清理
        os.remove(filepath)

    print("\n" + "=" * 60)
    print("✅ 集成测试完成!")
    print("=" * 60)

if __name__ == "__main__":
    test_integration()
