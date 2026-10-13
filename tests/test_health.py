"""Health endpoints must not report readiness without PostgreSQL."""

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1 import health
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTGRES_USER", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", "https://oss.example.test")
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_liveness_does_not_require_postgres(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_engine_call() -> None:
        raise AssertionError("liveness must not query PostgreSQL")

    monkeypatch.setattr(health, "get_engine", unexpected_engine_call)

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_up_after_query(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    monkeypatch.setattr(health, "get_engine", lambda: engine)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {"postgres": "up"}}
    assert connection.execute.call_args.args[0].text == "SELECT 1"


def test_readiness_returns_503_when_postgres_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("unavailable"))
    monkeypatch.setattr(health, "get_engine", lambda: engine)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "dependencies": {"postgres": "down"}}
