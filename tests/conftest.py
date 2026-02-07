"""共享 fixtures"""

import os
import tempfile

import pytest

from blog_autopilot.config import (
    AISettings,
    PathSettings,
    Settings,
    TelegramSettings,
    WordPressSettings,
)
from blog_autopilot.models import CategoryMeta


@pytest.fixture
def tmp_dirs(tmp_path):
    """创建临时的 input / processed / drafts 目录"""
    input_dir = tmp_path / "input"
    processed_dir = tmp_path / "processed"
    drafts_dir = tmp_path / "drafts"
    input_dir.mkdir()
    processed_dir.mkdir()
    drafts_dir.mkdir()
    return {
        "input": str(input_dir),
        "processed": str(processed_dir),
        "drafts": str(drafts_dir),
    }
