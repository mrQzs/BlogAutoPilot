"""输入文本清洗"""

import re

# 控制字符正则（保留换行和制表符）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 句子结束标点
_SENTENCE_ENDS = frozenset("。！？.!?")


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
