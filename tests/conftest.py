"""Fixtures for isolated PostgreSQL integration tests."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.core.config import Settings


@pytest.fixture(scope="session")
def isolated_postgres_url() -> Iterator[URL]:
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 inside the Compose api container")

    settings = Settings.from_env()
    if settings.app_env != "dev" or settings.postgres_host != "postgres":
        pytest.fail("integration tests may create a database only in the dev Compose postgres")

    database_name = f"oss_test_{uuid4().hex}"
    admin_engine = create_engine(
        settings.sqlalchemy_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
            created = True
        yield settings.sqlalchemy_url.set(database=database_name)
    finally:
        if created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        admin_engine.dispose()
