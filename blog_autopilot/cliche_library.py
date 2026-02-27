"""套话动态检测库 — 从历史审核数据中提取 AI 套话，生成检测库"""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from blog_autopilot.config import Settings
from blog_autopilot.constants import (
    CLICHE_INJECT_LIMIT,
    CLICHE_MAX_PHRASES,
    CLICHE_MIN_FREQUENCY,
    CLICHE_MIN_REVIEWS,
)
from blog_autopilot.exceptions import ClicheLibraryError

logger = logging.getLogger("blog-autopilot")

# 套话库文件路径（项目根目录）
CLICHE_FILE = Path(__file__).parent.parent / "ai_cliches.json"

# 从 description 中提取引号内套话短语的正则
_PHRASE_RE = re.compile("[「『\u201c\u2018](.*?)[」』\u201d\u2019]")


@dataclass(frozen=True)
class ClicheEntry:
    """单条套话记录"""
    phrase: str
    frequency: int
    severity: str  # 出现最多的严重级别


@dataclass(frozen=True)
class ClicheReport:
    """套话库更新报告"""
    review_count: int
    issue_count: int
    unique_phrases: int
    entries: tuple[ClicheEntry, ...]


def extract_phrases(description: str) -> list[str]:
    """从审核问题描述中提取引号内的套话短语"""
    matches = _PHRASE_RE.findall(description)
    # 过滤太短或太长的匹配（噪音）
    return [m.strip() for m in matches if 2 <= len(m.strip()) <= 30]


def build_cliche_entries(issues: list[dict]) -> list[ClicheEntry]:
    """
    从 ai_cliche 问题列表中提取套话短语，统计频率和严重级别。

    返回按频率降序排列的 ClicheEntry 列表。
    """
    phrase_counter: Counter[str] = Counter()
    severity_counter: dict[str, Counter[str]] = {}

    for issue in issues:
        desc = issue.get("description", "")
        sev = issue.get("severity", "medium")
        phrases = extract_phrases(desc)
        for phrase in phrases:
            phrase_counter[phrase] += 1
            if phrase not in severity_counter:
                severity_counter[phrase] = Counter()
            severity_counter[phrase][sev] += 1

    entries = []
    for phrase, freq in phrase_counter.most_common(CLICHE_MAX_PHRASES):
        if freq < CLICHE_MIN_FREQUENCY:
            break
        top_sev = severity_counter[phrase].most_common(1)[0][0]
        entries.append(ClicheEntry(
            phrase=phrase, frequency=freq, severity=top_sev,
        ))
    return entries


def save_cliche_library(entries: list[ClicheEntry], path: Path = CLICHE_FILE) -> None:
    """将套话条目保存为 JSON 文件"""
    data = [
        {"phrase": e.phrase, "frequency": e.frequency, "severity": e.severity}
        for e in entries
    ]
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"套话库已保存: {len(data)} 条 → {path}")


def load_cliche_library(path: Path = CLICHE_FILE) -> list[ClicheEntry]:
    """从 JSON 文件加载套话库，文件不存在时返回空列表"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            ClicheEntry(
                phrase=item["phrase"],
                frequency=item["frequency"],
                severity=item["severity"],
            )
            for item in data
            if isinstance(item, dict) and "phrase" in item
        ]
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"套话库文件解析失败: {e}")
        return []


def format_cliche_context(entries: list[ClicheEntry]) -> str:
    """
    将套话库格式化为提示词注入段落。

    注入到 review_system.txt 和 writer_system.txt 末尾。
    """
    if not entries:
        return ""
    phrases = [e.phrase for e in entries[:CLICHE_INJECT_LIMIT]]
    lines = [
        "",
        "═══════════════════════════",
        "  动态套话检测库（从历史审核中自动提取）",
        "═══════════════════════════",
        "",
        "以下套话在历史审核中被反复标记为 AI 痕迹，请特别注意避免：",
        "「" + "」「".join(phrases) + "」",
    ]
    return "\n".join(lines)


class ClicheUpdater:
    """套话库更新器，从数据库审核记录中提取并更新套话库"""

    def __init__(self, settings: Settings) -> None:
        from blog_autopilot.db import Database
        self._database = Database(settings.database)

    def update(self) -> ClicheReport:
        """
        从 article_reviews 提取 ai_cliche 问题，构建套话库。

        抛出:
            ClicheLibraryError: 审核记录不足
        """
        if not self._database.test_connection():
            raise ClicheLibraryError("数据库连接失败")

        issues = self._database.fetch_cliche_issues()
        if len(issues) < CLICHE_MIN_REVIEWS:
            raise ClicheLibraryError(
                f"审核记录不足: {len(issues)} 条 ai_cliche 问题 "
                f"(最少需要 {CLICHE_MIN_REVIEWS} 条)"
            )

        entries = build_cliche_entries(issues)
        save_cliche_library(entries)

        return ClicheReport(
            review_count=len(issues),
            issue_count=len(issues),
            unique_phrases=len(entries),
            entries=tuple(entries),
        )

    @staticmethod
    def format_output(report: ClicheReport) -> str:
        """终端友好输出"""
        lines = [
            f"套话库更新完成",
            f"  审核问题数: {report.issue_count}",
            f"  提取套话数: {report.unique_phrases}",
            "",
        ]
        if report.entries:
            lines.append("Top 套话（按频率排序）:")
            for e in report.entries[:20]:
                lines.append(
                    f"  [{e.severity}] 「{e.phrase}」 × {e.frequency}"
                )
        else:
            lines.append("未提取到满足频率阈值的套话短语。")
        return "\n".join(lines)
