"""Real PostgreSQL Ticket Engine and API acceptance tests in disposable databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.billing import get_billing_provider
from app.api.v1.diagnostics import engine_dependency, onu_dependency
from app.api.v1.tickets import get_field_dispatch_gateway
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.main import create_app
from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import DeviceMapping, MappingRegistry
from app.modules.diagnostics.correlation import DiagnosticReport, Observation
from app.modules.field_dispatch.gateway import DispatchReceipt, DispatchRejected, DispatchUncertain
from app.modules.field_dispatch.models import FieldWorkDeliveryAttempt, FieldWorkHandoff
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role
from app.modules.tickets.models import (
    ConnectionSnapshot,
    DiagnosticCheck,
    DiagnosticSnapshot,
    SubscriberSnapshot,
    Ticket,
    TicketAudit,
    TicketCallback,
    TicketCategory,
    TicketEvent,
    TicketPurgeAudit,
    TicketResolution,
)
from app.modules.tickets.schemas import AddDecision, CreateTicket, RecordCallback
from app.modules.tickets.service import (
    claim_ticket,
    create_ticket,
    record_callback,
    resolve_ticket,
    start_ticket,
)
from app.modules.tickets.status import TicketStatus, TicketType

ORIGIN = "https://oss.example.test"
PASSWORD = "Test-password-with-sufficient-length-42!"


@pytest.fixture
def ticket_engine(isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "head")
    engine = create_engine(isolated_postgres_url, connect_args={"connect_timeout": 5})
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def ticket_client(ticket_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BILLING_PROVIDER", "MOCK")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(ticket_engine, autoflush=False, expire_on_commit=False)

    def test_session() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def seed_user(engine: Engine, name: str, role: Role) -> User:
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            login=name,
            display_name=name.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user


def seed_legacy_user(engine: Engine, name: str, role: Role) -> UUID:
    """Insert through the schema at its historical revision, before ORM 0013 columns exist."""
    user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO app_users (id, login, display_name, role, password_hash)
                 VALUES (:id, :login, :name, :role, :password_hash)"""),
            {
                "id": user_id,
                "login": name,
                "name": name.title(),
                "role": role,
                "password_hash": hash_password(PASSWORD),
            },
        )
    return user_id


def seed_snapshot(
    engine: Engine,
    user: User,
    count: int = 1,
    ip_groups: list | None = None,
    *,
    phone: str | None = None,
    actual_address: str | None = None,
    house_number: str | None = None,
) -> tuple[SubscriberSnapshot, list[ConnectionSnapshot]]:
    with Session(engine, expire_on_commit=False) as db:
        snapshot = SubscriberSnapshot(
            account_no="000123",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=user.id,
            full_name="Synthetic Subscriber",
            phone=phone,
            actual_address=actual_address,
            house_number=house_number,
            contracts=[{"id": "contract-test"}],
            services=[{"id": "service-test"}],
        )
        db.add(snapshot)
        db.flush()
        connections = [
            ConnectionSnapshot(
                subscriber_snapshot_id=snapshot.id,
                access_type=["ETHERNET", "EPON", "GPON"][index],
                device=None,
                port=None,
                vlan=None,
                onu_id=None,
                ip_groups=ip_groups or [],
            )
            for index in range(count)
        ]
        db.add_all(connections)
        db.commit()
        return snapshot, connections


def login(client: TestClient, name: str) -> None:
    assert (
        client.post("/api/v1/auth/login", json={"login": name, "password": PASSWORD}).status_code
        == 200
    )


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["oss_csrf"]}


def grant_ticket_permission(engine: Engine, user: User, permission: Permission) -> None:
    with Session(engine) as db:
        row = db.get(User, user.id)
        assert row is not None
        if not row.permissions_customized:
            from app.modules.identity.roles import permissions_for

            db.add_all(
                UserPermission(user_id=user.id, permission=value)
                for value in permissions_for(Role(user.role))
            )
            row.permissions_customized = True
        db.add(UserPermission(user_id=user.id, permission=permission))
        db.commit()


def make_manual_ticket(client: TestClient, *, subject: str = "Synthetic deletion test") -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers=csrf(client),
        json={
            "ticket_type": "MANUAL",
            "subject": subject,
            "description": "Disposable test ticket",
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_soft_delete_own_scope_visibility_restore_and_security(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    owner = seed_user(ticket_engine, "delete_owner", Role.OPERATOR)
    other = seed_user(ticket_engine, "delete_other", Role.OPERATOR)
    admin = seed_user(ticket_engine, "delete_admin", Role.ADMIN)
    grant_ticket_permission(ticket_engine, owner, Permission.TICKET_DELETE_OWN)
    login(ticket_client, owner.login)
    own = make_manual_ticket(ticket_client)
    login(ticket_client, other.login)
    foreign = make_manual_ticket(ticket_client, subject="Other synthetic ticket")
    login(ticket_client, owner.login)
    own_path = f"/api/v1/tickets/{own['id']}"
    foreign_path = f"/api/v1/tickets/{foreign['id']}"
    assert ticket_client.delete(own_path, headers={"If-Match": '"1"'}).status_code == 403
    assert (
        ticket_client.delete(
            own_path,
            headers={**csrf(ticket_client), "Origin": "https://wrong.example", "If-Match": '"1"'},
        ).status_code
        == 403
    )
    assert (
        ticket_client.delete(
            foreign_path, headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 403
    )
    assert ticket_client.delete(own_path, headers=csrf(ticket_client)).status_code == 428
    deleted = ticket_client.request(
        "DELETE",
        own_path,
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"reason": "Синтетическая причина"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_by"] == str(owner.id)
    assert deleted.json()["delete_reason"] == "Синтетическая причина"
    assert deleted.json()["version"] == 2
    assert ticket_client.get(own_path).status_code == 404
    assert (
        ticket_client.get("/api/v1/tickets", params={"number": own["number"]}).json()["total"] == 0
    )
    assert all(
        row["ticket_id"] != own["id"]
        for row in ticket_client.get("/api/v1/tickets/events/recent").json()["items"]
    )
    assert ticket_client.get("/api/v1/tickets/trash").status_code == 403
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(TicketEvent.ticket_id == UUID(own["id"]))
            )
            == 2
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(TicketAudit.ticket_id == UUID(own["id"]))
            )
            == 2
        )
    login(ticket_client, admin.login)
    assert any(
        row["id"] == own["id"] for row in ticket_client.get("/api/v1/tickets/trash").json()["items"]
    )
    assert (
        ticket_client.post(
            f"{own_path}/restore", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 409
    )
    restored = ticket_client.post(
        f"{own_path}/restore", headers={**csrf(ticket_client), "If-Match": '"2"'}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["deleted_at"] is None
    assert ticket_client.get(own_path).status_code == 200
    assert any(
        event["event_type"] == "ticket_deleted"
        and event["event_data"]["reason"] == "Синтетическая причина"
        for event in ticket_client.get(f"{own_path}/history").json()["items"]
    )
    assert (
        ticket_client.get("/api/v1/tickets", params={"number": own["number"]}).json()["total"] == 1
    )
    grant_ticket_permission(ticket_engine, other, Permission.TICKET_DELETE_ALL)
    login(ticket_client, other.login)
    delegated = ticket_client.delete(own_path, headers={**csrf(ticket_client), "If-Match": '"3"'})
    assert delegated.status_code == 200, delegated.text
    login(ticket_client, admin.login)
    assert (
        ticket_client.post(
            own_path + "/restore", headers={**csrf(ticket_client), "If-Match": '"4"'}
        ).status_code
        == 200
    )
    with Session(ticket_engine) as db:
        grant = db.get(UserPermission, (other.id, Permission.TICKET_DELETE_ALL))
        assert grant is not None
        db.delete(grant)
        db.commit()
    login(ticket_client, other.login)
    assert (
        ticket_client.delete(
            own_path, headers={**csrf(ticket_client), "If-Match": '"5"'}
        ).status_code
        == 403
    )


def test_purge_requires_soft_delete_confirmation_and_keeps_minimal_audit(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    admin = seed_user(ticket_engine, "purge_admin", Role.ADMIN)
    login(ticket_client, admin.login)
    ticket = make_manual_ticket(ticket_client)
    path = f"/api/v1/tickets/{ticket['id']}"
    assert (
        ticket_client.request(
            "DELETE",
            f"{path}/purge",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"confirm_number": ticket["number"]},
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            f"{path}/comments", headers=csrf(ticket_client), json={"body": "Synthetic context"}
        ).status_code
        == 201
    )
    current_version = ticket_client.get(path).json()["version"]
    deleted = ticket_client.delete(
        path, headers={**csrf(ticket_client), "If-Match": f'"{current_version}"'}
    )
    assert deleted.status_code == 200
    assert (
        ticket_client.request(
            "DELETE",
            f"{path}/purge",
            headers={**csrf(ticket_client), "If-Match": f'"{current_version + 1}"'},
            json={"confirm_number": ticket["number"] + 1},
        ).status_code
        == 422
    )
    with Session(ticket_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                text("DELETE FROM ticket_comments WHERE ticket_id = :id"), {"id": ticket["id"]}
            )
            db.commit()
    purged = ticket_client.request(
        "DELETE",
        f"{path}/purge",
        headers={**csrf(ticket_client), "If-Match": f'"{current_version + 1}"'},
        json={"confirm_number": ticket["number"]},
    )
    assert purged.status_code == 204, purged.text
    assert ticket_client.get(path).status_code == 404
    with Session(ticket_engine) as db:
        assert db.get(Ticket, UUID(ticket["id"])) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(TicketEvent.ticket_id == UUID(ticket["id"]))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketPurgeAudit)
                .where(TicketPurgeAudit.ticket_id == UUID(ticket["id"]))
            )
            == 1
        )


