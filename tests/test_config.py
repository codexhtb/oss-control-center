"""Environment validation and safe PostgreSQL URL construction."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.security import ensure_non_root_runtime
from app.db import engine as db_engine


def test_settings_build_postgres_url_without_losing_special_characters() -> None:
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "oss_control",
            "POSTGRES_USER": "oss_control",
            "POSTGRES_PASSWORD": "a:@/special-password",
            "READINESS_TIMEOUT_SECONDS": "2",
            "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
            "AUTH_HASH_KEY": "test-only-key-with-at-least-32-bytes",
        }
    )

    assert settings.sqlalchemy_url.drivername == "postgresql+psycopg"
    assert settings.sqlalchemy_url.password == "a:@/special-password"
    assert settings.readiness_timeout_seconds == 2
    assert "special-password" not in repr(settings)


@pytest.mark.parametrize("timeout", ["0", "6", "not-a-number"])
def test_readiness_timeout_is_bounded(timeout: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                "POSTGRES_USER": "oss",
                "POSTGRES_PASSWORD": "secret",
                "READINESS_TIMEOUT_SECONDS": timeout,
                "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
                "AUTH_HASH_KEY": "test-only-key-with-at-least-32-bytes",
            }
        )


def test_missing_password_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                "POSTGRES_USER": "oss",
                "POSTGRES_PASSWORD": "",
                "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
                "AUTH_HASH_KEY": "test-only-key-with-at-least-32-bytes",
            }
        )


def test_engine_configures_bounded_postgres_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env(
        {
            "POSTGRES_USER": "oss",
            "POSTGRES_PASSWORD": "test-only",
            "READINESS_TIMEOUT_SECONDS": "2",
            "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
            "AUTH_HASH_KEY": "test-only-key-with-at-least-32-bytes",
        }
    )
    create_engine = MagicMock()
    monkeypatch.setattr(db_engine, "get_settings", lambda: settings)
    monkeypatch.setattr(db_engine, "create_engine", create_engine)
    db_engine.get_engine.cache_clear()
    try:
        db_engine.get_engine()
        options = create_engine.call_args.kwargs
        assert options["pool_timeout"] == 2
        assert options["connect_args"]["connect_timeout"] == 2
        assert options["connect_args"]["options"] == "-c statement_timeout=2000"
    finally:
        db_engine.get_engine.cache_clear()


def test_api_rejects_root_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.security.os.geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="must not run as root"):
        ensure_non_root_runtime()


def test_http_auth_requires_explicit_dev_loopback() -> None:
    values = {
        "POSTGRES_USER": "oss",
        "POSTGRES_PASSWORD": "test-only",
        "AUTH_PUBLIC_ORIGIN": "http://127.0.0.1:8000",
        "AUTH_HASH_KEY": "test-only-key-with-at-least-32-bytes",
    }
    with pytest.raises(ValidationError):
        Settings.from_env(values)
    assert not Settings.from_env({**values, "AUTH_ALLOW_HTTP_LOOPBACK": "true"}).secure_cookie
    with pytest.raises(ValidationError):
        Settings.from_env({**values, "APP_ENV": "production", "AUTH_ALLOW_HTTP_LOOPBACK": "true"})
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                **values,
                "AUTH_PUBLIC_ORIGIN": "http://0.0.0.0:8000",
                "AUTH_ALLOW_HTTP_LOOPBACK": "true",
            }
        )


def test_short_auth_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                "POSTGRES_USER": "oss",
                "POSTGRES_PASSWORD": "test-only",
                "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
                "AUTH_HASH_KEY": "short",
            }
        )
