"""标签注册表模块 — 词汇表约束 + WordPress 桥接"""

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path

from blog_autopilot.constants import TAG_REGISTRY_FUZZY_THRESHOLD
from blog_autopilot.models import TagSet

logger = logging.getLogger("blog-autopilot")

# 注册表文件路径
_REGISTRY_PATH = Path(__file__).parent.parent / "tag_registry.json"

# 缓存
_registry: dict | None = None
_registry_mtime: float = 0.0


def _load_registry() -> dict:
    """加载标签注册表（懒加载 + mtime 缓存失效）"""
    global _registry, _registry_mtime

    # 检查文件 mtime 来决定是否需要重新加载
    try:
        current_mtime = _REGISTRY_PATH.stat().st_mtime if _REGISTRY_PATH.exists() else 0.0
    except OSError:
        current_mtime = 0.0

    if _registry is not None and current_mtime == _registry_mtime:
        return _registry

    _registry = {}
    _registry_mtime = current_mtime

    if not _REGISTRY_PATH.exists():
        logger.warning(f"标签注册表文件不存在: {_REGISTRY_PATH}")
        return _registry

    try:
        with open(_REGISTRY_PATH, encoding="utf-8") as f:
            _registry = json.load(f)
        logger.info(
            f"标签注册表加载完成: {len(_registry)} 个层级"
        )
    except Exception as e:
        logger.warning(f"标签注册表加载失败: {e}")
        _registry = {}

    return _registry


def _invalidate_registry_cache() -> None:
    """清除注册表缓存，强制下次调用时重新加载"""
    global _registry, _registry_mtime
    _registry = None
    _registry_mtime = 0.0


def get_mode(level: str) -> str:
    """获取指定层级的模式（closed/semi_open/open）"""
    registry = _load_registry()
    entry = registry.get(level, {})
    return entry.get("mode", "open")


def get_allowed_values(level: str) -> list[str]:
    """获取指定层级的允许值列表"""
    registry = _load_registry()
    entry = registry.get(level, {})
    return entry.get("values", [])


def _fuzzy_match(
    value: str,
    candidates: list[str],
) -> tuple[str | None, float]:
    """
    模糊匹配：找到候选值中与 value 最相似的。

    返回 (best_match, similarity)。无候选时返回 (None, 0.0)。
    """
    if not candidates:
        return None, 0.0

    best_match = candidates[0]
    best_score = SequenceMatcher(None, value, candidates[0]).ratio()
    for candidate in candidates[1:]:
        score = SequenceMatcher(None, value, candidate).ratio()
        if score > best_score:
            best_score = score
            best_match = candidate
    return best_match, best_score


def validate_against_registry(
    level: str,
    value: str,
    similarity_threshold: float = TAG_REGISTRY_FUZZY_THRESHOLD,
) -> tuple[str, bool]:
    """
    根据注册表验证单个标签值。

    Args:
        level: 标签层级 (tag_magazine / tag_science / tag_topic / tag_content)
        value: 标签值
        similarity_threshold: 模糊匹配阈值

    Returns:
        (resolved_value, was_changed) — 解析后的值和是否被修正
    """
    registry = _load_registry()
    entry = registry.get(level)

    # 注册表中没有该层级配置 → 原样返回
    if not entry:
        return value, False

    mode = entry.get("mode", "open")
    allowed = entry.get("values", [])

    if mode == "open":
        return value, False

    # 精确匹配
    if value in allowed:
        return value, False

    # 模糊匹配
    best, score = _fuzzy_match(value, allowed)

    if mode == "closed":
        # closed 模式：必须修正到最近的候选值
        if best:
            if score < similarity_threshold:
                logger.warning(
                    f"标签注册表低置信度修正 [{level}]: "
                    f"'{value}' -> '{best}' (相似度 {score:.0%} < 阈值 {similarity_threshold:.0%})"
                )
            else:
                logger.info(
                    f"标签注册表修正 [{level}]: "
                    f"'{value}' -> '{best}' (相似度 {score:.0%})"
                )
            return best, True
        # 不可能走到这里（allowed 非空时 best 一定有值），但保险起见
        return value, False

    if mode == "semi_open":
        # semi_open 模式：相似度够高则修正，否则接受新值并记录
        if best and score >= similarity_threshold:
            logger.info(
                f"标签注册表修正 [{level}]: "
                f"'{value}' -> '{best}' (相似度 {score:.0%})"
            )
            return best, True
        # 新值：接受但记录日志供人工审查
        logger.info(
            f"标签注册表新值 [{level}]: '{value}' "
            f"(最近候选: '{best}', 相似度 {score:.0%})"
        )
        return value, False

    return value, False


def validate_tags_against_registry(tags: TagSet) -> TagSet:
    """
    对四级标签整体做注册表验证。

    Returns:
        修正后的 TagSet
    """
    registry = _load_registry()
    if not registry:
        return tags

    changes = {}
    for level in ("tag_magazine", "tag_science", "tag_topic", "tag_content"):
        original = getattr(tags, level)
        resolved, changed = validate_against_registry(level, original)
        changes[level] = resolved

    return TagSet(**changes)


def build_tagger_prompt_section() -> str:
    """
    从注册表动态生成「标签选择参考」提示词块。

    返回格式化的文本，可直接注入到 tagger system prompt 中。
    如果注册表未加载，返回空字符串。
    """
    registry = _load_registry()
    if not registry:
        return ""

    lines = []
    lines.append("═══════════════════════════")
    lines.append("  标签选择参考")
    lines.append("═══════════════════════════")
    lines.append("")

    level_labels = {
        "tag_magazine": ("一级", "杂志分类"),
        "tag_science": ("二级", "学科领域"),
        "tag_topic": ("三级", "具体主题"),
        "tag_content": ("四级", "内容概括，5字以内"),
    }

    for level, (num, desc) in level_labels.items():
        entry = registry.get(level, {})
        mode = entry.get("mode", "open")
        values = entry.get("values", [])

        if mode == "closed" and values:
            lines.append(f"{num} {level}（从以下值中选择，不可自定义）：")
            lines.append(f"  {' / '.join(values)}")
        elif mode == "semi_open" and values:
            lines.append(
                f"{num} {level}（优先从以下值中选择，"
                f"确实不匹配时可自定义）："
            )
            lines.append(f"  {' / '.join(values)}")
        elif mode == "open":
            lines.append(f"{num} {level}：{desc}")
        else:
            lines.append(f"{num} {level}：{desc}")

        lines.append("")

    return "\n".join(lines)


def derive_wp_tags_from_internal(tags: TagSet) -> list[str]:
    """
    从内部四级标签中提取需要同步到 WordPress 的标签名。

    只返回 wp_mapping=true 的层级的值。
    """
    registry = _load_registry()
    if not registry:
        return []

    wp_tags = []
    for level in ("tag_magazine", "tag_science", "tag_topic", "tag_content"):
        entry = registry.get(level, {})
        if entry.get("wp_mapping", False):
            value = getattr(tags, level)
            if value:
                wp_tags.append(value)

    return wp_tags