def test_0014_upgrade_keeps_existing_ticket_and_grants_admin(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0013_identity_permissions")
    engine = create_engine(isolated_postgres_url)
    try:
        admin_id = seed_legacy_user(engine, "migration_delete_admin", Role.ADMIN)
        ticket_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO tickets
                    (id, ticket_type, description, status, priority, created_by,
                     client_request_id, version)
                    VALUES (:id, 'INTERNAL', 'Disposable historical request',
                            'OPEN', 'NORMAL', :author, :request_id, 1)"""),
                {"id": ticket_id, "author": admin_id, "request_id": uuid4()},
            )
        command.upgrade(config, "head")
        with Session(engine) as db:
            ticket = db.get(Ticket, ticket_id)
            assert ticket is not None
            assert ticket.deleted_at is None
            assert ticket.description == "Disposable historical request"
            assert set(
                db.scalars(
                    select(UserPermission.permission).where(UserPermission.user_id == admin_id)
                )
            ) >= {"tickets.delete.own", "tickets.delete.all", "tickets.restore", "tickets.purge"}
    finally:
        engine.dispose()


def test_purge_closed_linked_ticket_preserves_source_snapshot(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    admin = seed_user(ticket_engine, "closed_purge_admin", Role.ADMIN)
    snapshot, connections = seed_snapshot(ticket_engine, admin)
    login(ticket_client, admin.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    path = f"/api/v1/tickets/{ticket_id}"
    started = ticket_client.post(
        path + "/start", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert started.status_code == 200, started.text
    diagnosed = ticket_client.post(
        path + "/manual-diagnostics",
        headers=csrf(ticket_client),
        json={
            "finding": "Synthetic observation",
            "observed_at": datetime.now(UTC).isoformat(),
            "connection_snapshot_id": str(connections[0].id),
        },
    )
    assert diagnosed.status_code == 201, diagnosed.text
    decided = ticket_client.post(
        path + "/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Synthetic resolution", "performed_actions": "Synthetic action"},
    )
    assert decided.status_code == 201, decided.text
    version = ticket_client.get(path).json()["version"]
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json={"decision_text": "Synthetic resolution", "performed_actions": "Synthetic action"},
    )
    assert resolved.status_code == 200, resolved.text
    closed = ticket_client.post(
        path + "/callbacks",
        headers={**csrf(ticket_client), "If-Match": f'"{resolved.json()["version"]}"'},
        json={"client_request_id": str(uuid4()), "outcome": "CONFIRMED"},
    )
    assert closed.status_code == 200, closed.text
    deleted = ticket_client.delete(
        path,
        headers={**csrf(ticket_client), "If-Match": f'"{closed.json()["version"]}"'},
    )
    assert deleted.status_code == 200, deleted.text
    purged = ticket_client.request(
        "DELETE",
        path + "/purge",
        headers={**csrf(ticket_client), "If-Match": f'"{deleted.json()["version"]}"'},
        json={"confirm_number": created.json()["number"]},
    )
    assert purged.status_code == 204, purged.text
    with Session(ticket_engine) as db:
        assert db.get(Ticket, UUID(ticket_id)) is None
        assert db.get(SubscriberSnapshot, snapshot.id) is not None
        assert db.get(ConnectionSnapshot, connections[0].id) is not None
        assert db.scalar(select(func.count()).select_from(DiagnosticSnapshot)) == 0


def test_onu_endpoint_requires_engineer_and_never_invents_an_onu(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "onu_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "onu_operator", Role.OPERATOR)
    snapshot, connections = seed_snapshot(
        ticket_engine,
        engineer,
        count=2,
        ip_groups=[{"group_id": "synthetic", "rows": [{"switch_id": "7", "vlan_id": 101}]}],
    )
    ticket_client.app.dependency_overrides[onu_dependency] = lambda: (
        MappingRegistry(devices=[DeviceMapping(netup_switch_id="7", librenms_device_id=42)]),
        None,
    )
    path = f"/api/v1/subscriber-lookups/{snapshot.id}/connections/{connections[1].id}/onu"
    login(ticket_client, operator.login)
    assert ticket_client.get(path).status_code == 403
    login(ticket_client, engineer.login)
    response = ticket_client.get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["availability"] == "UNAVAILABLE"
    assert response.json()["candidates"] == []


def test_automatic_diagnostic_requires_engineer_confirmation(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "diagnostic_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(ticket_engine, engineer)
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    class SyntheticEngine:
        def check(self, connection):
            when = datetime.now(UTC)
            return DiagnosticReport(
                connection_snapshot_id=connection.id,
                checked_at=when,
                device_match=DataQuality.KNOWN,
                interface_match=DataQuality.KNOWN,
                observations=[
                    Observation(
                        source="LIBRENMS",
                        measured_object="device:42",
                        result="UP",
                        observed_at=when,
                        retrieved_at=when,
                        quality=DataQuality.KNOWN,
                    )
                ],
                limitations=["optical_measurement_not_verified"],
            )

    ticket_client.app.dependency_overrides[engine_dependency] = SyntheticEngine
    check_path = f"/api/v1/tickets/{ticket_id}/diagnostics/check"
    assert ticket_client.post(check_path, headers={"If-Match": '"1"'}).status_code == 403
    checked = ticket_client.post(check_path, headers={**csrf(ticket_client), "If-Match": '"1"'})
    assert checked.status_code == 200
    check_id = checked.json()["check_id"]
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(DiagnosticSnapshot)) == 0
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/diagnostics/confirm",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"check_id": check_id},
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/start",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
        ).status_code
        == 200
    )
    confirmed = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/diagnostics/confirm",
        headers={**csrf(ticket_client), "If-Match": '"2"'},
        json={"check_id": check_id},
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["source_kind"] == "AUTOMATED"
    assert confirmed.json()["check_id"] == check_id
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/diagnostics/confirm",
            headers={**csrf(ticket_client), "If-Match": '"3"'},
            json={"check_id": check_id},
        ).status_code
        == 409
    )
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(DiagnosticSnapshot)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(TicketEvent.event_type == "automatic_diagnostic_confirmed")
            )
            == 1
        )

    operator = seed_user(ticket_engine, "diagnostic_operator", Role.OPERATOR)
    login(ticket_client, operator.login)
    assert (
        ticket_client.post(
            check_path, headers={**csrf(ticket_client), "If-Match": '"3"'}
        ).status_code
        == 403
    )
    admin = seed_user(ticket_engine, "diagnostic_admin", Role.ADMIN)
    login(ticket_client, admin.login)
    assert (
        ticket_client.post(
            check_path, headers={**csrf(ticket_client), "If-Match": '"3"'}
        ).status_code
        == 200
    )


def test_diagnostic_check_rejects_another_connection(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "diagnostic_fk_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(ticket_engine, engineer, count=2)
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201
    with Session(ticket_engine) as db:
        db.add(
            DiagnosticCheck(
                ticket_id=UUID(created.json()["id"]),
                subscriber_snapshot_id=snapshot.id,
                connection_snapshot_id=connections[1].id,
                requested_by=engineer.id,
                report={},
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(DiagnosticCheck)) == 0


def create(
    client: TestClient,
    snapshot: SubscriberSnapshot,
    connection: ConnectionSnapshot | None = None,
    request_id=None,
    duplicate_reason: str | None = None,
):
    return client.post(
        "/api/v1/tickets",
        headers=csrf(client),
        json={
            "lookup_id": str(snapshot.id),
            "connection_snapshot_id": str(connection.id) if connection else None,
            "description": "Synthetic connectivity issue",
            "client_request_id": str(request_id or uuid4()),
            **({"duplicate_reason": duplicate_reason} if duplicate_reason else {}),
        },
    )


def test_upgrade_0002_to_0003_preserves_identity(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0002_identity_rbac")
    engine = create_engine(isolated_postgres_url)
    try:
        seed_legacy_user(engine, "operator", Role.OPERATOR)
        command.upgrade(config, "0003_ticket_engine")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0003_ticket_engine"
            )
            assert connection.execute(text("SELECT count(*) FROM app_users")).scalar_one() == 1
            assert {
                "tickets",
                "ticket_comments",
                "ticket_decisions",
                "ticket_history",
                "audit_events",
                "subscriber_snapshots",
                "connection_snapshots",
                "diagnostic_snapshots",
            } <= set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_create_snapshot_idempotency_and_pagination(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, connections = seed_snapshot(ticket_engine, operator, 3)
    login(ticket_client, "operator")
    assert create(ticket_client, snapshot).status_code == 422
    request_id = uuid4()
    response = create(ticket_client, snapshot, connections[1], request_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "OPEN" and body["account_no"] == "000123"
    assert response.headers["etag"] == '"1"'
    assert create(ticket_client, snapshot, connections[1], request_id).status_code == 200
    conflict = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "lookup_id": str(snapshot.id),
            "connection_snapshot_id": str(connections[0].id),
            "description": "Other",
            "client_request_id": str(request_id),
        },
    )
    assert conflict.status_code == 409
    detail = ticket_client.get(f"/api/v1/tickets/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["subscriber"]["full_name"] == "Synthetic Subscriber"
    assert len(detail.json()["subscriber"]["connections"]) == 3
    assert detail.json()["technical_state"] == "UNKNOWN"
    assert (
        ticket_client.get(
            "/api/v1/tickets",
            params={
                "status": "OPEN",
                "priority": "NORMAL",
                "number": body["number"],
                "page": 1,
                "size": 1,
            },
        ).json()["total"]
        == 1
    )
    assert ticket_client.get("/api/v1/tickets", params={"status": "CLOSED"}).json()["total"] == 0
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 1
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 1


def test_single_connection_replay_and_page_boundary(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, connections = seed_snapshot(ticket_engine, operator)
    login(ticket_client, "operator")
    first_request = uuid4()
    first = create(ticket_client, snapshot, request_id=first_request)
    assert first.status_code == 201
    assert first.json()["connection_snapshot_id"] == str(connections[0].id)
    replay = create(ticket_client, snapshot, request_id=first_request)
    assert replay.status_code == 200, replay.text
    assert (
        create(ticket_client, snapshot, duplicate_reason="Separate synthetic issue").status_code
        == 201
    )
    first_page = ticket_client.get("/api/v1/tickets", params={"page": 1, "size": 1}).json()
    second_page = ticket_client.get("/api/v1/tickets", params={"page": 2, "size": 1}).json()
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]


def test_engineer_workflow_and_rbac(ticket_engine: Engine, ticket_client: TestClient) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "engineer", Role.ENGINEER)
    grant_ticket_permission(ticket_engine, engineer, Permission.TICKET_CALLBACK)
    seed_user(ticket_engine, "admin", Role.ADMIN)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, "operator")
    created = create(ticket_client, snapshot)
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/start", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 403
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/comments",
            headers=csrf(ticket_client),
            json={"body": "Additional caller detail"},
        ).status_code
        == 201
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/decisions",
            headers=csrf(ticket_client),
            json={"decision_text": "Resolved", "performed_actions": "Checked"},
        ).status_code
        == 403
    )
    login(ticket_client, "engineer")
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/close",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"decision_id": str(uuid4())},
        ).status_code
        == 409
    )
    assigned = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/assignment",
        headers={**csrf(ticket_client), "If-Match": '"2"'},
        json={"engineer_id": str(engineer.id)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned_engineer_id"] == str(engineer.id)
    assert (
        ticket_client.get("/api/v1/tickets", params={"assignee": str(engineer.id)}).json()["total"]
        == 1
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/start", headers={**csrf(ticket_client), "If-Match": '"2"'}
        ).status_code
        == 409
    )
    started = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/start", headers={**csrf(ticket_client), "If-Match": '"3"'}
    )
    assert started.status_code == 200, started.text
    field = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/field-work-request",
        headers={**csrf(ticket_client), "If-Match": '"4"'},
        json={"reason": "On-site cable check required"},
    )
    assert field.status_code == 200 and field.json()["status"] == "IN_PROGRESS"
    diagnostic = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/manual-diagnostics",
        headers=csrf(ticket_client),
        json={"finding": "Manual inspection pending", "observed_at": datetime.now(UTC).isoformat()},
    )
    assert diagnostic.status_code == 201, diagnostic.text
    assert diagnostic.json()["source_kind"] == "MANUAL"
    decision = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Further work complete", "performed_actions": "Checked connection"},
    )
    assert decision.status_code == 201, decision.text
    version = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()["version"]
    resolved = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json={"decision_text": "Further work complete", "performed_actions": "Checked connection"},
    )
    assert resolved.status_code == 200, resolved.text
    version = resolved.json()["version"]
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/close",
            headers=csrf(ticket_client),
            json={"decision_id": decision.json()["id"]},
        ).status_code
        == 428
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/close",
            headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
            json={"decision_id": str(uuid4())},
        ).status_code
        == 409
    )
    closed = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/callbacks",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json={"client_request_id": str(uuid4()), "outcome": "CONFIRMED"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_by"] == str(engineer.id)
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/comments",
            headers=csrf(ticket_client),
            json={"body": "late"},
        ).status_code
        == 409
    )
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/history").json()["total"] == 9
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/audit").json()["total"] == 9
    login(ticket_client, "admin")
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}").status_code == 200
    assert (
        ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json={}).status_code
        == 422
    )


def test_csrf_invalid_inputs_and_snapshot_scope(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    other = seed_user(ticket_engine, "other", Role.OPERATOR)
    snapshot, connections = seed_snapshot(ticket_engine, other)
    login(ticket_client, "operator")
    assert (
        ticket_client.post("/api/v1/tickets", json={"lookup_id": str(snapshot.id)}).status_code
        == 403
    )
    assert create(ticket_client, snapshot).status_code == 404
    own, _ = seed_snapshot(ticket_engine, operator)
    response = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={"lookup_id": str(own.id), "description": "", "client_request_id": str(uuid4())},
    )
    assert response.status_code == 422
    response = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "lookup_id": str(own.id),
            "connection_snapshot_id": str(connections[0].id),
            "description": "x",
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert ticket_client.get("/api/v1/tickets", params={"page": 0}).status_code == 422


def test_concurrent_version_conflict(ticket_engine: Engine, ticket_client: TestClient) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    first = seed_user(ticket_engine, "first", Role.ENGINEER)
    second = seed_user(ticket_engine, "second", Role.ENGINEER)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, "operator")
    ticket_id = create(ticket_client, snapshot).json()["id"]

    def attempt(actor_id):
        with Session(ticket_engine) as db:
            try:
                start_ticket(db, actor_id, ticket_id, 1)
                return "success"
            except ApiError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [first.id, second.id]))
    assert sorted(outcomes) == ["success", "version_conflict"]
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 2
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 2


def test_failed_audit_rolls_back_ticket(ticket_engine: Engine, ticket_client: TestClient) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, "operator")
    with ticket_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION reject_ticket_audit() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'blocked'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_ticket_audit BEFORE INSERT ON audit_events "
                "FOR EACH ROW EXECUTE FUNCTION reject_ticket_audit()"
            )
        )
    with pytest.raises(Exception, match="blocked"):
        create(ticket_client, snapshot)
    with pytest.raises(Exception, match="blocked"):
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": "INTERNAL",
                "description": "Synthetic task",
                "client_request_id": str(uuid4()),
            },
        )
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 0
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 0


def test_snapshot_and_history_are_immutable(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, "operator")
    ticket_id = create(ticket_client, snapshot).json()["id"]
    with pytest.raises(DBAPIError, match="immutable Service Desk record"):
        with ticket_engine.begin() as connection:
            connection.execute(
                text("UPDATE subscriber_snapshots SET account_no = 'changed' WHERE id = :id"),
                {"id": snapshot.id},
            )
    with pytest.raises(DBAPIError, match="immutable Service Desk record"):
        with ticket_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM ticket_history WHERE ticket_id = :id"), {"id": ticket_id}
            )


def test_three_types_http_creation_rbac_and_filters(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "engineer", Role.ENGINEER)
    seed_user(ticket_engine, "admin", Role.ADMIN)
    created_ids = []
    for actor in (operator, engineer):
        snapshot, _ = seed_snapshot(ticket_engine, actor)
        login(ticket_client, actor.login)
        for kind in TicketType:
            payload = {
                "ticket_type": kind.value,
                "description": f"Synthetic {kind.value} request",
                "client_request_id": str(uuid4()),
            }
            if kind == TicketType.SUBSCRIBER_LINKED:
                payload["lookup_id"] = str(snapshot.id)
                if actor == engineer:
                    payload["duplicate_reason"] = "Separate synthetic request"
            response = ticket_client.post(
                "/api/v1/tickets", headers=csrf(ticket_client), json=payload
            )
            assert response.status_code == 201, response.text
            body = response.json()
            created_ids.append(body["id"])
            assert body["ticket_type"] == kind.value
            assert body["status"] == "OPEN"
            detail = ticket_client.get(f"/api/v1/tickets/{body['id']}")
            assert detail.status_code == 200
            assert detail.json()["technical_state"] == "UNKNOWN"
            if kind == TicketType.SUBSCRIBER_LINKED:
                assert detail.json()["subscriber"]["account_no"] == "000123"
            else:
                assert body["account_no"] is None
                assert body["subscriber_snapshot_id"] is None
                assert detail.json()["subscriber"] is None
                assert detail.json()["selected_connection"] is None
            listing = ticket_client.get("/api/v1/tickets", params={"ticket_type": kind.value})
            assert listing.status_code == 200
            assert all(item["ticket_type"] == kind.value for item in listing.json()["items"])
    assert len(set(created_ids)) == 6
    assert (
        ticket_client.get("/api/v1/tickets", params={"account_no": "000123"}).json()["total"] == 2
    )
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 6
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 6
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 6
    login(ticket_client, "admin")
    assert ticket_client.get("/api/v1/tickets").status_code == 200
    for kind in TicketType:
        assert ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind.value,
                "description": "Denied",
                "client_request_id": str(uuid4()),
            },
        ).status_code == (422 if kind == TicketType.SUBSCRIBER_LINKED else 201)


def test_subject_for_all_types_validation_replay_and_legacy_client(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, operator.login)
    old_payload = {
        "lookup_id": str(snapshot.id),
        "description": "Legacy synthetic request",
        "client_request_id": str(uuid4()),
    }
    old = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=old_payload)
    assert old.status_code == 201, old.text
    assert old.json()["subject"] is None
    assert (
        ticket_client.post(
            "/api/v1/tickets", headers=csrf(ticket_client), json=old_payload
        ).status_code
        == 200
    )
    for kind in TicketType:
        payload = {
            "ticket_type": kind.value,
            "subject": f"Synthetic {kind.value} task",
            "description": "Synthetic description",
            "client_request_id": str(uuid4()),
        }
        if kind == TicketType.SUBSCRIBER_LINKED:
            payload["lookup_id"] = str(snapshot.id)
            payload["duplicate_reason"] = "Separate synthetic issue"
        created = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
        assert created.status_code == 201, created.text
        ticket_id = created.json()["id"]
        assert created.json()["subject"] == payload["subject"]
        assert (
            ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()["subject"]
            == payload["subject"]
        )
        assert (
            ticket_client.post(
                "/api/v1/tickets", headers=csrf(ticket_client), json=payload
            ).status_code
            == 200
        )
        changed = {**payload, "subject": "Different synthetic subject"}
        assert (
            ticket_client.post(
                "/api/v1/tickets", headers=csrf(ticket_client), json=changed
            ).status_code
            == 409
        )
        assert ticket_client.post("/api/v1/tickets", json=payload).status_code == 403
    invalid = ("", "   ", " leading", "trailing ", "line\nbreak", "control\x7f", "x" * 161)
    for subject in invalid:
        response = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": "INTERNAL",
                "subject": subject,
                "description": "x",
                "client_request_id": str(uuid4()),
            },
        )
        assert response.status_code == 422
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 4


def test_assignable_engineers_unassigned_filter_and_recent_events(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "engineer", Role.ENGINEER)
    inactive = seed_user(ticket_engine, "inactive", Role.ENGINEER)
    seed_user(ticket_engine, "admin", Role.ADMIN)
    with Session(ticket_engine) as db:
        db.get(User, inactive.id).is_active = False
        db.commit()
    assert ticket_client.get("/api/v1/tickets/events/recent").status_code == 401
    login(ticket_client, operator.login)
    public_engineers = ticket_client.get("/api/v1/tickets/assignable-engineers")
    assert public_engineers.status_code == 200
    assert public_engineers.json()["items"] == [
        {"id": str(engineer.id), "display_name": engineer.display_name}
    ]
    created = []
    for kind in ("MANUAL", "INTERNAL"):
        response = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind,
                "description": f"Synthetic {kind}",
                "client_request_id": str(uuid4()),
            },
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    unassigned = ticket_client.get(
        "/api/v1/tickets",
        params={"unassigned": "true", "ticket_type": "MANUAL", "page": 1, "size": 1},
    )
    assert unassigned.status_code == 200
    assert unassigned.json()["total"] == 1
    assert len(unassigned.json()["items"]) == 1
    feed = ticket_client.get("/api/v1/tickets/events/recent", params={"size": 1, "page": 1})
    assert feed.status_code == 200
    assert feed.json()["total"] == 2 and len(feed.json()["items"]) == 1
    assert set(feed.json()["items"][0]) == {
        "id",
        "ticket_id",
        "ticket_number",
        "event_type",
        "actor_id",
        "actor_name",
        "ticket_version",
        "occurred_at",
    }
    assert (
        ticket_client.get("/api/v1/tickets/events/recent", params={"size": 51}).status_code == 422
    )
    assert (
        ticket_client.get("/api/v1/tickets/events/recent", params={"page": 1001}).status_code == 422
    )
    login(ticket_client, engineer.login)
    engineers = ticket_client.get("/api/v1/tickets/assignable-engineers", params={"size": 1})
    assert engineers.status_code == 200
    assert engineers.json()["total"] == 1
    assert engineers.json()["items"] == [
        {"id": str(engineer.id), "display_name": engineer.display_name}
    ]
    assigned = ticket_client.post(
        f"/api/v1/tickets/{created[0]['id']}/assignment",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"engineer_id": str(engineer.id)},
    )
    assert assigned.status_code == 200, assigned.text
    listing = ticket_client.get("/api/v1/tickets", params={"unassigned": "true"}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == created[1]["id"]
    assert (
        ticket_client.get("/api/v1/tickets", params={"assignee": str(engineer.id)}).json()["total"]
        == 1
    )
    assert (
        ticket_client.get(
            "/api/v1/tickets", params={"assignee": str(engineer.id), "unassigned": "true"}
        ).status_code
        == 422
    )
    feed = ticket_client.get("/api/v1/tickets/events/recent", params={"size": 1, "page": 2}).json()
    assert feed["total"] == 3 and len(feed["items"]) == 1
    login(ticket_client, "admin")
    assert ticket_client.get("/api/v1/tickets/events/recent").status_code == 200
    assert ticket_client.get("/api/v1/tickets/assignable-engineers").status_code == 200


def test_0009_upgrade_preserves_subjectless_history(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0008_ticket_types")
    engine = create_engine(isolated_postgres_url)
    try:
        operator_id = seed_legacy_user(engine, "operator", Role.OPERATOR)
        ticket_id = uuid4()
        event_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO tickets
                    (id, ticket_type, description, status, priority,
                     created_by, client_request_id, version)
                    VALUES (:id, 'MANUAL', 'Synthetic legacy description',
                            'OPEN', 'NORMAL', :creator, :request_id, 1)"""),
                {"id": ticket_id, "creator": operator_id, "request_id": uuid4()},
            )
            connection.execute(
                text("""INSERT INTO ticket_history
                    (id, ticket_id, event_type, actor_id, event_data, ticket_version)
                    VALUES (:id, :ticket_id, 'ticket_created', :actor, '{}'::jsonb, 1)"""),
                {"id": event_id, "ticket_id": ticket_id, "actor": operator_id},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == ScriptDirectory.from_config(config).get_current_head()
            )
            assert (
                connection.execute(
                    text("SELECT subject FROM tickets WHERE id = :id"), {"id": ticket_id}
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    text("SELECT id FROM ticket_history WHERE ticket_id = :id"), {"id": ticket_id}
                ).scalar_one()
                == event_id
            )
        with Session(engine) as db:
            assert db.get(Ticket, ticket_id).subject is None
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE tickets SET subject = '   ' WHERE id = :id"), {"id": ticket_id}
                )
    finally:
        engine.dispose()


