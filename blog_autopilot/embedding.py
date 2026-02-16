"""Embedding 模块 — OpenAI Embedding API 封装"""

import hashlib
import logging
from collections import OrderedDict

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from blog_autopilot.config import EmbeddingSettings
from blog_autopilot.constants import EMBEDDING_CACHE_SIZE
from blog_autopilot.exceptions import EmbeddingError

logger = logging.getLogger("blog-autopilot")


class EmbeddingClient:
    """OpenAI Embedding API 客户端，延迟初始化 + 内存缓存"""

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._client: OpenAI | None = None
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def client(self) -> OpenAI:
        """延迟创建 OpenAI client"""
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.api_key.get_secret_value(),
                base_url=self._settings.api_base,
            )
        return self._client

    @staticmethod
    def _text_hash(text: str) -> str:
        """计算文本 hash 作为缓存 key"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_get(self, text: str) -> list[float] | None:
        """从缓存获取 embedding"""
        key = self._text_hash(text)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache_hits += 1
            return self._cache[key]
        self._cache_misses += 1
        return None

    def _cache_put(self, text: str, embedding: list[float]) -> None:
        """将 embedding 存入缓存"""
        key = self._text_hash(text)
        self._cache[key] = embedding
        self._cache.move_to_end(key)
        # 超出容量时淘汰最旧的
        while len(self._cache) > EMBEDDING_CACHE_SIZE:
            self._cache.popitem(last=False)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def get_embedding(self, text: str) -> list[float]:
        """
        获取单条文本的 embedding 向量。

        抛出:
            ValueError: 空文本
            EmbeddingError: API 调用失败
        """
        if not text or not text.strip():
            raise ValueError("Embedding 输入文本不能为空")

        # 检查缓存
        cached = self._cache_get(text)
        if cached is not None:
            logger.debug("Embedding 缓存命中")
            return cached

        try:
            response = self.client.embeddings.create(
                input=text,
                model=self._settings.model,
                dimensions=self._settings.dimensions,
            )
            embedding = response.data[0].embedding
            usage = response.usage
            logger.info(
                f"Embedding 完成 | tokens: {usage.total_tokens} | "
                f"维度: {len(embedding)}"
            )

            self._cache_put(text, embedding)
            return embedding

        except ValueError:
            raise
        except Exception as e:
            raise EmbeddingError(f"Embedding API 调用失败: {e}") from e
