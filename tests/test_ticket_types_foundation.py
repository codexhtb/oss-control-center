"""Phase 1.4 schema and RBAC checks on isolated PostgreSQL databases."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.identity.crypto import hash_password
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.tickets.models import DiagnosticSnapshot, Ticket
from app.modules.tickets.schemas import TicketView
from app.modules.tickets.service import ticket_detail
from app.modules.tickets.status import TicketType


def test_ticket_creation_permissions_are_centralized() -> None:
    for role in (Role.OPERATOR, Role.ENGINEER):
        assert {Permission.TICKET_CREATE, Permission.SUBSCRIBER_READ} <= permissions_for(role)
    assert Permission.TICKET_CLOSE not in permissions_for(Role.OPERATOR)
    assert Permission.TICKET_ASSIGN not in permissions_for(Role.OPERATOR)
    assert Permission.TICKET_MANUAL_DIAGNOSTIC not in permissions_for(Role.OPERATOR)
    assert Permission.TICKET_DECISION not in permissions_for(Role.OPERATOR)
    assert {
        Permission.TICKET_ASSIGN,
        Permission.TICKET_MANUAL_DIAGNOSTIC,
        Permission.TICKET_DECISION,
        Permission.TICKET_CLOSE,
    } <= permissions_for(Role.ENGINEER)
    assert not {Permission.TICKET_CREATE, Permission.SUBSCRIBER_READ, Permission.TICKET_READ} & (
        permissions_for(Role.ADMIN)
    )
    assert set(TicketType) == {"SUBSCRIBER_LINKED", "MANUAL", "INTERNAL"}


def _config(url: URL, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url.render_as_string(hide_password=False))
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def _user(connection: Connection) -> UUID:
    user_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO app_users (id, login, display_name, role, password_hash, is_active) "
            "VALUES (:id, 'synthetic-phase14-engineer', 'Synthetic engineer', 'engineer', "
            ":hash, true)"
        ),
        {"id": user_id, "hash": hash_password(uuid4().hex)},
    )
    return user_id


def _snapshot(connection: Connection, user_id: UUID, account: str) -> tuple[UUID, UUID]:
    snapshot_id, connection_id = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO subscriber_snapshots "
            "(id, account_no, source, fetched_at, requested_by, expires_at, contracts, services) "
            "VALUES (:id, :account, 'MOCK', now(), :user_id, now() + interval '1 hour', "
            "'[]'::jsonb, '[]'::jsonb)"
        ),
        {"id": snapshot_id, "account": account, "user_id": user_id},
    )
    connection.execute(
        text(
            "INSERT INTO connection_snapshots (id, subscriber_snapshot_id, access_type) "
            "VALUES (:id, :snapshot_id, 'UNKNOWN')"
        ),
        {"id": connection_id, "snapshot_id": snapshot_id},
    )
    return snapshot_id, connection_id


def _ticket(
    connection: Connection,
    user_id: UUID,
    ticket_type: str,
    *,
    account: str | None = None,
    snapshot_id: UUID | None = None,
    connection_id: UUID | None = None,
    service_id: str | None = None,
    status: str = "OPEN",
) -> UUID:
    ticket_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO tickets "
            "(id, ticket_type, account_no, subscriber_snapshot_id, connection_snapshot_id, "
            "selected_service_id, description, status, priority, created_by, client_request_id, "
            "version) VALUES (:id, :ticket_type, :account, :snapshot_id, :connection_id, "
            ":service_id, 'Synthetic ticket', :status, 'NORMAL', :user_id, :request_id, 1)"
        ),
        {
            "id": ticket_id,
            "ticket_type": ticket_type,
            "account": account,
            "snapshot_id": snapshot_id,
            "connection_id": connection_id,
            "service_id": service_id,
            "status": status,
            "user_id": user_id,
            "request_id": uuid4(),
        },
    )
    return ticket_id


def _diagnostic(
    connection: Connection,
    user_id: UUID,
    ticket_id: UUID,
    snapshot_id: UUID | None,
    connection_id: UUID | None = None,
) -> UUID:
    diagnostic_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO diagnostic_snapshots "
            "(id, ticket_id, subscriber_snapshot_id, connection_snapshot_id, source_kind, "
            "availability, observation, observed_at, recorded_by) "
            "VALUES (:id, :ticket_id, :snapshot_id, :connection_id, 'MANUAL', 'KNOWN', "
            "'{}'::jsonb, now(), :user_id)"
        ),
        {
            "id": diagnostic_id,
            "ticket_id": ticket_id,
            "snapshot_id": snapshot_id,
            "connection_id": connection_id,
            "user_id": user_id,
        },
    )
    return diagnostic_id


def _rejected(engine: Engine, statement: str, params: dict) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(statement), params)


def test_0007_upgrade_preserves_existing_linked_ticket_and_history(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(isolated_postgres_url, monkeypatch)
    command.upgrade(config, "0007_contacts")
    engine = create_engine(isolated_postgres_url)
    try:
        # The current ORM includes 0008 columns; use only SQL against 0007.
        with engine.begin() as connection:
            user_id = _user(connection)
            snapshot_id, connection_id = _snapshot(connection, user_id, "MOCK-LEGACY-14")
            ticket_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO tickets (id, account_no, subscriber_snapshot_id, "
                    "connection_snapshot_id, description, status, priority, created_by, "
                    "client_request_id, version) VALUES (:id, 'MOCK-LEGACY-14', :snapshot_id, "
                    ":connection_id, 'Legacy synthetic ticket', 'IN_PROGRESS', 'NORMAL', "
                    ":user_id, :request_id, 1)"
                ),
                {
                    "id": ticket_id,
                    "snapshot_id": snapshot_id,
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "request_id": uuid4(),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO ticket_history "
                    "(id, ticket_id, event_type, actor_id, event_data, ticket_version) "
                    "VALUES (:id, :ticket_id, 'ticket_created', :user_id, '{}'::jsonb, 1)"
                ),
                {"id": uuid4(), "ticket_id": ticket_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ticket_comments (id, ticket_id, author_id, body) "
                    "VALUES (:id, :ticket_id, :user_id, 'Synthetic comment')"
                ),
                {"id": uuid4(), "ticket_id": ticket_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ticket_decisions "
                    "(id, ticket_id, engineer_id, decision_text, performed_actions) "
                    "VALUES (:id, :ticket_id, :user_id, 'Synthetic decision', 'Synthetic action')"
                ),
                {"id": uuid4(), "ticket_id": ticket_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_events "
                    "(id, ticket_id, actor_id, action, outcome, origin) "
                    "VALUES (:id, :ticket_id, :user_id, 'ticket_created', 'success', 'API')"
                ),
                {"id": uuid4(), "ticket_id": ticket_id, "user_id": user_id},
            )
            _diagnostic(connection, user_id, ticket_id, snapshot_id, connection_id)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision() == "0008_ticket_types"
            )
            for table in (
                "ticket_history",
                "ticket_comments",
                "ticket_decisions",
                "audit_events",
                "diagnostic_snapshots",
            ):
                assert (
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE ticket_id = :id"),
                        {"id": ticket_id},
                    ).scalar_one()
                    == 1
                )
        with Session(engine) as db:
            stored = db.get(Ticket, ticket_id)
            assert stored is not None
            assert stored.ticket_type == TicketType.SUBSCRIBER_LINKED
            assert stored.account_no == "MOCK-LEGACY-14"
            assert stored.subscriber_snapshot_id == snapshot_id
            assert stored.connection_snapshot_id == connection_id
            observation = db.query(DiagnosticSnapshot).filter_by(ticket_id=ticket_id).one()
            assert observation.subscriber_snapshot_id == snapshot_id
    finally:
        engine.dispose()


def test_0008_sql_constraints_and_diagnostic_parent_links(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(isolated_postgres_url, monkeypatch)
    command.upgrade(config, "head")
    engine = create_engine(isolated_postgres_url)
    try:
        with engine.begin() as connection:
            user_id = _user(connection)
            first_snapshot, first_connection = _snapshot(connection, user_id, "MOCK-LINKED-14")
            second_snapshot, second_connection = _snapshot(connection, user_id, "MOCK-OTHER-14")
            another_connection_same_snapshot = uuid4()
            connection.execute(
                text(
                    "INSERT INTO connection_snapshots (id, subscriber_snapshot_id, access_type) "
                    "VALUES (:id, :snapshot_id, 'UNKNOWN')"
                ),
                {"id": another_connection_same_snapshot, "snapshot_id": first_snapshot},
            )
            linked = _ticket(
                connection,
                user_id,
                TicketType.SUBSCRIBER_LINKED,
                account="MOCK-LINKED-14",
                snapshot_id=first_snapshot,
                connection_id=first_connection,
            )
            manual = _ticket(connection, user_id, TicketType.MANUAL)
            internal = _ticket(connection, user_id, TicketType.INTERNAL)
            _diagnostic(connection, user_id, linked, first_snapshot, first_connection)
            _diagnostic(connection, user_id, manual, None)
            _diagnostic(connection, user_id, internal, None)
        with Session(engine) as db:
            assert {db.get(Ticket, item).ticket_type for item in (linked, manual, internal)} == {
                "SUBSCRIBER_LINKED",
                "MANUAL",
                "INTERNAL",
            }
            assert db.get(Ticket, manual).subscriber_snapshot_id is None
            assert db.get(Ticket, internal).account_no is None
            manual_diagnostic = db.query(DiagnosticSnapshot).filter_by(ticket_id=manual).one()
            assert manual_diagnostic.subscriber_snapshot_id is None
            assert TicketView.model_validate(db.get(Ticket, manual)).subscriber_snapshot_id is None
            assert ticket_detail(db, manual).subscriber is None
            assert ticket_detail(db, internal).selected_connection is None

        _rejected(
            engine, "UPDATE tickets SET ticket_type = 'INTERNAL' WHERE id = :id", {"id": manual}
        )
        _rejected(engine, "UPDATE tickets SET account_no = 'other' WHERE id = :id", {"id": linked})
        for ticket_type, account, snapshot_id, connection_id, service_id in (
            ("MANUAL", "unverified", None, None, None),
            ("INTERNAL", None, first_snapshot, None, None),
            ("INTERNAL", None, None, None, "service"),
            ("SUBSCRIBER_LINKED", None, None, None, None),
            ("SUBSCRIBER_LINKED", "MOCK-LINKED-14", first_snapshot, second_connection, None),
            ("SUBSCRIBER_LINKED", "MOCK-OTHER-14", first_snapshot, None, None),
            ("OTHER", None, None, None, None),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _ticket(
                        connection,
                        user_id,
                        ticket_type,
                        account=account,
                        snapshot_id=snapshot_id,
                        connection_id=connection_id,
                        service_id=service_id,
                    )
        for ticket_id, snapshot_id, connection_id in (
            (uuid4(), None, None),
            (manual, first_snapshot, None),
            (linked, None, None),
            (linked, second_snapshot, second_connection),
            (linked, first_snapshot, another_connection_same_snapshot),
            (manual, None, first_connection),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    _diagnostic(connection, user_id, ticket_id, snapshot_id, connection_id)
    finally:
        engine.dispose()
