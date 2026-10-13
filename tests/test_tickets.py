"""Real PostgreSQL Ticket Engine and API acceptance tests in disposable databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.main import create_app
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.tickets.models import (
    ConnectionSnapshot,
    SubscriberSnapshot,
    Ticket,
    TicketAudit,
    TicketEvent,
)
from app.modules.tickets.service import start_ticket

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


def seed_snapshot(
    engine: Engine, user: User, count: int = 1
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
        seed_user(engine, "operator", Role.OPERATOR)
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
    assert ticket_client.get(f"/api/v1/tickets/{ticket_id}").status_code == 403
    assert (
        ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json={}).status_code
        == 403
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
