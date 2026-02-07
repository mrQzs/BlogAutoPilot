"""Pydantic BaseSettings 配置模块，从 .env 读取所有凭据"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WordPressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WP_")

    url: str = "https://wo.city/index.php?rest_route=/wp/v2/posts"
    user: str
    app_password: SecretStr
    target_category_id: int = 15


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TG_")

    bot_token: SecretStr
    channel_id: str = "@gooddayupday"


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_")

    api_key: SecretStr
    api_base: str = "https://api.ikuncode.cc/v1"
    model_writer: str = "claude-opus-4-5-20251101"
    model_promo: str = "claude-haiku-4-5-20251001"
    writer_max_tokens: int = 200_000
    promo_max_tokens: int = 10_000
    default_headers: dict[str, str] = {"User-Agent": "MyBlogWriter/1.0"}


class PathSettings(BaseSettings):
    input_folder: str = "./input"
    processed_folder: str = "./processed"
    drafts_folder: str = "./drafts"


class Settings(BaseSettings):
    """顶层配置，聚合所有子配置"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    wp: WordPressSettings = None  # type: ignore[assignment]
    tg: TelegramSettings = None  # type: ignore[assignment]
    ai: AISettings = None  # type: ignore[assignment]
    paths: PathSettings = PathSettings()

    def model_post_init(self, __context) -> None:
        if self.wp is None:
            self.wp = WordPressSettings(_env_file=".env")
        if self.tg is None:
            self.tg = TelegramSettings(_env_file=".env")
        if self.ai is None:
            self.ai = AISettings(_env_file=".env")


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
