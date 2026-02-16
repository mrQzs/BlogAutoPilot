"""测试标签同义词归一化"""

import json
import pytest

import blog_autopilot.tag_normalizer as mod
from blog_autopilot.tag_normalizer import normalize_synonym


class TestTagNormalizer:

    def setup_method(self):
        """每个测试前重置缓存"""
        mod._synonym_map = None

    def test_normalize_known_synonym(self, tmp_path):
        """已知同义词应归一化为标准形式"""
        synonyms = {"AI应用": ["人工智能应用", "AI 应用"]}
        syn_file = tmp_path / "tag_synonyms.json"
        syn_file.write_text(json.dumps(synonyms, ensure_ascii=False))

        mod._synonym_map = None
        original_path = mod._SYNONYMS_PATH
        mod._SYNONYMS_PATH = syn_file

        try:
            result = normalize_synonym("人工智能应用")
            assert result == "AI应用"
        finally:
            mod._SYNONYMS_PATH = original_path

    def test_normalize_unknown_tag(self):
        """未知标签应原样返回"""
        mod._synonym_map = {}
        result = normalize_synonym("完全未知的标签")
        assert result == "完全未知的标签"

    def test_normalize_canonical_tag(self, tmp_path):
        """标准形式本身也应正确映射"""
        synonyms = {"AI应用": ["人工智能应用"]}
        syn_file = tmp_path / "tag_synonyms.json"
        syn_file.write_text(json.dumps(synonyms, ensure_ascii=False))

        mod._synonym_map = None
        original_path = mod._SYNONYMS_PATH
        mod._SYNONYMS_PATH = syn_file

        try:
            result = normalize_synonym("AI应用")
            assert result == "AI应用"
        finally:
            mod._SYNONYMS_PATH = original_path

    def test_missing_file(self, tmp_path):
        """同义词文件不存在时应返回空映射"""
        mod._synonym_map = None
        original_path = mod._SYNONYMS_PATH
        mod._SYNONYMS_PATH = tmp_path / "nonexistent.json"

        try:
            result = normalize_synonym("任何标签")
            assert result == "任何标签"
        finally:
            mod._SYNONYMS_PATH = original_path
