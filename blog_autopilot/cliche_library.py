"""套话动态检测库 — 从历史审核数据中提取 AI 套话，生成检测库"""

import fcntl
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from blog_autopilot.config import Settings
from blog_autopilot.constants import (
    CLICHE_AUTO_REFRESH_HOURS,
    CLICHE_INJECT_LIMIT,
    CLICHE_MAX_PHRASES,
    CLICHE_MIN_FREQUENCY,
    CLICHE_MIN_REVIEWS,
)
from blog_autopilot.exceptions import ClicheLibraryError

logger = logging.getLogger("blog-autopilot")

# 套话库文件路径（项目根目录）
CLICHE_FILE = Path(__file__).parent.parent / "ai_cliches.json"

# 基础套话库文件路径（项目根目录）
BASELINE_FILE = Path(__file__).parent.parent / "cliche_baseline.json"

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
    """从 JSON 文件加载套话库，文件不存在时返回空列表，跳过格式错误的条目"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for item in data:
            if not isinstance(item, dict) or "phrase" not in item:
                continue
            try:
                entries.append(ClicheEntry(
                    phrase=item["phrase"],
                    frequency=int(item.get("frequency", 0)),
                    severity=item.get("severity", "medium"),
                ))
            except (TypeError, ValueError) as e:
                logger.warning(f"跳过格式错误的套话条目 {item!r}: {e}")
        return entries
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


# ── Part D: 基础套话库 + 合并 + 自动刷新 ──


def load_baseline_cliches(path: Path = BASELINE_FILE) -> list[ClicheEntry]:
    """加载基础套话库（frequency=0），文件不存在时返回空列表，跳过格式错误的条目"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for item in data:
            if not isinstance(item, dict) or "phrase" not in item:
                continue
            try:
                entries.append(ClicheEntry(
                    phrase=str(item["phrase"]),
                    frequency=0,
                    severity=str(item.get("severity", "medium")),
                ))
            except (TypeError, ValueError) as e:
                logger.warning(f"跳过格式错误的基础套话条目 {item!r}: {e}")
        return entries
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"基础套话库文件解析失败: {e}")
        return []


def load_merged_cliches(
    dynamic_path: Path = CLICHE_FILE,
    baseline_path: Path = BASELINE_FILE,
) -> list[ClicheEntry]:
    """
    合并基础+动态套话库，动态条目优先（按 phrase 去重）。

    返回按频率降序排列的列表（动态高频在前，基础库 frequency=0 在末尾）。
    """
    dynamic = load_cliche_library(path=dynamic_path)
    baseline = load_baseline_cliches(path=baseline_path)

    # 动态条目优先
    seen = {e.phrase for e in dynamic}
    merged = list(dynamic)
    for entry in baseline:
        if entry.phrase not in seen:
            merged.append(entry)
            seen.add(entry.phrase)

    # 按频率降序排列，相同频率按 phrase 稳定排序
    merged.sort(key=lambda e: (-e.frequency, e.phrase))

    return merged


def is_cliche_stale(path: Path = CLICHE_FILE, max_age_hours: int = CLICHE_AUTO_REFRESH_HOURS) -> bool:
    """检查动态套话库是否过期（文件不存在也视为过期）"""
    if not path.exists():
        return True
    try:
        mtime = path.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        return age_hours >= max_age_hours
    except OSError:
        return True


def auto_refresh_cliches(settings: Settings, database=None) -> None:
    """
    过期且 DB 可用时自动刷新动态套话库，静默失败。

    使用文件锁防止多进程并发刷新。

    Args:
        database: 可选的现有 Database 实例，避免创建冗余连接。
    """
    if not is_cliche_stale():
        return

    lock_path = CLICHE_FILE.parent / ".cliche_refresh.lock"
    try:
        lock_fd = open(lock_path, "a")
    except OSError:
        logger.debug("套话库自动刷新: 无法创建锁文件，跳过")
        return

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        lock_fd.close()
        logger.debug("套话库自动刷新: 其他进程正在刷新，跳过")
        return

    try:
        # 获得锁后再次检查是否过期（可能另一进程刚刷新完）
        if not is_cliche_stale():
            return
        updater = ClicheUpdater(settings, database=database)
        report = updater.update()
        logger.info(
            f"套话库自动刷新完成: {report.unique_phrases} 条套话"
        )
    except ClicheLibraryError as e:
        logger.debug(f"套话库自动刷新跳过: {e}")
    except Exception as e:
        logger.debug(f"套话库自动刷新失败: {e}")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


class ClicheUpdater:
    """套话库更新器，从数据库审核记录中提取并更新套话库"""

    def __init__(self, settings: Settings, database=None) -> None:
        if database is not None:
            self._database = database
        else:
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
