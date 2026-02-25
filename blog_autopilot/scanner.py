"""目录扫描 + 路径解析模块"""

import json
import logging
import os

from blog_autopilot.constants import ALLOWED_CATEGORIES, SUBCATEGORY_DIR_PATTERN

# 尝试从 categories.json 加载大类列表，失败则回退到常量
_CATEGORIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "categories.json"
)


def _load_categories_config() -> dict:
    """从 categories.json 加载完整分类配置，失败时返回空字典"""
    try:
        with open(_CATEGORIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_allowed_categories() -> tuple[str, ...]:
    """从 categories.json 加载允许的大类，失败时回退到 constants"""
    data = _load_categories_config()
    if data:
        return tuple(k for k in data if not k.startswith("_"))
    return ALLOWED_CATEGORIES


def _find_bot_token(category_name: str, subcategory_name: str) -> str | None:
    """从 categories.json 查找分类对应的 bot_token"""
    data = _load_categories_config()
    subs = data.get(category_name)
    if not isinstance(subs, list):
        return None
    for sub in subs:
        if sub.get("name") == subcategory_name:
            return sub.get("bot_token")
    return None
from blog_autopilot.models import CategoryMeta, FileTask

logger = logging.getLogger("blog-autopilot")


def parse_directory_structure(
    filepath: str, input_folder: str
) -> CategoryMeta | None:
    """
    解析文件路径，提取分类信息。

    返回 CategoryMeta 或 None（格式不符时）。
    """
    try:
        rel_path = os.path.relpath(filepath, input_folder)
        dir_path = os.path.dirname(rel_path)

        if not dir_path or dir_path == ".":
            filename = os.path.basename(filepath)
            logger.warning(f"跳过根目录文件: {filename}")
            return None

        parts = dir_path.split(os.sep)

        if len(parts) != 2:
            logger.warning(f"跳过格式错误的目录: {dir_path}")
            return None

        category_name = parts[0]
        subcategory_dir = parts[1]

        if category_name not in _load_allowed_categories():
            logger.warning(f"跳过未知大类: {category_name}")
            return None

        match = SUBCATEGORY_DIR_PATTERN.match(subcategory_dir)
        if not match:
            logger.warning(f"跳过格式错误的目录: {dir_path}")
            return None

        subcategory_name = match.group(1)
        category_id = int(match.group(2))

        if category_id <= 0:
            logger.warning(
                f"跳过无效的分类 ID: {category_id} in {dir_path}"
            )
            return None

        hashtag = f"#{category_name}_{subcategory_name}"
        tg_bot_token = _find_bot_token(category_name, subcategory_name)

        return CategoryMeta(
            category_name=category_name,
            subcategory_name=subcategory_name,
            category_id=category_id,
            hashtag=hashtag,
            tg_bot_token=tg_bot_token,
        )

    except Exception as e:
        logger.error(f"解析目录结构时出错: {e}")
        return None


def scan_input_directory(input_folder: str) -> list[FileTask]:
    """
    递归扫描 input 目录，返回所有有效文件及其元数据。
    """
    file_list: list[FileTask] = []

    for root, _dirs, files in os.walk(input_folder):
        for filename in files:
            if filename.startswith("."):
                continue

            filepath = os.path.join(root, filename)
            metadata = parse_directory_structure(filepath, input_folder)

            if metadata is None:
                continue

            file_list.append(
                FileTask(
                    filepath=filepath,
                    filename=filename,
                    metadata=metadata,
                )
            )

    return file_list
