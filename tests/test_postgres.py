"""Real PostgreSQL checks run only against an isolated Compose test database."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from app.api.v1 import health
from app.main import create_app


def test_postgres_connection(isolated_postgres_url: URL) -> None:
    engine = create_engine(isolated_postgres_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_baseline_migration_applies_to_empty_database(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )

    engine = create_engine(isolated_postgres_url)
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == []

        command.upgrade(config, "head")
        command.upgrade(config, "head")

        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            tables = inspect(connection).get_table_names()
        assert revision == "0001_foundation"
        assert tables == ["alembic_version"]
    finally:
        engine.dispose()


def test_readiness_returns_503_on_real_connection_refusal(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    unreachable_engine = create_engine(
        isolated_postgres_url.set(host="127.0.0.1", port=1),
        connect_args={"connect_timeout": 1},
    )
    monkeypatch.setattr(health, "get_engine", lambda: unreachable_engine)
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health/live").status_code == 200
            response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "dependencies": {"postgres": "down"}}
    finally:
        unreachable_engine.dispose()
