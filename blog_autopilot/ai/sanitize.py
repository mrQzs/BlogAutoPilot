"""输入/输出文本清洗"""

import logging
import re

logger = logging.getLogger("blog-autopilot")

# 控制字符正则（保留换行和制表符）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 句子结束标点
_SENTENCE_ENDS = frozenset("。！？.!?")

# AI 身份泄露检测模式（匹配常见 LLM 自我介绍）
_AI_IDENTITY_PATTERNS = [
    re.compile(r"I'?m\s+(Claude|ChatGPT|GPT|an?\s+AI)", re.IGNORECASE),
    re.compile(r"I\s+am\s+(Claude|ChatGPT|GPT|an?\s+AI)", re.IGNORECASE),
    re.compile(r"(made|created|developed|built)\s+by\s+(Anthropic|OpenAI|Google|Meta)", re.IGNORECASE),
    re.compile(r"as\s+an?\s+AI\s+(assistant|language\s+model|model)", re.IGNORECASE),
    re.compile(r"I\s+(cannot|can'?t|don'?t)\s+(actually|really)\s+(write|create|generate)", re.IGNORECASE),
    re.compile(r"作为(一个|一名)?(人工智能|AI)(助手|模型|语言模型)", re.IGNORECASE),
    re.compile(r"我是(Claude|ChatGPT|GPT|一个AI|人工智能)", re.IGNORECASE),
    re.compile(r"由\s*(Anthropic|OpenAI)\s*(开发|创建|制作)", re.IGNORECASE),
]


def check_ai_identity_leak(text: str) -> bool:
    """检测 AI 输出中是否包含身份泄露内容。返回 True 表示检测到泄露。"""
    for pattern in _AI_IDENTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            logger.warning(f"检测到 AI 身份泄露: '{match.group()}' (位置 {match.start()})")
            return True
    return False


def strip_ai_identity_lines(text: str) -> str:
    """移除包含 AI 身份泄露的行，返回清洗后的文本。"""
    lines = text.split("\n")
    cleaned = []
    removed = 0
    for line in lines:
        if check_ai_identity_leak(line):
            removed += 1
            continue
        cleaned.append(line)
    if removed:
        logger.warning(f"已移除 {removed} 行 AI 身份泄露内容")
    return "\n".join(cleaned)


def sanitize_input(text: str, max_length: int) -> str:
    """清洗输入文本：移除控制字符，在句子边界截断。"""
    # 移除控制字符（保留 \n \r \t）
    text = _CONTROL_CHAR_RE.sub("", text)
    if len(text) <= max_length:
        return text
    # 在 max_length 范围内找最后一个句子结束标点
    truncated = text[:max_length]
    for i in range(len(truncated) - 1, max(0, len(truncated) - 200), -1):
        if truncated[i] in _SENTENCE_ENDS:
            return truncated[: i + 1]
    # 找不到句子边界，硬截断
    return truncated
