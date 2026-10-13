"""Isolated PostgreSQL migration and retention checks; never touches oss_control."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User
from app.modules.tickets.models import (
    ConnectionSnapshot,
    LookupCleanupAudit,
    SubscriberSnapshot,
    Ticket,
)


def test_cleanup_migration_preserves_ticket_and_limits_deletion(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0005_netup_groups")
    engine = create_engine(isolated_postgres_url)
    try:
        with Session(engine, expire_on_commit=False) as db:
            user = User(
                login=f"synthetic-{uuid4().hex}",
                display_name="Synthetic",
                role="operator",
                password_hash=hash_password("Synthetic-test-only-password-42!"),
            )
            db.add(user)
            db.flush()
            old = datetime.now(UTC) - timedelta(days=2)

            def snapshot(when: datetime) -> SubscriberSnapshot:
                value = SubscriberSnapshot(
                    account_no="synthetic-account",
                    source="MOCK",
                    fetched_at=when,
                    expires_at=when + timedelta(minutes=15),
                    requested_by=user.id,
                    contracts=[],
                    services=[],
                )
                db.add(value)
                db.flush()
                db.add(ConnectionSnapshot(subscriber_snapshot_id=value.id, access_type="UNKNOWN"))
                db.flush()
                return value

            removable = snapshot(old)
            locked = snapshot(old)
            retained = snapshot(old)
            fresh = snapshot(datetime.now(UTC))
            db.add(
                Ticket(
                    account_no=retained.account_no,
                    subscriber_snapshot_id=retained.id,
                    description="Synthetic only",
                    status="OPEN",
                    priority="NORMAL",
                    created_by=user.id,
                    client_request_id=uuid4(),
                    version=1,
                )
            )
            db.commit()

        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM subscriber_snapshots WHERE id=:id FOR KEY SHARE"),
                {"id": locked.id},
            )
            with engine.begin() as maintenance:
                assert maintenance.scalar(text("SELECT purge_expired_lookup_snapshots(500)")) == 1
        with Session(engine) as db:
            assert db.get(SubscriberSnapshot, removable.id) is None
            assert db.get(SubscriberSnapshot, locked.id) is not None
            assert db.get(SubscriberSnapshot, retained.id) is not None
            assert db.get(SubscriberSnapshot, fresh.id) is not None
            assert db.scalar(select(func.count()).select_from(LookupCleanupAudit)) == 1
            assert db.scalar(select(LookupCleanupAudit.deleted_count)) == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ConnectionSnapshot)
                    .where(ConnectionSnapshot.subscriber_snapshot_id == removable.id)
                )
                == 0
            )

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM subscriber_snapshots WHERE id=:id"), {"id": locked.id}
                )
        # Once the competing key-share transaction ends, only the procedure can remove it.
        with engine.begin() as maintenance:
            assert maintenance.scalar(text("SELECT purge_expired_lookup_snapshots(500)")) == 1

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM subscriber_snapshots WHERE id=:id"), {"id": retained.id}
                )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM tickets WHERE subscriber_snapshot_id=:id"),
                    {"id": retained.id},
                )
                == 1
            )
    finally:
        engine.dispose()