def test_manual_internal_lifecycle_without_billing(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "engineer", Role.ENGINEER)
    login(ticket_client, operator.login)
    unavailable = ticket_client.post(
        "/api/v1/subscriber-lookups",
        headers=csrf(ticket_client),
        json={"account_no": "MOCK-UNAVAILABLE-001"},
    )
    assert unavailable.status_code == 503
    for kind in (TicketType.MANUAL, TicketType.INTERNAL):
        created = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind.value,
                "description": "Synthetic task without billing",
                "client_request_id": str(uuid4()),
            },
        )
        assert created.status_code == 201, created.text
        ticket_id = created.json()["id"]
        assert (
            ticket_client.post(
                f"/api/v1/tickets/{ticket_id}/start",
                headers={**csrf(ticket_client), "If-Match": '"1"'},
            ).status_code
            == 403
        )
        comment = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/comments",
            headers=csrf(ticket_client),
            json={"body": "Additional synthetic detail"},
        )
        assert comment.status_code == 201
        login(ticket_client, engineer.login)
        version = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()["version"]
        assigned = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/assignment",
            headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
            json={"engineer_id": str(engineer.id)},
        )
        assert assigned.status_code == 200, assigned.text
        started = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/start",
            headers={**csrf(ticket_client), "If-Match": f'"{assigned.json()["version"]}"'},
        )
        assert started.status_code == 200, started.text
        assert (
            ticket_client.post(
                f"/api/v1/tickets/{ticket_id}/close",
                headers={**csrf(ticket_client), "If-Match": f'"{started.json()["version"]}"'},
                json={"decision_id": str(uuid4())},
            ).status_code
            == 409
        )
        diagnostic = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/manual-diagnostics",
            headers=csrf(ticket_client),
            json={"finding": "Observed manually", "observed_at": datetime.now(UTC).isoformat()},
        )
        assert diagnostic.status_code == 201, diagnostic.text
        assert diagnostic.json()["connection_snapshot_id"] is None
        decision = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/decisions",
            headers=csrf(ticket_client),
            json={"decision_text": "Finished", "performed_actions": "Checked manually"},
        )
        assert decision.status_code == 201, decision.text
        current = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()
        resolved = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/resolve",
            headers={**csrf(ticket_client), "If-Match": f'"{current["version"]}"'},
            json={"decision_text": "Finished", "performed_actions": "Checked manually"},
        )
        assert resolved.status_code == 200, resolved.text
        current = resolved.json()
        assert (
            ticket_client.post(
                f"/api/v1/tickets/{ticket_id}/close",
                headers={**csrf(ticket_client), "If-Match": f'"{current["version"] - 1}"'},
                json={"decision_id": decision.json()["id"]},
            ).status_code
            == 409
        )
        closed = ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/close",
            headers={**csrf(ticket_client), "If-Match": f'"{current["version"]}"'},
            json={
                "decision_id": resolved.json()["resolved_decision_id"],
                "reason": "No subscriber",
            },
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"
        assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/history").json()["total"] == 8
        assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/audit").json()["total"] == 8
        with Session(ticket_engine) as db:
            item = db.get(Ticket, UUID(ticket_id))
            assert item.ticket_type == kind
            assert item.subscriber_snapshot_id is None
            assert db.scalar(select(func.count()).select_from(SubscriberSnapshot)) == 0
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(DiagnosticSnapshot)
                    .where(DiagnosticSnapshot.ticket_id == UUID(ticket_id))
                )
                == 1
            )
        login(ticket_client, operator.login)


