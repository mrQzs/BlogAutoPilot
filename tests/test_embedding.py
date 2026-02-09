"""测试 Embedding 模块"""

import pytest
from unittest.mock import MagicMock, patch

from blog_autopilot.embedding import EmbeddingClient
from blog_autopilot.exceptions import EmbeddingError


class TestEmbeddingClient:

    def test_lazy_client_init(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)
        assert client._client is None

    def test_get_embedding_success(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 3072
        mock_response.usage.total_tokens = 50

        mock_openai = MagicMock()
        mock_openai.embeddings.create.return_value = mock_response
        client._client = mock_openai

        result = client.get_embedding("测试文本")

        assert len(result) == 3072
        assert result[0] == 0.1
        mock_openai.embeddings.create.assert_called_once()

    def test_get_embedding_empty_text(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        with pytest.raises(ValueError, match="不能为空"):
            client.get_embedding("")

    def test_get_embedding_whitespace_text(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        with pytest.raises(ValueError, match="不能为空"):
            client.get_embedding("   ")

    def test_get_embedding_cache_hit(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.5] * 3072
        mock_response.usage.total_tokens = 30

        mock_openai = MagicMock()
        mock_openai.embeddings.create.return_value = mock_response
        client._client = mock_openai

        # 第一次调用
        result1 = client.get_embedding("缓存测试文本")
        # 第二次调用（应命中缓存）
        result2 = client.get_embedding("缓存测试文本")

        assert result1 == result2
        # API 只被调用了一次
        assert mock_openai.embeddings.create.call_count == 1
        assert client.cache_stats["hits"] == 1

    def test_get_embedding_api_error(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        mock_openai = MagicMock()
        mock_openai.embeddings.create.side_effect = Exception("API 超时")
        client._client = mock_openai

        with pytest.raises(EmbeddingError, match="API 调用失败"):
            client.get_embedding("测试文本")

    def test_cache_stats(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        stats = client.cache_stats
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0


class TestEmbeddingBatch:

    def test_batch_empty(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)
        assert client.get_embeddings_batch([]) == []

    def test_batch_success(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        mock_response = MagicMock()
        emb1 = MagicMock()
        emb1.embedding = [0.1] * 3072
        emb2 = MagicMock()
        emb2.embedding = [0.2] * 3072
        mock_response.data = [emb1, emb2]
        mock_response.usage.total_tokens = 100

        mock_openai = MagicMock()
        mock_openai.embeddings.create.return_value = mock_response
        client._client = mock_openai

        results = client.get_embeddings_batch(["文本1", "文本2"])

        assert len(results) == 2
        assert results[0][0] == 0.1
        assert results[1][0] == 0.2

    def test_batch_with_empty_text(self, embedding_settings):
        """批量处理中的空文本被跳过"""
        client = EmbeddingClient(embedding_settings)

        mock_response = MagicMock()
        emb1 = MagicMock()
        emb1.embedding = [0.3] * 3072
        mock_response.data = [emb1]
        mock_response.usage.total_tokens = 50

        mock_openai = MagicMock()
        mock_openai.embeddings.create.return_value = mock_response
        client._client = mock_openai

        results = client.get_embeddings_batch(["有效文本", "", ""])

        assert len(results) == 3
        assert len(results[0]) == 3072
        assert results[1] == []  # 空文本
        assert results[2] == []  # 空文本

    def test_batch_cache_interaction(self, embedding_settings):
        """批量处理利用缓存"""
        client = EmbeddingClient(embedding_settings)

        # 先缓存一个
        mock_response_single = MagicMock()
        mock_response_single.data = [MagicMock()]
        mock_response_single.data[0].embedding = [0.9] * 3072
        mock_response_single.usage.total_tokens = 20

        mock_openai = MagicMock()
        mock_openai.embeddings.create.return_value = mock_response_single
        client._client = mock_openai

        client.get_embedding("已缓存文本")

        # 批量处理，其中包含已缓存的文本
        mock_response_batch = MagicMock()
        emb = MagicMock()
        emb.embedding = [0.5] * 3072
        mock_response_batch.data = [emb]
        mock_response_batch.usage.total_tokens = 30
        mock_openai.embeddings.create.return_value = mock_response_batch

        results = client.get_embeddings_batch(["已缓存文本", "新文本"])

        assert len(results) == 2
        assert results[0][0] == 0.9  # 来自缓存
