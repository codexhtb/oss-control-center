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
from app.modules.tickets.models import (
    ConnectionSnapshot,
    LookupCleanupAudit,
    SubscriberSnapshot,
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
        user_id = uuid4()
        removable, locked, retained, fresh = (uuid4() for _ in range(4))
        old = datetime.now(UTC) - timedelta(days=2)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app_users "
                    "(id,login,display_name,role,password_hash,is_active) "
                    "VALUES (:id,:login,'Synthetic','operator',:hash,true)"
                ),
                {
                    "id": user_id,
                    "login": f"synthetic-{uuid4().hex}",
                    "hash": hash_password("Synthetic-test-only-password-42!"),
                },
            )
            for item_id, fetched in (
                (removable, old),
                (locked, old),
                (retained, old),
                (fresh, datetime.now(UTC)),
            ):
                connection.execute(
                    text(
                        "INSERT INTO subscriber_snapshots "
                        "(id,account_no,source,fetched_at,requested_by,expires_at,"
                        "contracts,services) "
                        "VALUES (:id,'synthetic-account','MOCK',:fetched,:user,:expires,"
                        "'[]'::jsonb,'[]'::jsonb)"
                    ),
                    {
                        "id": item_id,
                        "fetched": fetched,
                        "user": user_id,
                        "expires": fetched + timedelta(minutes=15),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO connection_snapshots "
                        "(id,subscriber_snapshot_id,access_type) "
                        "VALUES (:id,:parent,'UNKNOWN')"
                    ),
                    {"id": uuid4(), "parent": item_id},
                )
            connection.execute(
                text(
                    "INSERT INTO tickets "
                    "(id,account_no,subscriber_snapshot_id,description,status,priority,"
                    "created_by,client_request_id,version) "
                    "VALUES (:id,'synthetic-account',:snapshot,'Synthetic only','OPEN',"
                    "'NORMAL',:user,:request,1)"
                ),
                {"id": uuid4(), "snapshot": retained, "user": user_id, "request": uuid4()},
            )

        command.upgrade(config, "head")
        for invalid_limit in (None, 0, 501):
            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.scalar(
                        text("SELECT purge_expired_lookup_snapshots(:limit)"),
                        {"limit": invalid_limit},
                    )
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM subscriber_snapshots WHERE id=:id FOR KEY SHARE"),
                {"id": locked},
            )
            with engine.begin() as maintenance:
                assert maintenance.scalar(text("SELECT purge_expired_lookup_snapshots(500)")) == 1
        with Session(engine) as db:
            assert db.get(SubscriberSnapshot, removable) is None
            assert db.get(SubscriberSnapshot, locked) is not None
            assert db.get(SubscriberSnapshot, retained) is not None
            assert db.get(SubscriberSnapshot, fresh) is not None
            assert db.get(SubscriberSnapshot, retained).contacts == []
            assert db.scalar(select(func.count()).select_from(LookupCleanupAudit)) == 1
            assert db.scalar(select(LookupCleanupAudit.deleted_count)) == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ConnectionSnapshot)
                    .where(ConnectionSnapshot.subscriber_snapshot_id == removable)
                )
                == 0
            )

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM subscriber_snapshots WHERE id=:id"), {"id": locked}
                )
        # Once the competing key-share transaction ends, only the procedure can remove it.
        with engine.begin() as maintenance:
            assert maintenance.scalar(text("SELECT purge_expired_lookup_snapshots(500)")) == 1

        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM subscriber_snapshots WHERE id=:id"), {"id": retained}
                )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM tickets WHERE subscriber_snapshot_id=:id"),
                    {"id": retained},
                )
                == 1
            )
    finally:
        engine.dispose()