def test_ticket_type_validation_replay_and_legacy_client(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, operator.login)
    for kind in ("MANUAL", "INTERNAL"):
        request_id = str(uuid4())
        payload = {
            "ticket_type": kind,
            "description": "Synthetic request",
            "client_request_id": request_id,
        }
        first = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
        assert first.status_code == 201, first.text
        repeat = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
        assert repeat.status_code == 200
        assert repeat.json()["id"] == first.json()["id"]
        assert (
            ticket_client.post(
                "/api/v1/tickets",
                headers=csrf(ticket_client),
                json={**payload, "description": "Other"},
            ).status_code
            == 409
        )
        other_kind = "INTERNAL" if kind == "MANUAL" else "MANUAL"
        assert (
            ticket_client.post(
                "/api/v1/tickets",
                headers=csrf(ticket_client),
                json={**payload, "ticket_type": other_kind},
            ).status_code
            == 409
        )
        for field, value in (
            ("lookup_id", str(snapshot.id)),
            ("lookup_id", None),
            ("connection_snapshot_id", None),
            ("selected_service_id", "test"),
            ("account_no", "000123"),
        ):
            assert (
                ticket_client.post(
                    "/api/v1/tickets", headers=csrf(ticket_client), json={**payload, field: value}
                ).status_code
                == 422
            )
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={"description": "No type or lookup", "client_request_id": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={"ticket_type": "INTERNAL", "description": "x", "client_request_id": "bad"},
        ).status_code
        == 422
    )
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": "INTERNAL",
                "description": "x" * 10001,
                "client_request_id": str(uuid4()),
            },
        ).status_code
        == 422
    )
    legacy_id = uuid4()
    legacy = create(ticket_client, snapshot, request_id=legacy_id)
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["ticket_type"] == "SUBSCRIBER_LINKED"
    assert create(ticket_client, snapshot, request_id=legacy_id).status_code == 200


def test_concurrent_manual_idempotency_and_version_conflict(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    actor = seed_user(ticket_engine, "engineer", Role.ENGINEER)
    payload = CreateTicket.model_validate(
        {
            "ticket_type": "MANUAL",
            "description": "Concurrent synthetic request",
            "client_request_id": str(uuid4()),
        }
    )

    def create_once() -> tuple[str, bool]:
        with Session(ticket_engine) as db:
            ticket, created = create_ticket(db, actor.id, payload)
            return str(ticket.id), created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create_once(), range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]

    def start_once() -> str:
        with Session(ticket_engine) as db:
            try:
                start_ticket(db, actor.id, UUID(results[0][0]), 1)
                return "success"
            except ApiError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: start_once(), range(2))) == ["success", "version_conflict"]
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 2


def test_own_ticket_edit_scope_conflict_and_history(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    owner = seed_user(ticket_engine, "edit_owner", Role.OPERATOR)
    other = seed_user(ticket_engine, "edit_other", Role.OPERATOR)
    with Session(ticket_engine) as db:
        for user in (owner, other):
            db.get(User, user.id).permissions_customized = True
            db.add_all(
                [
                    UserPermission(user_id=user.id, permission=permission)
                    for permission in (
                        Permission.TICKET_CREATE,
                        Permission.TICKET_READ_OWN,
                        Permission.TICKET_EDIT_OWN,
                        Permission.TICKET_COMMENT,
                    )
                ]
            )
        db.commit()
    login(ticket_client, owner.login)
    first = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "ticket_type": "MANUAL",
            "subject": "Original subject",
            "description": "Original description",
            "client_request_id": str(uuid4()),
        },
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    assert (
        ticket_client.get(f"/api/v1/tickets/{first_id}").json()["created_by_name"]
        == owner.display_name
    )
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{first_id}",
            headers={"If-Match": '"1"'},
            json={"subject": "Edited subject"},
        ).status_code
        == 403
    )
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{first_id}",
            headers=csrf(ticket_client),
            json={"subject": "Edited subject"},
        ).status_code
        == 428
    )
    edited = ticket_client.patch(
        f"/api/v1/tickets/{first_id}",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"subject": "Edited subject", "description": "Edited description"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{first_id}",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"subject": "Lost update"},
        ).status_code
        == 409
    )
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{first_id}",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"account_no": "not-allowed"},
        ).status_code
        == 422
    )
    assert (
        ticket_client.post(
            f"/api/v1/tickets/{first_id}/comments",
            headers=csrf(ticket_client),
            json={"body": "More synthetic context"},
        ).status_code
        == 201
    )
    assert (
        ticket_client.get(f"/api/v1/tickets/{first_id}/comments").json()["items"][0]["author_name"]
        == owner.display_name
    )
    login(ticket_client, other.login)
    second = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "ticket_type": "INTERNAL",
            "subject": "Other task",
            "description": "Other description",
            "client_request_id": str(uuid4()),
        },
    )
    assert second.status_code == 201
    second_id = second.json()["id"]
    assert ticket_client.get("/api/v1/tickets").json()["total"] == 1
    assert ticket_client.get(f"/api/v1/tickets/{first_id}").status_code == 404
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{first_id}",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"subject": "Unauthorized"},
        ).status_code
        == 403
    )
    login(ticket_client, owner.login)
    assert ticket_client.get("/api/v1/tickets").json()["total"] == 1
    assert ticket_client.get(f"/api/v1/tickets/{second_id}").status_code == 404
    history = ticket_client.get(f"/api/v1/tickets/{first_id}/history")
    assert history.status_code == 200
    change = next(item for item in history.json()["items"] if item["event_type"] == "ticket_edited")
    assert change["actor_id"] == str(owner.id)
    assert change["actor_name"] == owner.display_name
    assert change["event_data"]["changes"]["subject"] == {
        "before": "Original subject",
        "after": "Edited subject",
    }
    with Session(ticket_engine) as db:
        assert db.get(Ticket, UUID(first_id)).subject == "Edited subject"
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(
                    TicketAudit.ticket_id == UUID(first_id), TicketAudit.action == "ticket_edited"
                )
            )
            == 1
        )


