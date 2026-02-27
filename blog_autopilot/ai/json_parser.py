"""通用 JSON 解析/修复引擎"""

import json
import logging
import re

from blog_autopilot.exceptions import AIResponseParseError

logger = logging.getLogger("blog-autopilot")


def _escape_newlines_in_json_strings(text: str) -> str:
    """将 JSON 字符串值内的原始换行符转义为 \\n"""
    result = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == '\\' and in_string:
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch == '\n':
            result.append('\\n')
        elif in_string and ch == '\r':
            result.append('\\r')
        else:
            result.append(ch)
    return ''.join(result)


def _repair_truncated_json(text: str) -> str | None:
    """
    尝试修复被截断的 JSON 字符串。

    常见场景：AI 输出被 max_tokens 截断，导致数组或字符串未闭合。
    策略：截断到最后一个完整的值，然后补齐缺失的闭合符号。
    """
    # 去掉末尾不完整的字符串值（如 "有组织犯 被截断）
    # 找到最后一个完整的引号对
    cleaned = text.rstrip()

    # 如果末尾是未闭合的字符串，截断到上一个完整的值
    # 例如: ["a", "b", "未完成  →  ["a", "b"
    last_quote = cleaned.rfind('"')
    if last_quote > 0:
        # 检查这个引号之后是否有 ] 或 }，如果没有说明被截断了
        after = cleaned[last_quote + 1:].strip()
        if after and after[0] not in ']},':
            # 引号后面跟的不是闭合符，说明这个引号是值的开头，截断它
            cleaned = cleaned[:last_quote].rstrip().rstrip(',')

    # 统计未闭合的括号
    stack = []
    in_string = False
    escape = False
    for ch in cleaned:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == '[':
            stack.pop()

    if not stack:
        return None  # 没有未闭合的括号，不需要修复

    # 清理末尾的逗号（JSON 不允许 trailing comma）
    cleaned = cleaned.rstrip().rstrip(',')

    # 补齐闭合符号
    closing = []
    for opener in reversed(stack):
        closing.append(']' if opener == '[' else '}')

    return cleaned + ''.join(closing)


def _parse_json_response(
    response_text: str,
    validate_fn,
    error_prefix: str,
) -> dict:
    """
    通用 JSON 解析：处理 AI 返回的各种格式变体。

    尝试顺序：
    1. 直接解析
    2. 提取 markdown 代码块中的 JSON
    3. 提取第一个 { 到最后一个 } 之间的子串

    抛出:
        AIResponseParseError: 解析失败或缺少必需字段
    """
    raw_text = response_text.strip()

    # 尝试 0: 先用原始文本直接解析（避免 _escape_newlines 污染正常 JSON）
    try:
        data = json.loads(raw_text)
        validate_fn(data)
        return data
    except (json.JSONDecodeError, AIResponseParseError):
        pass

    # 修复 JSON 字符串值内的原始换行符（AI 常见问题）
    text = _escape_newlines_in_json_strings(raw_text)

    # 尝试 1: 修复换行符后直接解析
    try:
        data = json.loads(text)
        validate_fn(data)
        return data
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 markdown 代码块
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        inner = code_block.group(1).strip()
        try:
            data = json.loads(inner)
            validate_fn(data)
            return data
        except json.JSONDecodeError:
            pass
        # 代码块内 JSON 可能被截断，尝试修复
        cb_brace = inner.find("{")
        if cb_brace != -1:
            repaired = _repair_truncated_json(inner[cb_brace:])
            if repaired:
                try:
                    data = json.loads(repaired)
                    validate_fn(data)
                    logger.warning("JSON 被截断（代码块内），已自动修复")
                    return data
                except (json.JSONDecodeError, AIResponseParseError):
                    pass

    # 尝试 3: 提取 { ... } 子串
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            data = json.loads(text[first_brace:last_brace + 1])
            validate_fn(data)
            return data
        except json.JSONDecodeError:
            pass

    # 尝试 4: 修复被截断的 JSON（AI 输出被 max_tokens 截断时常见）
    if first_brace != -1:
        truncated = text[first_brace:]
        repaired = _repair_truncated_json(truncated)
        if repaired:
            try:
                data = json.loads(repaired)
                validate_fn(data)
                logger.warning("JSON 被截断，已自动修复")
                return data
            except (json.JSONDecodeError, AIResponseParseError):
                pass

    # 尝试 5: 用原始文本（未经换行符修复）重试 {…} 提取
    # _escape_newlines_in_json_strings 遇到未转义引号时会污染状态，
    # 此步用原始文本绕过
    raw_first = raw_text.find("{")
    raw_last = raw_text.rfind("}")
    if raw_first != -1 and raw_last > raw_first:
        try:
            data = json.loads(raw_text[raw_first:raw_last + 1])
            validate_fn(data)
            return data
        except (json.JSONDecodeError, AIResponseParseError):
            pass

    raise AIResponseParseError(
        f"{error_prefix}。响应内容前 200 字符: "
        f"{text[:200]}"
    )
