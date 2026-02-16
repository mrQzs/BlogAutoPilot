"""测试配置模块"""

import pytest
from pydantic import ValidationError

from blog_autopilot.config import (
    AISettings,
    DatabaseSettings,
    EmbeddingSettings,
    WordPressSettings,
    ScheduleSettings,
    Settings,
)


class TestWordPressSettings:

    def test_valid_settings(self):
        s = WordPressSettings(
            url="https://test.wp/wp-json/wp/v2/posts",
            user="admin",
            app_password="secret",
        )
        assert s.user == "admin"

    def test_invalid_url(self):
        with pytest.raises(ValidationError):
            WordPressSettings(
                url="not-a-url",
                user="admin",
                app_password="secret",
            )

    def test_empty_user(self):
        with pytest.raises(ValidationError):
            WordPressSettings(
                url="https://test.wp/wp-json/wp/v2/posts",
                user="",
                app_password="secret",
            )


class TestDatabaseSettings:

    def test_valid_settings(self):
        s = DatabaseSettings(
            host="localhost",
            port=5432,
            name="testdb",
            user="testuser",
            password="testpass",
        )
        assert s.port == 5432

    def test_invalid_port_too_high(self):
        with pytest.raises(ValidationError):
            DatabaseSettings(
                host="localhost",
                port=70000,
                name="testdb",
                user="testuser",
                password="testpass",
            )

    def test_invalid_port_zero(self):
        with pytest.raises(ValidationError):
            DatabaseSettings(
                host="localhost",
                port=0,
                name="testdb",
                user="testuser",
                password="testpass",
            )

    def test_get_dsn_with_url(self):
        s = DatabaseSettings(url="postgresql://user:pass@host/db")
        assert s.get_dsn() == "postgresql://user:pass@host/db"

    def test_get_dsn_without_url(self):
        s = DatabaseSettings(
            host="myhost",
            port=5433,
            name="mydb",
            user="myuser",
            password="mypass",
        )
        dsn = s.get_dsn()
        assert "myhost" in dsn
        assert "5433" in dsn
        assert "mydb" in dsn


class TestAISettings:

    def test_valid_settings(self):
        s = AISettings(
            api_key="test-key",
            api_base="https://api.test.com/v1",
        )
        assert s.model_writer == "claude-opus-4-6"

    def test_invalid_api_base(self):
        with pytest.raises(ValidationError):
            AISettings(
                api_key="test-key",
                api_base="not-a-url",
            )

    def test_fallback_models_default_empty(self):
        s = AISettings(
            api_key="test-key",
            api_base="https://api.test.com/v1",
        )
        assert s.model_writer_fallback == ""
        assert s.model_promo_fallback == ""


class TestEmbeddingSettings:

    def test_valid_settings(self):
        s = EmbeddingSettings(
            api_key="test-key",
            api_base="https://api.test.com/v1",
            dimensions=1536,
        )
        assert s.dimensions == 1536

    def test_invalid_api_base(self):
        with pytest.raises(ValidationError):
            EmbeddingSettings(
                api_key="test-key",
                api_base="ftp://invalid",
            )


class TestScheduleSettings:

    def test_defaults(self):
        s = ScheduleSettings()
        assert s.publish_window_enabled is False
        assert s.publish_window_start == 8
        assert s.publish_window_end == 22

    def test_custom_window(self):
        s = ScheduleSettings(
            publish_window_enabled=True,
            publish_window_start=10,
            publish_window_end=20,
        )
        assert s.publish_window_enabled is True

    def test_invalid_hour_negative(self):
        with pytest.raises(ValidationError):
            ScheduleSettings(publish_window_start=-1)

    def test_invalid_hour_too_high(self):
        with pytest.raises(ValidationError):
            ScheduleSettings(publish_window_end=24)


class TestSettings:

    def test_aggregation(self):
        s = Settings(
            wp=WordPressSettings(
                url="https://test.wp/wp-json/wp/v2/posts",
                user="admin",
                app_password="secret",
            ),
            tg=None,
            ai=AISettings(api_key="k", api_base="https://api.test.com/v1"),
        )
        assert s.wp.user == "admin"