def test_categories_rbac_csrf_origin_and_immutable_ticket_label(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "category_operator", Role.OPERATOR)
    admin = seed_user(ticket_engine, "category_admin", Role.ADMIN)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, operator.login)
    listed = ticket_client.get("/api/v1/ticket-categories")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert len(listed.json()) == 8
    category = next(item for item in listed.json() if item["name"] == "Нет интернета")
    other = next(item for item in listed.json() if item["is_other"])
    assert ticket_client.get("/api/v1/ticket-categories?include_inactive=true").status_code == 403
    path = "/api/v1/ticket-categories"
    assert (
        ticket_client.post(
            path, headers=csrf(ticket_client), json={"name": "Новая", "sort_order": 90}
        ).status_code
        == 403
    )
    created = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "lookup_id": str(snapshot.id),
            "category_id": category["id"],
            "caller_phone": "+7 (900) 000-00-01",
            "client_request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert created.json()["category_name"] == "Нет интернета"
    assert created.json()["caller_phone"] == "+7 (900) 000-00-01"
    assert created.json()["description"] == "Нет интернета"
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "lookup_id": str(snapshot.id),
                "category_id": other["id"],
                "client_request_id": str(uuid4()),
                "duplicate_reason": "Отдельный вопрос",
            },
        ).status_code
        == 422
    )
    login(ticket_client, admin.login)
    assert ticket_client.post(path, json={"name": "Новая", "sort_order": 90}).status_code == 403
    assert (
        ticket_client.post(
            path,
            headers={**csrf(ticket_client), "Origin": "https://bad.example"},
            json={"name": "Новая", "sort_order": 90},
        ).status_code
        == 403
    )
    new_category = ticket_client.post(
        path, headers=csrf(ticket_client), json={"name": "Новая", "sort_order": 90}
    )
    assert new_category.status_code == 201, new_category.text
    updated = ticket_client.patch(
        f"{path}/{category['id']}",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"name": "Нет доступа", "sort_order": 5, "is_active": False},
    )
    assert updated.status_code == 200, updated.text
    assert (
        ticket_client.patch(
            f"{path}/{category['id']}",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"sort_order": 20},
        ).status_code
        == 409
    )
    login(ticket_client, operator.login)
    reread = ticket_client.get(f"/api/v1/tickets/{ticket_id}")
    assert reread.json()["category_name"] == "Нет интернета"
    assert reread.json()["caller_phone"] == "+7 (900) 000-00-01"
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "lookup_id": str(snapshot.id),
                "category_id": category["id"],
                "duplicate_reason": "Другая проблема",
                "client_request_id": str(uuid4()),
            },
        ).status_code
        == 422
    )
    with Session(ticket_engine) as db:
        assert db.get(TicketCategory, UUID(category["id"])).name == "Нет доступа"
        assert db.get(Ticket, UUID(ticket_id)).category_name == "Нет интернета"


def test_category_ticket_idempotency_and_manual_phone(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "category_replay", Role.OPERATOR)
    login(ticket_client, operator.login)
    category = ticket_client.get("/api/v1/ticket-categories").json()[0]
    payload = {
        "ticket_type": "MANUAL",
        "category_id": category["id"],
        "caller_phone": "(000) 123 456",
        "client_request_id": str(uuid4()),
    }
    first = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
    assert first.status_code == 201, first.text
    replay = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={**payload, "caller_phone": "other"},
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={**payload, "client_request_id": str(uuid4()), "caller_phone": "  bad  "},
        ).status_code
        == 422
    )


def test_0015_upgrade_preserves_existing_ticket(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0014_ticket_deletion")
    engine = create_engine(isolated_postgres_url)
    try:
        author = seed_legacy_user(engine, "category_historical", Role.OPERATOR)
        ticket_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO tickets
                (id, ticket_type, description, status, priority, created_by,
                 client_request_id, version)
                VALUES (:id, 'INTERNAL', 'Historical description', 'OPEN',
                        'NORMAL', :author, :request, 1)"""),
                {"id": ticket_id, "author": author, "request": uuid4()},
            )
        command.upgrade(config, "head")
        with Session(engine) as db:
            ticket = db.get(Ticket, ticket_id)
            assert ticket is not None
            assert ticket.category_id is None and ticket.category_name is None
            assert ticket.caller_phone is None
            assert ticket.description == "Historical description"
            assert db.scalar(select(func.count()).select_from(TicketCategory)) == 8
    finally:
        engine.dispose()


def test_0018_upgrade_preserves_historical_decisions(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0017_ticket_callbacks")
    engine = create_engine(isolated_postgres_url)
    try:
        engineer = seed_legacy_user(engine, "reason_historical", Role.ENGINEER)
        ticket_id, decision_id = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO tickets
                (id, ticket_type, description, status, priority, created_by,
                 client_request_id, version)
                VALUES (:id, 'INTERNAL', 'Historical decision', 'OPEN',
                        'NORMAL', :engineer, :request, 1)"""),
                {"id": ticket_id, "engineer": engineer, "request": uuid4()},
            )
            connection.execute(
                text("""INSERT INTO ticket_decisions
                (id, ticket_id, engineer_id, decision_text, performed_actions)
                VALUES (:id, :ticket_id, :engineer, 'Historical verdict', 'Historical actions')"""),
                {"id": decision_id, "ticket_id": ticket_id, "engineer": engineer},
            )
        command.upgrade(config, "head")
        with engine.begin() as connection:
            historical = connection.execute(
                text("SELECT reason, performed_actions FROM ticket_decisions WHERE id = :id"),
                {"id": decision_id},
            ).one()
            assert historical == (None, "Historical actions")
            connection.execute(
                text("""INSERT INTO ticket_decisions
                (id, ticket_id, engineer_id, decision_text, reason, performed_actions)
                VALUES (:id, :ticket_id, :engineer, 'New verdict', 'CONSULTATION', NULL)"""),
                {"id": uuid4(), "ticket_id": ticket_id, "engineer": engineer},
            )
            actual_head = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert actual_head == ScriptDirectory.from_config(config).get_current_head()
    finally:
        engine.dispose()


def test_category_disable_and_ticket_create_serialize(ticket_engine: Engine) -> None:
    """A pending non-key UPDATE must hold creation until the inactive row is visible."""
    operator = seed_user(ticket_engine, "category_race_operator", Role.OPERATOR)
    with Session(ticket_engine) as db:
        category_id = db.scalar(
            select(TicketCategory.id).where(TicketCategory.name == "Нет интернета")
        )
    assert category_id is not None
    payload = CreateTicket(
        ticket_type=TicketType.MANUAL,
        category_id=category_id,
        client_request_id=uuid4(),
    )
    backend_pids: Queue[int] = Queue()

    def create_while_disabling() -> int:
        with Session(ticket_engine) as db:
            pid = db.scalar(text("SELECT pg_backend_pid()"))
            assert pid is not None
            backend_pids.put(pid)
            try:
                create_ticket(db, operator.id, payload)
            except ApiError as error:
                db.rollback()
                return error.status_code
            return 201

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(ticket_engine) as admin:
            admin.execute(
                text("UPDATE ticket_categories SET is_active = false WHERE id = :category_id"),
                {"category_id": category_id},
            )
            pending = pool.submit(create_while_disabling)
            try:
                pid = backend_pids.get(timeout=5)
                deadline = monotonic() + 10
                with ticket_engine.connect() as observer:
                    while monotonic() < deadline:
                        wait_event = observer.scalar(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                            {"pid": pid},
                        )
                        if wait_event == "Lock":
                            break
                        sleep(0.05)
                    else:
                        pytest.fail("Ticket creation did not wait for the category update")
                admin.commit()
                assert pending.result(timeout=10) == 422
            finally:
                admin.rollback()
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0


