"""标签同义词归一化模块"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("blog-autopilot")

# 同义词映射文件路径
_SYNONYMS_PATH = Path(__file__).parent.parent / "tag_synonyms.json"

# 缓存
_synonym_map: dict[str, str] | None = None


def _load_synonyms() -> dict[str, str]:
    """加载同义词映射（懒加载 + 缓存）"""
    global _synonym_map
    if _synonym_map is not None:
        return _synonym_map

    _synonym_map = {}
    if not _SYNONYMS_PATH.exists():
        return _synonym_map

    try:
        with open(_SYNONYMS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # 格式: {"canonical": ["synonym1", "synonym2", ...]}
        for canonical, synonyms in data.items():
            for syn in synonyms:
                _synonym_map[syn] = canonical
            # canonical 也映射到自身（确保一致性）
            _synonym_map[canonical] = canonical
        logger.info(f"标签同义词加载完成: {len(_synonym_map)} 条映射")
    except Exception as e:
        logger.warning(f"标签同义词加载失败: {e}")
        _synonym_map = {}

    return _synonym_map


def normalize_synonym(tag: str) -> str:
    """将标签归一化为标准形式"""
    mapping = _load_synonyms()
    return mapping.get(tag, tag)


def reload_synonyms() -> None:
    """强制重新加载同义词映射"""
    global _synonym_map
    _synonym_map = None
    _load_synonyms()
