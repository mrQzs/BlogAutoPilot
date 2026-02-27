"""HTML 标签匹配检查"""

import logging
import re

logger = logging.getLogger("blog-autopilot")

_CHECKED_TAGS = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                 "ul", "ol", "li", "blockquote", "pre", "table")


def _warn_unclosed_tags(html: str) -> None:
    """检查常见 HTML 标签的开闭数量是否匹配，不匹配时记录警告。"""
    for tag in _CHECKED_TAGS:
        opens = len(re.findall(rf"<{tag}[\s>]", html, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", html, re.IGNORECASE))
        if opens != closes:
            logger.warning(
                f"HTML 标签不匹配: <{tag}> 开启 {opens} 次, "
                f"关闭 {closes} 次"
            )