def test_ticket_list_uses_historical_subscriber_snapshots_without_billing_lookup(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "list_subscriber_operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "list_subscriber_engineer", Role.ENGINEER)
    personal, _ = seed_snapshot(ticket_engine, operator)
    with Session(ticket_engine, expire_on_commit=False) as db:
        organization = SubscriberSnapshot(
            account_no="000124",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=operator.id,
            full_name=None,
            organization_name="Synthetic Organization",
            contracts=[],
            services=[],
        )
        unnamed = SubscriberSnapshot(
            account_no="000125",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=operator.id,
            full_name="  ",
            organization_name=None,
            contracts=[],
            services=[],
        )
        db.add_all([organization, unnamed])
        db.commit()

    login(ticket_client, operator.login)
    linked_numbers = {}
    for snapshot in (personal, organization, unnamed):
        result = create(ticket_client, snapshot)
        assert result.status_code == 201, result.text
        linked_numbers[result.json()["number"]] = snapshot.account_no
    for kind in ("MANUAL", "INTERNAL"):
        result = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind,
                "subject": f"Synthetic {kind}",
                "description": "Synthetic request",
                "client_request_id": str(uuid4()),
            },
        )
        assert result.status_code == 201, result.text

    def unavailable_provider():
        pytest.fail("Ticket list must not initialize a BillingProvider")

    ticket_client.app.dependency_overrides[get_billing_provider] = unavailable_provider
    snapshot_selects = []

    def count_snapshot_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "subscriber_snapshots" in statement:
            snapshot_selects.append(statement)

    event.listen(ticket_engine, "before_cursor_execute", count_snapshot_selects)
    try:
        page = ticket_client.get("/api/v1/tickets", params={"page": 1, "size": 10})
    finally:
        event.remove(ticket_engine, "before_cursor_execute", count_snapshot_selects)
    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.json()["total"] == 5
    assert len(snapshot_selects) == 1
    items = {item["account_no"]: item for item in page.json()["items"] if item["account_no"]}
    assert items["000123"]["subscriber_name"] == "Synthetic Subscriber"
    assert items["000124"]["subscriber_name"] == "Synthetic Organization"
    assert items["000125"]["subscriber_name"] is None
    assert len(linked_numbers) == 3
    assert all(
        item["subscriber_name"] is None and item["account_no"] is None
        for item in page.json()["items"]
        if item["ticket_type"] != "SUBSCRIBER_LINKED"
    )
    for kind in ("MANUAL", "INTERNAL"):
        filtered = ticket_client.get("/api/v1/tickets", params={"ticket_type": kind, "size": 1})
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["ticket_type"] == kind
    linked_page = ticket_client.get(
        "/api/v1/tickets", params={"ticket_type": "SUBSCRIBER_LINKED", "size": 1, "page": 2}
    )
    assert linked_page.status_code == 200
    assert linked_page.json()["total"] == 3
    assert linked_page.json()["items"][0]["subscriber_name"] in {
        "Synthetic Subscriber",
        "Synthetic Organization",
        None,
    }
    login(ticket_client, engineer.login)
    assert ticket_client.get("/api/v1/tickets", params={"size": 2}).json()["total"] == 5
    restricted = seed_user(ticket_engine, "list_restricted", Role.OPERATOR)
    with Session(ticket_engine) as db:
        db.get(User, restricted.id).permissions_customized = True
        db.add(UserPermission(user_id=restricted.id, permission=Permission.TICKET_READ_OWN))
        db.commit()
    login(ticket_client, restricted.login)
    restricted_page = ticket_client.get("/api/v1/tickets")
    assert restricted_page.status_code == 200
    assert restricted_page.json()["total"] == 0


def test_ticket_search_filters_history_pagination_and_rbac(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    owner = seed_user(ticket_engine, "search_owner", Role.OPERATOR)
    colleague = seed_user(ticket_engine, "search_colleague", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "search_engineer", Role.ENGINEER)
    first_day = datetime(2026, 10, 10, 12, tzinfo=UTC)
    with Session(ticket_engine, expire_on_commit=False) as db:
        category = db.scalar(select(TicketCategory).where(TicketCategory.name == "Нет интернета"))
        assert category is not None
        person = SubscriberSnapshot(
            account_no="0001499",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=owner.id,
            full_name="Иван Тестовый",
            contracts=[],
            services=[],
        )
        organization = SubscriberSnapshot(
            account_no="0002500",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=colleague.id,
            organization_name="Синтетическая организация",
            contracts=[],
            services=[],
        )
        db.add_all([person, organization])
        db.flush()
        person_ticket = Ticket(
            ticket_type=TicketType.SUBSCRIBER_LINKED,
            account_no=person.account_no,
            subscriber_snapshot_id=person.id,
            subject=category.name,
            category_id=category.id,
            category_name=category.name,
            description="wire-down на тестовой линии",
            caller_phone="+7 (900) 123-45-67",
            status=TicketStatus.OPEN,
            priority="NORMAL",
            created_by=owner.id,
            client_request_id=uuid4(),
            version=1,
            created_at=first_day,
            updated_at=first_day,
        )
        organization_ticket = Ticket(
            ticket_type=TicketType.SUBSCRIBER_LINKED,
            account_no=organization.account_no,
            subscriber_snapshot_id=organization.id,
            subject="Услуга организации",
            description="Другая синтетическая проблема",
            status=TicketStatus.IN_PROGRESS,
            priority="HIGH",
            created_by=colleague.id,
            client_request_id=uuid4(),
            version=1,
            created_at=first_day + timedelta(days=2),
            updated_at=first_day + timedelta(days=2),
        )
        manual_ticket = Ticket(
            ticket_type=TicketType.MANUAL,
            subject="Проверка линии",
            description="wire-down в другом месте",
            status=TicketStatus.OPEN,
            priority="NORMAL",
            created_by=owner.id,
            client_request_id=uuid4(),
            version=1,
            created_at=first_day + timedelta(days=4),
            updated_at=first_day + timedelta(days=4),
        )
        deleted_ticket = Ticket(
            ticket_type=TicketType.INTERNAL,
            subject="wire-down удалённая задача",
            description="Только корзина",
            status=TicketStatus.OPEN,
            priority="NORMAL",
            created_by=owner.id,
            deleted_at=first_day + timedelta(days=5),
            deleted_by=owner.id,
            client_request_id=uuid4(),
            version=1,
            created_at=first_day + timedelta(days=3),
            updated_at=first_day + timedelta(days=5),
        )
        db.add_all([person_ticket, organization_ticket, manual_ticket, deleted_ticket])
        db.flush()
        db.add(
            TicketResolution(
                ticket_id=organization_ticket.id,
                engineer_id=engineer.id,
                decision_text="Optic restored",
                performed_actions="wire-down fix applied",
            )
        )
        db.commit()

    def numbers(query: str, **filters) -> tuple[int, list[int]]:
        response = ticket_client.get("/api/v1/tickets", params={"q": query, **filters})
        assert response.status_code == 200, response.text
        return response.json()["total"], [item["number"] for item in response.json()["items"]]

    def unavailable_provider():
        pytest.fail("Search must not initialize a BillingProvider")

    ticket_client.app.dependency_overrides[get_billing_provider] = unavailable_provider
    login(ticket_client, colleague.login)
    assert numbers("1499") == (1, [person_ticket.number])
    assert person_ticket.number in numbers(str(person_ticket.number))[1]
    assert numbers("ИВАН") == (1, [person_ticket.number])
    assert numbers("ОРГАНИЗАЦИЯ") == (1, [organization_ticket.number])
    assert numbers("79001234567") == (1, [person_ticket.number])
    assert numbers("900 123-45-67") == (1, [person_ticket.number])
    assert numbers("нет ИНТЕРНЕТА") == (1, [person_ticket.number])
    assert numbers("другая СИНТЕТИЧЕСКАЯ") == (1, [organization_ticket.number])
    assert numbers("restored") == (1, [organization_ticket.number])
    assert numbers("fix applied") == (1, [organization_ticket.number])
    assert numbers("wire-down") == (
        3,
        [manual_ticket.number, organization_ticket.number, person_ticket.number],
    )
    assert numbers("wire-down", size=1, page=2) == (3, [organization_ticket.number])
    assert numbers("wire-down", size=1, page=3) == (3, [person_ticket.number])
    assert numbers("wire-down", category="нет интернета", active_only="true") == (
        1,
        [person_ticket.number],
    )
    assert numbers(
        "wire-down",
        category_id=str(category.id),
        status="OPEN",
        ticket_type="SUBSCRIBER_LINKED",
        created_from="2026-10-10T00:00:00+03:00",
        created_before="2026-10-11T00:00:00+03:00",
    ) == (1, [person_ticket.number])
    assert numbers("wire-down", assignee=str(engineer.id)) == (0, [])
    assert numbers("удалённая") == (0, [])
    assert numbers("%") == (0, [])
    assert numbers("%' OR TRUE--") == (0, [])
    assert numbers("   ")[0] == 3
    assert (
        ticket_client.get(
            "/api/v1/tickets", params={"created_from": "2026-10-10T00:00:00"}
        ).status_code
        == 422
    )
    assert (
        ticket_client.get(
            "/api/v1/tickets",
            params={
                "created_from": "2026-10-12T00:00:00Z",
                "created_before": "2026-10-11T00:00:00Z",
            },
        ).status_code
        == 422
    )

    with Session(ticket_engine) as db:
        db.get(User, owner.id).permissions_customized = True
        db.add(UserPermission(user_id=owner.id, permission=Permission.TICKET_READ_OWN))
        db.commit()
    login(ticket_client, owner.login)
    assert numbers("wire-down")[0] == 2
    assert numbers("restored") == (0, [])


def test_engineer_claim_race_release_and_queues(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "queue_operator", Role.OPERATOR)
    first = seed_user(ticket_engine, "queue_first", Role.ENGINEER)
    second = seed_user(ticket_engine, "queue_second", Role.ENGINEER)
    login(ticket_client, operator.login)
    target = make_manual_ticket(ticket_client, subject="Synthetic shared queue")
    other = make_manual_ticket(ticket_client, subject="Synthetic second ticket")
    path = f"/api/v1/tickets/{target['id']}"
    assert (
        ticket_client.post(
            path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 403
    )

    def attempt(actor: User) -> str:
        with Session(ticket_engine) as db:
            try:
                claim_ticket(db, actor.id, UUID(target["id"]), 1)
                return "success"
            except ApiError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (first, second)))
    assert sorted(outcomes) == ["success", "ticket_already_claimed"]
    winner = first if outcomes[0] == "success" else second
    loser = second if winner == first else first
    login(ticket_client, winner.login)
    owned = ticket_client.get(path)
    assert owned.status_code == 200
    assert owned.json()["assigned_engineer_id"] == str(winner.id)
    assert owned.json()["assigned_engineer_name"] == winner.display_name
    assert owned.json()["status"] == "IN_PROGRESS"
    assert owned.json()["version"] == 2
    retry = ticket_client.post(path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'})
    assert retry.status_code == 200 and retry.json()["version"] == 2
    waiting = ticket_client.get("/api/v1/tickets", params={"active_only": True, "unassigned": True})
    mine = ticket_client.get(
        "/api/v1/tickets", params={"active_only": True, "assignee": str(winner.id)}
    )
    assert waiting.json()["total"] == 1 and waiting.json()["items"][0]["id"] == other["id"]
    assert mine.json()["total"] == 1 and mine.json()["items"][0]["id"] == target["id"]
    assert mine.json()["items"][0]["work_started_at"] is not None
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(
                    TicketEvent.ticket_id == UUID(target["id"]),
                    TicketEvent.event_type == "engineer_claimed",
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(
                    TicketAudit.ticket_id == UUID(target["id"]),
                    TicketAudit.action == "engineer_claimed",
                )
            )
            == 1
        )

    login(ticket_client, loser.login)
    conflict = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "ticket_already_claimed"
    assert (
        ticket_client.post(
            path + "/release", headers={**csrf(ticket_client), "If-Match": '"2"'}
        ).status_code
        == 409
    )
    login(ticket_client, winner.login)
    released = ticket_client.post(
        path + "/release", headers={**csrf(ticket_client), "If-Match": '"2"'}
    )
    assert released.status_code == 200 and released.json()["version"] == 3
    assert released.json()["assigned_engineer_id"] is None
    assert released.json()["status"] == "IN_PROGRESS"
    waiting = ticket_client.get("/api/v1/tickets", params={"active_only": True, "unassigned": True})
    assert waiting.json()["total"] == 2
    login(ticket_client, loser.login)
    reclaimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"3"'}
    )
    assert reclaimed.status_code == 200 and reclaimed.json()["assigned_engineer_id"] == str(
        loser.id
    )
    mine = ticket_client.get(
        "/api/v1/tickets", params={"active_only": True, "assignee": str(loser.id)}
    )
    assert mine.json()["total"] == 1 and mine.json()["items"][0]["work_started_at"] is not None
    with Session(ticket_engine) as db:
        events = db.scalars(
            select(TicketEvent.event_type)
            .where(TicketEvent.ticket_id == UUID(target["id"]))
            .order_by(TicketEvent.ticket_version)
        ).all()
    assert events == ["ticket_created", "engineer_claimed", "engineer_released", "engineer_claimed"]


