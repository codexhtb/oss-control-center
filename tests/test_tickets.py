"""Real PostgreSQL Ticket Engine and API acceptance tests in disposable databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.diagnostics import engine_dependency, onu_dependency
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.main import create_app
from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import DeviceMapping, MappingRegistry
from app.modules.diagnostics.correlation import DiagnosticReport, Observation
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
    TicketEvent,
)
from app.modules.tickets.schemas import CreateTicket
from app.modules.tickets.service import create_ticket, start_ticket
from app.modules.tickets.status import TicketType

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
    engine: Engine, user: User, count: int = 1, ip_groups: list | None = None
) -> tuple[SubscriberSnapshot, list[ConnectionSnapshot]]:
    with Session(engine, expire_on_commit=False) as db:
        snapshot = SubscriberSnapshot(
            account_no="000123",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=user.id,
            full_name="Synthetic Subscriber",
            phone=None,
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
):
    return client.post(
        "/api/v1/tickets",
        headers=csrf(client),
        json={
            "lookup_id": str(snapshot.id),
            "connection_snapshot_id": str(connection.id) if connection else None,
            "description": "Synthetic connectivity issue",
            "client_request_id": str(request_id or uuid4()),
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
    assert create(ticket_client, snapshot).status_code == 201
    first_page = ticket_client.get("/api/v1/tickets", params={"page": 1, "size": 1}).json()
    second_page = ticket_client.get("/api/v1/tickets", params={"page": 2, "size": 1}).json()
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]


def test_engineer_workflow_and_rbac(ticket_engine: Engine, ticket_client: TestClient) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "engineer", Role.ENGINEER)
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
        == 422
    )
    closed = ticket_client.post(
        f"/api/v1/tickets/{ticket_id}/close",
        headers={**csrf(ticket_client), "If-Match": f'"{version}"'},
        json={"decision_id": decision.json()["id"]},
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
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/history").json()["total"] == 8
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/audit").json()["total"] == 8
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
    for kind in TicketType:
        payload = {
            "ticket_type": kind.value,
            "subject": f"Synthetic {kind.value} task",
            "description": "Synthetic description",
            "client_request_id": str(uuid4()),
        }
        if kind == TicketType.SUBSCRIBER_LINKED:
            payload["lookup_id"] = str(snapshot.id)
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
    assert ticket_client.get("/api/v1/tickets/assignable-engineers").status_code == 403
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
            == 422
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
            json={"decision_id": decision.json()["id"]},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"
        assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/history").json()["total"] == 7
        assert ticket_client.get(f"/api/v1/tickets/{ticket_id}/audit").json()["total"] == 7
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
