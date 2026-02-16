"""Pydantic BaseSettings 配置模块，从 .env 读取所有凭据"""

import logging
from functools import lru_cache
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WordPressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WP_", extra="ignore")

    url: str = "https://wo.city/index.php?rest_route=/wp/v2/posts"
    user: str
    app_password: SecretStr
    target_category_id: int = 15

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("user")
    @classmethod
    def user_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user must not be empty")
        return v


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TG_", extra="ignore")

    bot_token: SecretStr
    channel_id: str = "@gooddayupday"


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    api_key: SecretStr
    api_base: str = "https://api.ikuncode.cc/v1"
    model_writer: str = "claude-opus-4-6"
    model_promo: str = "claude-haiku-4-5-20251001"
    model_writer_fallback: str = ""
    model_promo_fallback: str = ""
    writer_max_tokens: int = 200_000
    promo_max_tokens: int = 10_000
    default_headers: dict[str, str] = {"User-Agent": "MyBlogWriter/1.0"}
    model_cover_image: str = "dall-e-3"
    cover_image_enabled: bool = True
    cover_image_api_key: SecretStr | None = None
    cover_image_api_base: str = "https://api.dwyu.top/v1"
    quality_review_enabled: bool = True
    model_reviewer: str = ""
    reviewer_max_tokens: int = 4096
    quality_pass_threshold: int = 7
    quality_rewrite_threshold: int = 5

    @field_validator("api_base")
    @classmethod
    def api_base_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("api_base must start with http:// or https://")
        return v


class DatabaseSettings(BaseSettings):
    """PostgreSQL 连接配置"""
    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    url: SecretStr | None = None
    host: str = "localhost"
    port: int = 5432
    name: str = "blog_articles"
    user: str = ""
    password: SecretStr = SecretStr("")

    @field_validator("port")
    @classmethod
    def port_must_be_valid(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be in range 1-65535")
        return v

    @field_validator("host")
    @classmethod
    def host_must_be_non_empty_when_no_url(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("host must not be empty when url is not set")
        return v

    def get_dsn(self) -> str:
        """返回 PostgreSQL 连接字符串"""
        if self.url:
            return self.url.get_secret_value()
        return (
            f"postgresql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class EmbeddingSettings(BaseSettings):
    """OpenAI Embedding API 配置"""
    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    api_key: SecretStr = SecretStr("")
    api_base: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-large"
    dimensions: int = 3072

    @field_validator("dimensions")
    @classmethod
    def dimensions_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("dimensions must be positive")
        return v

    @field_validator("api_base")
    @classmethod
    def api_base_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("api_base must start with http:// or https://")
        return v


class ScheduleSettings(BaseSettings):
    """发布时段配置"""
    model_config = SettingsConfigDict(env_prefix="SCHEDULE_", extra="ignore")

    publish_window_enabled: bool = False
    publish_window_start: int = 8   # 开始小时 (0-23)
    publish_window_end: int = 22    # 结束小时 (0-23)

    @field_validator("publish_window_start", "publish_window_end")
    @classmethod
    def hour_must_be_valid(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("hour must be in range 0-23")
        return v


class PathSettings(BaseSettings):
    input_folder: str = "./input"
    processed_folder: str = "./processed"
    drafts_folder: str = "./drafts"


class Settings:
    """顶层配置，聚合所有子配置"""

    def __init__(
        self,
        wp: WordPressSettings | None = None,
        tg: TelegramSettings | None = None,
        ai: AISettings | None = None,
        paths: PathSettings | None = None,
        database: DatabaseSettings | None = None,
        embedding: EmbeddingSettings | None = None,
        schedule: ScheduleSettings | None = None,
    ) -> None:
        _env = ".env"
        self.wp = wp or WordPressSettings(_env_file=_env)
        self.tg = tg or TelegramSettings(_env_file=_env)
        self.ai = ai or AISettings(_env_file=_env)
        self.paths = paths or PathSettings()
        if database is not None:
            self.database = database
        else:
            try:
                self.database = DatabaseSettings(_env_file=_env)
            except Exception:
                self.database = DatabaseSettings()
        if embedding is not None:
            self.embedding = embedding
        else:
            try:
                self.embedding = EmbeddingSettings(_env_file=_env)
            except Exception:
                self.embedding = EmbeddingSettings()
        if schedule is not None:
            self.schedule = schedule
        else:
            try:
                self.schedule = ScheduleSettings(_env_file=_env)
            except Exception:
                self.schedule = ScheduleSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """延迟单例：首次调用时加载配置"""
    return Settings()


def setup_logging() -> logging.Logger:
    """配置并返回全局 logger"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("blog-autopilot")