def test_claim_permissions_closed_deleted_and_admin_reassignment(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "claim_operator", Role.OPERATOR)
    first = seed_user(ticket_engine, "claim_first", Role.ENGINEER)
    second = seed_user(ticket_engine, "claim_second", Role.ENGINEER)
    denied = seed_user(ticket_engine, "claim_denied", Role.ENGINEER)
    admin = seed_user(ticket_engine, "claim_admin", Role.ADMIN)
    with Session(ticket_engine) as db:
        row = db.get(User, denied.id)
        row.permissions_customized = True
        db.add(UserPermission(user_id=denied.id, permission=Permission.TICKET_READ))
        db.commit()
    login(ticket_client, operator.login)
    target = make_manual_ticket(ticket_client, subject="Synthetic RBAC claim")
    deleted = make_manual_ticket(ticket_client, subject="Synthetic deleted claim")
    path = f"/api/v1/tickets/{target['id']}"

    def headers(version: int) -> dict[str, str]:
        return {**csrf(ticket_client), "If-Match": f'"{version}"'}

    for actor in (denied, admin):
        login(ticket_client, actor.login)
        assert ticket_client.post(path + "/claim", headers=headers(1)).status_code == 403
    login(ticket_client, first.login)
    assert ticket_client.post(path + "/claim", headers=headers(1)).status_code == 200
    login(ticket_client, second.login)
    assert (
        ticket_client.post(
            path + "/assignment", headers=headers(2), json={"engineer_id": str(second.id)}
        ).status_code
        == 409
    )
    assert ticket_client.post(path + "/start", headers=headers(2)).status_code == 409
    login(ticket_client, admin.login)
    reassigned = ticket_client.post(
        path + "/assignment", headers=headers(2), json={"engineer_id": str(second.id)}
    )
    assert reassigned.status_code == 200 and reassigned.json()["assigned_engineer_id"] == str(
        second.id
    )
    login(ticket_client, second.login)
    decision = ticket_client.post(
        path + "/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Synthetic resolution", "performed_actions": "Synthetic work"},
    )
    assert decision.status_code == 201, decision.text
    current_version = ticket_client.get(path).json()["version"]
    resolved = ticket_client.post(
        path + "/resolve",
        headers=headers(current_version),
        json={"decision_text": "Synthetic resolution", "performed_actions": "Synthetic work"},
    )
    assert resolved.status_code == 200, resolved.text
    closed = ticket_client.post(
        path + "/close",
        headers=headers(resolved.json()["version"]),
        json={"decision_id": resolved.json()["resolved_decision_id"], "reason": "Manual task"},
    )
    assert closed.status_code == 200
    assert (
        ticket_client.post(path + "/claim", headers=headers(closed.json()["version"])).status_code
        == 409
    )
    assert (
        ticket_client.post(path + "/release", headers=headers(closed.json()["version"])).status_code
        == 409
    )
    login(ticket_client, admin.login)
    assert (
        ticket_client.request(
            "DELETE", f"/api/v1/tickets/{deleted['id']}", headers=headers(1), json={}
        ).status_code
        == 200
    )
    login(ticket_client, first.login)
    assert (
        ticket_client.post(f"/api/v1/tickets/{deleted['id']}/claim", headers=headers(1)).status_code
        == 404
    )


def test_resolved_verdict_permissions_history_and_queues(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "resolution_operator", Role.OPERATOR)
    editor = seed_user(ticket_engine, "resolution_editor", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "resolution_engineer", Role.ENGINEER)
    other = seed_user(ticket_engine, "resolution_other", Role.ENGINEER)
    grant_ticket_permission(ticket_engine, editor, Permission.TICKET_EDIT_ALL)
    login(ticket_client, operator.login)
    ticket = make_manual_ticket(ticket_client, subject="Synthetic verdict")
    path = f"/api/v1/tickets/{ticket['id']}"

    login(ticket_client, engineer.login)
    claimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert claimed.status_code == 200, claimed.text
    version = claimed.json()["version"]
    payload = {"decision_text": "Synthetic line repaired", "performed_actions": "Synthetic check"}
    login(ticket_client, other.login)
    foreign = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json=payload,
    )
    assert foreign.status_code == 403
    login(ticket_client, engineer.login)
    assert (
        ticket_client.post(
            path + "/resolve", headers={"If-Match": f'"{version}"'}, json=payload
        ).status_code
        == 403
    )
    assert (
        ticket_client.post(path + "/resolve", headers=csrf(ticket_client), json=payload).status_code
        == 428
    )
    assert (
        ticket_client.post(
            path + "/resolve",
            headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
            json={**payload, "decision_text": "   "},
        ).status_code
        == 422
    )
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json=payload,
    )
    assert resolved.status_code == 200, resolved.text
    data = resolved.json()
    assert data["status"] == "RESOLVED"
    assert data["resolved_by"] == str(engineer.id)
    assert data["resolved_at"] and data["resolved_decision_id"]
    assert data["closed_at"] is None
    with Session(ticket_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE tickets SET resolved_decision_id = :wrong WHERE id = :ticket_id"),
                {"wrong": uuid4(), "ticket_id": UUID(ticket["id"])},
            )
        db.rollback()
    assert ticket_client.get(path).json()["status"] == "RESOLVED"
    assert ticket_client.get("/api/v1/tickets", params={"status": "RESOLVED"}).json()["total"] == 1
    assert ticket_client.get("/api/v1/tickets", params={"active_only": True}).json()["total"] == 0
    assert (
        ticket_client.get(
            "/api/v1/tickets", params={"active_only": True, "assignee": str(engineer.id)}
        ).json()["total"]
        == 0
    )
    assert ticket_client.get("/api/v1/tickets", params={"q": "line repaired"}).json()["total"] == 1
    assert (
        ticket_client.post(
            path + "/resolve",
            headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
            json=payload,
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            path + "/claim", headers={**csrf(ticket_client), "If-Match": f'"{data["version"]}"'}
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            path + "/release", headers={**csrf(ticket_client), "If-Match": f'"{data["version"]}"'}
        ).status_code
        == 409
    )
    assert (
        ticket_client.post(
            path + "/assignment",
            headers={**csrf(ticket_client), "If-Match": f'"{data["version"]}"'},
            json={"engineer_id": str(other.id)},
        ).status_code
        == 409
    )
    decisions = ticket_client.get(path + "/decisions").json()
    assert (
        decisions["total"] == 1
        and decisions["items"][0]["decision_text"] == payload["decision_text"]
    )
    assert decisions["items"][0].get("reason") == "NOT_ESTABLISHED", decisions
    assert decisions["items"][0]["performed_actions"] == "Synthetic check"
    assert [
        item["event_type"] for item in ticket_client.get(path + "/history").json()["items"]
    ].count("ticket_resolved") == 1
    assert [item["action"] for item in ticket_client.get(path + "/audit").json()["items"]].count(
        "ticket_resolved"
    ) == 1

    login(ticket_client, editor.login)
    assert (
        ticket_client.post(
            path + "/resolve",
            headers={**csrf(ticket_client), "If-Match": f'"{data["version"]}"'},
            json=payload,
        ).status_code
        == 403
    )
    edited = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": f'"{data["version"]}"'},
        json={"description": "Synthetic corrected description"},
    )
    assert edited.status_code == 200, edited.text
    login(ticket_client, engineer.login)
    revision = ticket_client.post(
        path + "/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Synthetic revised verdict", "reason": "CABLE_BREAK"},
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["reason"] == "CABLE_BREAK"
    assert revision.json()["performed_actions"] is None
    assert (
        ticket_client.post(
            path + "/decisions",
            headers=csrf(ticket_client),
            json={"decision_text": "Invalid reason", "reason": "GUESSED_FROM_ONU"},
        ).status_code
        == 422
    )
    latest = ticket_client.get(path).json()
    assert latest["resolved_decision_id"] == revision.json()["id"]
    assert latest["resolved_at"] == data["resolved_at"]
    assert ticket_client.get(path + "/decisions").json()["total"] == 2
    assert (
        ticket_client.post(
            path + "/close",
            headers={**csrf(ticket_client), "If-Match": f'"{latest["version"]}"'},
            json={"decision_id": data["resolved_decision_id"], "reason": "Manual task"},
        ).status_code
        == 422
    )
    closed = ticket_client.post(
        path + "/close",
        headers={**csrf(ticket_client), "If-Match": f'"{latest["version"]}"'},
        json={"decision_id": revision.json()["id"], "reason": "Manual task"},
    )
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"


def test_concurrent_resolution_records_only_one_verdict(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    creator = seed_user(ticket_engine, "race_resolution_creator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "race_resolution_engineer", Role.ENGINEER)
    login(ticket_client, creator.login)
    ticket = make_manual_ticket(ticket_client)
    login(ticket_client, engineer.login)
    claimed = ticket_client.post(
        f"/api/v1/tickets/{ticket['id']}/claim",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
    )
    assert claimed.status_code == 200, claimed.text
    payload = AddDecision(decision_text="Synthetic recovered service", performed_actions="Checked")

    def attempt(_index: int) -> str:
        with Session(ticket_engine) as db:
            try:
                resolve_ticket(
                    db, engineer.id, UUID(ticket["id"]), payload, claimed.json()["version"]
                )
                return "resolved"
            except ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    assert sorted(outcomes) == ["resolved", "version_conflict"]
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketResolution)
                .where(TicketResolution.ticket_id == UUID(ticket["id"]))
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(
                    TicketEvent.ticket_id == UUID(ticket["id"]),
                    TicketEvent.event_type == "ticket_resolved",
                )
            )
            == 1
        )


