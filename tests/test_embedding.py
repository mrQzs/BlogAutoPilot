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

    def test_get_embedding_api_error(self, embedding_settings):
        client = EmbeddingClient(embedding_settings)

        mock_openai = MagicMock()
        mock_openai.embeddings.create.side_effect = Exception("API 超时")
        client._client = mock_openai

        with pytest.raises(EmbeddingError, match="API 调用失败"):
            client.get_embedding("测试文本")
