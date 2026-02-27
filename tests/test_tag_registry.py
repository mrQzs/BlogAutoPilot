"""测试标签注册表模块"""

import json
import pytest
from unittest.mock import patch

from blog_autopilot.models import TagSet
from blog_autopilot.tag_registry import (
    _fuzzy_match,
    _invalidate_registry_cache,
    _load_registry,
    build_tagger_prompt_section,
    derive_wp_tags_from_internal,
    get_allowed_values,
    get_mode,
    validate_against_registry,
    validate_tags_against_registry,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清除注册表缓存"""
    _invalidate_registry_cache()
    yield
    _invalidate_registry_cache()


# ── 加载与缓存 ──


class TestRegistryLoading:

    def test_load_registry_success(self):
        """正常加载注册表"""
        registry = _load_registry()
        assert "tag_magazine" in registry
        assert "tag_science" in registry
        assert "tag_topic" in registry
        assert "tag_content" in registry

    def test_load_registry_cached(self):
        """二次调用使用缓存"""
        r1 = _load_registry()
        r2 = _load_registry()
        assert r1 is r2

    def test_invalidate_cache(self):
        """清除缓存后重新加载"""
        r1 = _load_registry()
        _invalidate_registry_cache()
        r2 = _load_registry()
        assert r1 is not r2

    def test_missing_file_graceful(self, tmp_path):
        """注册表文件不存在时优雅降级"""
        import blog_autopilot.tag_registry as mod
        original = mod._REGISTRY_PATH
        mod._REGISTRY_PATH = tmp_path / "nonexistent.json"
        _invalidate_registry_cache()
        try:
            registry = _load_registry()
            assert registry == {}
        finally:
            mod._REGISTRY_PATH = original
            _invalidate_registry_cache()

    def test_mtime_cache_invalidation(self, tmp_path):
        """文件修改后缓存自动失效"""
        import time
        import blog_autopilot.tag_registry as mod
        original = mod._REGISTRY_PATH

        registry_file = tmp_path / "test_registry.json"
        registry_file.write_text(
            '{"tag_magazine": {"mode": "closed", "values": ["A"]}}',
            encoding="utf-8",
        )
        mod._REGISTRY_PATH = registry_file
        _invalidate_registry_cache()
        try:
            r1 = _load_registry()
            assert r1.get("tag_magazine", {}).get("values") == ["A"]

            # 修改文件（确保 mtime 不同）
            time.sleep(0.05)
            registry_file.write_text(
                '{"tag_magazine": {"mode": "closed", "values": ["A", "B"]}}',
                encoding="utf-8",
            )

            # 不手动 invalidate，mtime 变化应自动触发重新加载
            r2 = _load_registry()
            assert r2.get("tag_magazine", {}).get("values") == ["A", "B"]
            assert r1 is not r2
        finally:
            mod._REGISTRY_PATH = original
            _invalidate_registry_cache()


# ── 访问器 ──


class TestAccessors:

    def test_get_mode_closed(self):
        assert get_mode("tag_magazine") == "closed"

    def test_get_mode_semi_open(self):
        assert get_mode("tag_topic") == "semi_open"

    def test_get_mode_open(self):
        assert get_mode("tag_content") == "open"

    def test_get_mode_unknown_level(self):
        """未注册的层级默认返回 open"""
        assert get_mode("tag_nonexistent") == "open"

    def test_get_allowed_values(self):
        values = get_allowed_values("tag_magazine")
        assert "技术周刊" in values
        assert "生活杂谈" in values

    def test_get_allowed_values_empty(self):
        """open 模式下无 values 列表"""
        # tag_content 是 open 模式，没有 values
        values = get_allowed_values("tag_content")
        assert values == []


# ── 模糊匹配 ──


class TestFuzzyMatch:

    def test_exact_match(self):
        best, score = _fuzzy_match("技术周刊", ["技术周刊", "生活杂谈"])
        assert best == "技术周刊"
        assert score == 1.0

    def test_close_match(self):
        best, score = _fuzzy_match("技术月刊", ["技术周刊", "生活杂谈"])
        assert best == "技术周刊"
        assert score > 0.5

    def test_no_candidates(self):
        best, score = _fuzzy_match("技术周刊", [])
        assert best is None
        assert score == 0.0


# ── closed 模式验证 ──


class TestClosedMode:

    def test_exact_match(self):
        value, changed = validate_against_registry("tag_magazine", "技术周刊")
        assert value == "技术周刊"
        assert changed is False

    def test_fuzzy_correction(self):
        """相近的值被修正到最接近的候选值"""
        value, changed = validate_against_registry("tag_magazine", "技术月刊")
        assert value == "技术周刊"
        assert changed is True

    def test_forced_correction_on_low_similarity(self):
        """完全不同的值也被修正（closed 模式总是修正）"""
        value, changed = validate_against_registry("tag_magazine", "量子物理")
        assert value in get_allowed_values("tag_magazine")
        assert changed is True

    def test_low_similarity_logs_warning(self, caplog):
        """低相似度修正时记录 warning 日志"""
        import logging
        with caplog.at_level(logging.WARNING, logger="blog-autopilot"):
            value, changed = validate_against_registry("tag_magazine", "量子物理")
        assert changed is True
        assert any("低置信度修正" in r.message for r in caplog.records)

    def test_science_exact(self):
        value, changed = validate_against_registry("tag_science", "AI应用")
        assert value == "AI应用"
        assert changed is False

    def test_science_fuzzy(self):
        value, changed = validate_against_registry("tag_science", "人工智能应用")
        # 应该匹配到 AI应用 或 其他最近的
        assert changed is True
        assert value in get_allowed_values("tag_science")


# ── semi_open 模式验证 ──


class TestSemiOpenMode:

    def test_exact_match(self):
        value, changed = validate_against_registry("tag_topic", "API开发")
        assert value == "API开发"
        assert changed is False

    def test_fuzzy_match_close_enough(self):
        """足够相似的值被修正到候选值"""
        value, changed = validate_against_registry(
            "tag_topic", "API开发技术", similarity_threshold=0.6,
        )
        assert value == "API开发"
        assert changed is True

    def test_new_value_accepted(self):
        """完全不同的新值被接受（semi_open）"""
        value, changed = validate_against_registry(
            "tag_topic", "区块链安全",
        )
        assert value == "区块链安全"
        assert changed is False


# ── open 模式验证 ──


class TestOpenMode:

    def test_passthrough(self):
        """open 模式直接放行"""
        value, changed = validate_against_registry("tag_content", "任意值")
        assert value == "任意值"
        assert changed is False


# ── 整体验证 ──


class TestValidateTagsAgainstRegistry:

    def test_all_valid(self):
        tags = TagSet(
            tag_magazine="技术周刊",
            tag_science="AI应用",
            tag_topic="API开发",
            tag_content="Claude自动化",
        )
        result = validate_tags_against_registry(tags)
        assert result.tag_magazine == "技术周刊"
        assert result.tag_science == "AI应用"
        assert result.tag_topic == "API开发"
        assert result.tag_content == "Claude自动化"

    def test_corrections_applied(self):
        tags = TagSet(
            tag_magazine="技术月刊",  # 应被修正
            tag_science="AI应用",
            tag_topic="API开发",
            tag_content="自动化",
        )
        result = validate_tags_against_registry(tags)
        assert result.tag_magazine == "技术周刊"  # closed: 被修正
        assert result.tag_content == "自动化"  # open: 不修正

    def test_empty_registry_passthrough(self, tmp_path):
        """注册表为空时原样返回"""
        import blog_autopilot.tag_registry as mod
        original = mod._REGISTRY_PATH
        mod._REGISTRY_PATH = tmp_path / "nonexistent.json"
        _invalidate_registry_cache()
        try:
            tags = TagSet(
                tag_magazine="随便写",
                tag_science="随便写",
                tag_topic="随便写",
                tag_content="随便写",
            )
            result = validate_tags_against_registry(tags)
            assert result == tags
        finally:
            mod._REGISTRY_PATH = original
            _invalidate_registry_cache()


# ── 提示词生成 ──


class TestBuildTaggerPromptSection:

    def test_prompt_section_generated(self):
        text = build_tagger_prompt_section()
        assert "标签选择参考" in text
        assert "一级" in text
        assert "二级" in text
        assert "三级" in text
        assert "四级" in text

    def test_closed_mode_text(self):
        text = build_tagger_prompt_section()
        assert "不可自定义" in text

    def test_semi_open_mode_text(self):
        text = build_tagger_prompt_section()
        assert "确实不匹配时可自定义" in text

    def test_includes_values(self):
        text = build_tagger_prompt_section()
        assert "技术周刊" in text
        assert "AI应用" in text

    def test_empty_registry(self, tmp_path):
        """注册表为空时返回空字符串"""
        import blog_autopilot.tag_registry as mod
        original = mod._REGISTRY_PATH
        mod._REGISTRY_PATH = tmp_path / "nonexistent.json"
        _invalidate_registry_cache()
        try:
            text = build_tagger_prompt_section()
            assert text == ""
        finally:
            mod._REGISTRY_PATH = original
            _invalidate_registry_cache()


# ── WordPress 桥接 ──


class TestDeriveWpTags:

    def test_wp_mapping_true(self):
        tags = TagSet(
            tag_magazine="技术周刊",
            tag_science="AI应用",
            tag_topic="API开发",
            tag_content="Claude自动化",
        )
        wp_tags = derive_wp_tags_from_internal(tags)
        # tag_magazine 和 tag_science 有 wp_mapping=true
        assert "技术周刊" in wp_tags
        assert "AI应用" in wp_tags
        # tag_topic 和 tag_content 的 wp_mapping=false
        assert "API开发" not in wp_tags
        assert "Claude自动化" not in wp_tags

    def test_wp_mapping_count(self):
        tags = TagSet(
            tag_magazine="生活杂谈",
            tag_science="心理学",
            tag_topic="认知偏误",
            tag_content="确认偏误",
        )
        wp_tags = derive_wp_tags_from_internal(tags)
        assert len(wp_tags) == 2

    def test_empty_registry(self, tmp_path):
        """注册表不存在时返回空列表"""
        import blog_autopilot.tag_registry as mod
        original = mod._REGISTRY_PATH
        mod._REGISTRY_PATH = tmp_path / "nonexistent.json"
        _invalidate_registry_cache()
        try:
            tags = TagSet("a", "b", "c", "d")
            assert derive_wp_tags_from_internal(tags) == []
        finally:
            mod._REGISTRY_PATH = original
            _invalidate_registry_cache()