def test_callback_queue_repeated_calls_and_second_engineer_cycle(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    creator = seed_user(ticket_engine, "callback_creator", Role.OPERATOR)
    caller = seed_user(ticket_engine, "callback_staff", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "callback_engineer", Role.ENGINEER)
    grant_ticket_permission(ticket_engine, caller, Permission.TICKET_CALLBACK)
    snapshot, _ = seed_snapshot(ticket_engine, creator)
    login(ticket_client, creator.login)
    created = create(ticket_client, snapshot)
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    login(ticket_client, engineer.login)
    claimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert claimed.status_code == 200, claimed.text
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{claimed.json()["version"]}"'},
        json={"decision_text": "First synthetic fix", "performed_actions": "First check"},
    )
    assert resolved.status_code == 200, resolved.text
    decision_one = resolved.json()["resolved_decision_id"]
    queue = ticket_client.get("/api/v1/tickets", params={"callback_queue": True}).json()
    assert queue["total"] == 1 and queue["items"][0]["id"] == created.json()["id"]
    assert (
        ticket_client.post(
            path + "/close",
            headers={**csrf(ticket_client), "If-Match": f'"{resolved.json()["version"]}"'},
            json={"decision_id": decision_one, "reason": "Bypass"},
        ).json()["code"]
        == "callback_required"
    )
    login(ticket_client, caller.login)
    request_id = str(uuid4())
    no_answer = {"client_request_id": request_id, "outcome": "NO_ANSWER"}
    headers = {**csrf(ticket_client), "If-Match": f'"{resolved.json()["version"]}"'}
    assert (
        ticket_client.post(
            path + "/callbacks", json=no_answer, headers={"If-Match": headers["If-Match"]}
        ).status_code
        == 403
    )
    first = ticket_client.post(path + "/callbacks", json=no_answer, headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "RESOLVED", first.text
    replay = ticket_client.post(path + "/callbacks", json=no_answer, headers=headers)
    assert replay.status_code == 200 and replay.json()["version"] == first.json()["version"]
    assert (
        ticket_client.post(
            path + "/callbacks",
            headers={**csrf(ticket_client), "If-Match": f'"{first.json()["version"]}"'},
            json={"client_request_id": str(uuid4()), "outcome": "STILL_BROKEN"},
        ).status_code
        == 422
    )
    returned = ticket_client.post(
        path + "/callbacks",
        headers={**csrf(ticket_client), "If-Match": f'"{first.json()["version"]}"'},
        json={
            "client_request_id": str(uuid4()),
            "outcome": "STILL_BROKEN",
            "comment": "Synthetic issue remains",
        },
    )
    assert returned.status_code == 200 and returned.json()["status"] == "IN_PROGRESS", returned.text
    assert returned.json()["assigned_engineer_id"] is None
    assert (
        ticket_client.get(
            "/api/v1/tickets", params={"active_only": True, "unassigned": True}
        ).json()["total"]
        == 1
    )
    assert (
        ticket_client.get("/api/v1/tickets", params={"callback_queue": True}).json()["total"] == 0
    )
    login(ticket_client, engineer.login)
    reclaimed = ticket_client.post(
        path + "/claim",
        headers={**csrf(ticket_client), "If-Match": f'"{returned.json()["version"]}"'},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    second = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{reclaimed.json()["version"]}"'},
        json={"decision_text": "Second synthetic fix", "performed_actions": "Second check"},
    )
    assert second.status_code == 200 and second.json()["resolved_decision_id"] != decision_one
    login(ticket_client, caller.login)
    confirmed = ticket_client.post(
        path + "/callbacks",
        headers={**csrf(ticket_client), "If-Match": f'"{second.json()["version"]}"'},
        json={
            "client_request_id": str(uuid4()),
            "outcome": "CONFIRMED",
            "comment": "Synthetic confirmation",
        },
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CLOSED", confirmed.text
    assert confirmed.json()["closed_by"] == str(caller.id)
    assert (
        ticket_client.get("/api/v1/tickets", params={"callback_queue": True}).json()["total"] == 0
    )
    calls = ticket_client.get(path + "/callbacks").json()
    assert calls["total"] == 3
    assert [item["outcome"] for item in calls["items"]] == [
        "CONFIRMED",
        "STILL_BROKEN",
        "NO_ANSWER",
    ]
    assert ticket_client.get(path + "/decisions").json()["total"] == 2
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketCallback)
                .where(TicketCallback.ticket_id == UUID(created.json()["id"]))
            )
            == 3
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(
                    TicketEvent.ticket_id == UUID(created.json()["id"]),
                    TicketEvent.event_type == "callback_confirmed_closed",
                )
            )
            == 1
        )


def test_concurrent_callback_conflict_has_one_durable_result(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    creator = seed_user(ticket_engine, "callback_race_creator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "callback_race_engineer", Role.ENGINEER)
    caller = seed_user(ticket_engine, "callback_race_staff", Role.OPERATOR)
    grant_ticket_permission(ticket_engine, caller, Permission.TICKET_CALLBACK)
    snapshot, _ = seed_snapshot(ticket_engine, creator)
    login(ticket_client, creator.login)
    created = create(ticket_client, snapshot)
    path = f"/api/v1/tickets/{created.json()['id']}"
    login(ticket_client, engineer.login)
    claimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{claimed.json()["version"]}"'},
        json={"decision_text": "Synthetic fix", "performed_actions": "Check"},
    )
    assert resolved.status_code == 200, resolved.text

    def attempt(outcome: str) -> str:
        with Session(ticket_engine) as db:
            try:
                record_callback(
                    db,
                    caller.id,
                    UUID(created.json()["id"]),
                    RecordCallback(
                        client_request_id=uuid4(),
                        outcome=outcome,
                        comment="Synthetic issue remains" if outcome == "STILL_BROKEN" else None,
                    ),
                    resolved.json()["version"],
                )
                return outcome
            except ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("CONFIRMED", "STILL_BROKEN")))
    assert "version_conflict" in results
    assert len(set(results)) == 2
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketCallback)
                .where(TicketCallback.ticket_id == UUID(created.json()["id"]))
            )
            == 1
        )


class RecordingFieldGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, message: str) -> DispatchReceipt:
        self.calls.append((chat_id, message))
        return DispatchReceipt(chat_id=chat_id, message_id="701")


class RejectingFieldGateway:
    def __init__(self, uncertain: bool = False) -> None:
        self.calls = 0
        self.uncertain = uncertain

    def send_message(self, _chat_id: str, _message: str) -> DispatchReceipt:
        self.calls += 1
        if self.uncertain:
            raise DispatchUncertain
        raise DispatchRejected("telegram_http_400")


def test_field_work_requires_explicit_confirmed_telegram_delivery(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "field_dispatch_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(
        ticket_engine,
        engineer,
        phone="+000000000",
        actual_address="г. Тест, ул. Безопасная",
        house_number="20",
    )
    gateway = RecordingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: gateway
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    claimed = ticket_client.post(
        f"{path}/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert claimed.status_code == 200, claimed.text

    requested = ticket_client.post(
        f"{path}/field-work-request",
        headers={**csrf(ticket_client), "If-Match": '"2"'},
        json={"reason": "Synthetic on-site cable inspection"},
    )
    assert requested.status_code == 200, requested.text
    assert gateway.calls == []
    latest = ticket_client.get(f"{path}/field-work-handoffs/latest")
    assert latest.status_code == 200, latest.text
    handoff = latest.json()["handoff"]
    assert handoff["delivery_status"] == "DRAFT"
    assert handoff["ready_to_send"] is True
    assert "Заявка #" in handoff["message_preview"]
    assert "Абонент: Synthetic Subscriber" in handoff["message_preview"]
    assert "Контакт NetUP: +000000000" in handoff["message_preview"]
    assert "Адрес: г. Тест, ул. Безопасная, д. 20" in handoff["message_preview"]
    assert "Отправил инженер: Field_Dispatch_Engineer" in handoff["message_preview"]

    send_path = f"{path}/field-work-handoffs/{handoff['id']}/send"
    request_id = str(uuid4())
    assert ticket_client.post(send_path, json={"client_request_id": request_id}).status_code == 403
    sent = ticket_client.post(
        send_path, headers=csrf(ticket_client), json={"client_request_id": request_id}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["delivery_status"] == "SENT"
    assert sent.json()["telegram_message_id"] == 701
    assert sent.json()["sent_by"] == str(engineer.id)
    assert len(gateway.calls) == 1
    for replay_id in (request_id, str(uuid4())):
        replay = ticket_client.post(
            send_path, headers=csrf(ticket_client), json={"client_request_id": replay_id}
        )
        assert replay.status_code == 200, replay.text
    assert len(gateway.calls) == 1

    second = ticket_client.post(
        f"{path}/field-work-request",
        headers={**csrf(ticket_client), "If-Match": '"3"'},
        json={"reason": "Synthetic repeat dispatch"},
    )
    assert second.status_code == 200, second.text
    second_handoff = ticket_client.get(f"{path}/field-work-handoffs/latest").json()["handoff"]
    second_path = f"{path}/field-work-handoffs/{second_handoff['id']}/send"
    rejected_gateway = RejectingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: rejected_gateway
    failed = ticket_client.post(
        second_path, headers=csrf(ticket_client), json={"client_request_id": str(uuid4())}
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["delivery_status"] == "FAILED"
    assert failed.json()["last_error_code"] == "telegram_http_400"

    uncertain_gateway = RejectingFieldGateway(uncertain=True)
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: uncertain_gateway
    uncertain = ticket_client.post(
        second_path, headers=csrf(ticket_client), json={"client_request_id": str(uuid4())}
    )
    assert uncertain.status_code == 200, uncertain.text
    assert uncertain.json()["delivery_status"] == "UNCERTAIN"
    blocked = ticket_client.post(
        second_path, headers=csrf(ticket_client), json={"client_request_id": str(uuid4())}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "field_send_uncertain"
    assert uncertain_gateway.calls == 1

    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(FieldWorkHandoff)) == 2
        assert db.scalar(select(func.count()).select_from(FieldWorkDeliveryAttempt)) == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(
                    TicketAudit.action == "field_work_sent",
                    TicketAudit.ticket_id == UUID(created.json()["id"]),
                )
            )
            == 1
        )
