"""自定义异常层级"""


class BlogAutoPilotError(Exception):
    """基础异常"""


class ConfigError(BlogAutoPilotError):
    """配置错误（缺失环境变量、格式不合法等）"""


class ExtractionError(BlogAutoPilotError):
    """文本提取失败"""


class AIAPIError(BlogAutoPilotError):
    """AI API 调用失败"""


class AIResponseParseError(BlogAutoPilotError):
    """AI 返回内容解析失败"""


class WordPressError(BlogAutoPilotError):
    """WordPress 发布失败"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TelegramError(BlogAutoPilotError):
    """Telegram 推送失败"""
