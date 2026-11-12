"""Real PostgreSQL Ticket Engine and API acceptance tests in disposable databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader
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
from app.modules.field_dispatch.binding import chat_id_aliases, destination_chat_id
from app.modules.field_dispatch.gateway import (
    DispatchReceipt,
    DispatchRejected,
    DispatchUncertain,
    completion_prompt_text,
)
from app.modules.field_dispatch.group_control import process_group_control
from app.modules.field_dispatch.inbound import process_update
from app.modules.field_dispatch.models import (
    FieldDispatchChatMigration,
    FieldDispatchGroupBinding,
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldReportNotification,
    FieldReportNotificationRead,
    FieldRescheduleDialog,
    FieldWorkCompletionPrompt,
    FieldWorkDeliveryAttempt,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.field_dispatch.poller import (
    _delete_prompt,
    _send_prompt,
    _sync_callback,
    _sync_completion,
    _sync_dialog,
    _sync_schedule_card,
)
from app.modules.field_dispatch.rescheduling import (
    ABKHAZIA,
    parse_telegram_reschedule,
)
from app.modules.field_dispatch.service import finalize_send, prepare_send
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role
from app.modules.internal_tasks.models import (
    InternalTask,
    InternalTaskEvent,
    InternalTaskNotification,
)
from app.modules.reports.export import load_bundle
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
CONTACT = {"caller_phone": "+7 900 111 22 33", "city": "Тестовый город"}
MANUAL_CONTACT = {**CONTACT, "caller_full_name": "Тестовый обратившийся"}


@pytest.fixture
def ticket_engine(isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "head")
    engine = create_engine(isolated_postgres_url, connect_args={"connect_timeout": 5})
    with Session(engine) as db:
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.chat_id = "-5319791765"
        binding.chat_title = "Synthetic Group"
        binding.chat_type = "group"
        binding.status = "ACTIVE"
        db.commit()
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
    account_no: str = "000123",
) -> tuple[SubscriberSnapshot, list[ConnectionSnapshot]]:
    with Session(engine, expire_on_commit=False) as db:
        snapshot = SubscriberSnapshot(
            account_no=account_no,
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


def test_internal_tickets_are_invisible_to_operators_even_with_ticket_grants(
    ticket_engine: Engine, ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "internal_access_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "internal_access_operator", Role.OPERATOR)
    for permission in (
        Permission.TICKET_EDIT_ALL, Permission.TICKET_START, Permission.TICKET_FIELD_WORK,
        Permission.TICKET_ASSIGN, Permission.TICKET_DECISION,
    ):
        grant_ticket_permission(ticket_engine, operator, permission)

    login(ticket_client, engineer.login)
    internal = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json={
        "ticket_type": "INTERNAL", "subject": "Internal access sentinel",
        "description": "Synthetic internal details", "client_request_id": str(uuid4()),
    })
    assert internal.status_code == 201, internal.text
    internal_id = internal.json()["id"]
    path = f"/api/v1/tickets/{internal_id}"
    assert ticket_client.get(path).status_code == 200
    assert ticket_client.get("/api/v1/tickets", params={"ticket_type": "INTERNAL"}).json()[
        "total"
    ] == 1
    assert ticket_client.post(
        path + "/comments", headers=csrf(ticket_client), json={"body": "Synthetic comment"},
    ).status_code == 201

    login(ticket_client, operator.login)
    manual = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json={
        "ticket_type": "MANUAL", "caller_full_name": "Synthetic Caller",
        "caller_phone": "+7 900 000 00 01", "city": "Synthetic City",
        "client_request_id": str(uuid4()),
    })
    assert manual.status_code == 201, manual.text
    forbidden_create = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json={
        "ticket_type": "INTERNAL", "subject": "No access",
        "description": "Synthetic attempt", "client_request_id": str(uuid4()),
    })
    assert forbidden_create.status_code == 403
    for params in ({}, {"ticket_type": "INTERNAL"}, {"q": "Internal access sentinel"},
                   {"number": internal.json()["number"]}):
        page = ticket_client.get("/api/v1/tickets", params=params)
        assert page.status_code == 200 and page.json()["total"] == (1 if not params else 0)
        assert all(item["id"] != internal_id for item in page.json()["items"])
    events = ticket_client.get("/api/v1/tickets/events/recent")
    assert events.status_code == 200
    assert all(item["ticket_id"] != internal_id for item in events.json()["items"])
    for suffix in ("", "/comments", "/history", "/decisions", "/manual-diagnostics",
                   "/previous-tickets", "/field-work-handoffs/latest"):
        assert ticket_client.get(path + suffix).status_code == 404, suffix
    assert ticket_client.patch(path, headers={**csrf(ticket_client), "If-Match": '"1"'},
                               json={"subject": "Synthetic change"}).status_code == 404
    assert ticket_client.post(path + "/comments", headers=csrf(ticket_client),
                              json={"body": "Denied"}).status_code == 404
    assert ticket_client.post(path + "/start", headers={**csrf(ticket_client),
                               "If-Match": '"1"'}).status_code == 404
    assert ticket_client.get(f"/api/v1/tickets/{manual.json()['id']}").status_code == 200
    manual_path = f"/api/v1/tickets/{manual.json()['id']}"
    assert ticket_client.patch(
        manual_path, headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"ticket_type": "INTERNAL", "reason": "Synthetic attempt"},
    ).status_code == 403
    assert ticket_client.get(manual_path).json()["ticket_type"] == "MANUAL"

    login(ticket_client, engineer.login)
    assert ticket_client.get(path + "/comments").json()["total"] == 1
    assert ticket_client.get(path + "/history").status_code == 200


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
            **MANUAL_CONTACT,
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
            "subject": "Synthetic connectivity issue",
            **CONTACT,
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
            "subject": "Other",
            **CONTACT,
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
    closed_version = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()["version"]
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{ticket_id}",
            headers={**csrf(ticket_client), "If-Match": f'"{closed_version}"'},
            json={"priority": "HIGH"},
        ).status_code
        == 422
    )
    correction = ticket_client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers={**csrf(ticket_client), "If-Match": f'"{closed_version}"'},
        json={"priority": "HIGH", "reason": "Синтетическая поправка приоритета"},
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["status"] == "CLOSED"
    assert correction.json()["closed_at"] == closed.json()["closed_at"]
    assert correction.json()["closed_decision_id"] == closed.json()["closed_decision_id"]
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{ticket_id}",
            headers={**csrf(ticket_client), "If-Match": f'"{correction.json()["version"]}"'},
            json={"ticket_type": "MANUAL", "reason": "Попытка смены типа"},
        ).status_code
        == 409
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
    engineer = seed_user(ticket_engine, "audit_engineer", Role.ENGINEER)
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
    login(ticket_client, engineer.login)
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
        kinds = (TicketType.SUBSCRIBER_LINKED, TicketType.MANUAL)
        if actor.role == Role.ENGINEER:
            kinds += (TicketType.INTERNAL,)
        for kind in kinds:
            payload = {
                "ticket_type": kind.value,
                "description": f"Synthetic {kind.value} request",
                **(
                    {
                        "subject": f"Synthetic {kind.value} request",
                        **(MANUAL_CONTACT if kind == TicketType.MANUAL else CONTACT),
                    }
                    if kind != TicketType.INTERNAL
                    else {}
                ),
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
    assert len(set(created_ids)) == 5
    assert (
        ticket_client.get("/api/v1/tickets", params={"account_no": "000123"}).json()["total"] == 2
    )
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 5
        assert db.scalar(select(func.count()).select_from(TicketEvent)) == 5
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 5
    login(ticket_client, "admin")
    assert ticket_client.get("/api/v1/tickets").status_code == 200
    for kind in (TicketType.SUBSCRIBER_LINKED, TicketType.MANUAL, TicketType.INTERNAL):
        assert ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind.value,
                "description": "Denied",
                **(
                    {
                        "subject": "Denied",
                        **(MANUAL_CONTACT if kind == TicketType.MANUAL else CONTACT),
                    }
                    if kind != TicketType.INTERNAL
                    else {}
                ),
                "client_request_id": str(uuid4()),
            },
        ).status_code == (422 if kind == TicketType.SUBSCRIBER_LINKED else 201)


def test_subject_for_all_types_validation_replay_and_legacy_client(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "subject_engineer", Role.ENGINEER)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, operator.login)
    old_payload = {
        "lookup_id": str(snapshot.id),
        "description": "Legacy synthetic request",
        "subject": "Legacy synthetic request",
        **CONTACT,
        "client_request_id": str(uuid4()),
    }
    old = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=old_payload)
    assert old.status_code == 201, old.text
    assert old.json()["subject"] == "Legacy synthetic request"
    assert (
        ticket_client.post(
            "/api/v1/tickets", headers=csrf(ticket_client), json=old_payload
        ).status_code
        == 200
    )
    for kind in (TicketType.SUBSCRIBER_LINKED, TicketType.MANUAL, TicketType.INTERNAL):
        if kind == TicketType.INTERNAL:
            login(ticket_client, engineer.login)
        payload = {
            "ticket_type": kind.value,
            "subject": f"Synthetic {kind.value} task",
            "description": "Synthetic description",
            **(
                MANUAL_CONTACT
                if kind == TicketType.MANUAL
                else CONTACT
                if kind == TicketType.SUBSCRIBER_LINKED
                else {}
            ),
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
        if kind == TicketType.INTERNAL:
            login(ticket_client, operator.login)
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
    admin = seed_user(ticket_engine, "admin", Role.ADMIN)
    with Session(ticket_engine) as db:
        db.get(User, inactive.id).is_active = False
        db.commit()
    assert ticket_client.get("/api/v1/tickets/events/recent").status_code == 401
    login(ticket_client, operator.login)
    public_engineers = ticket_client.get("/api/v1/tickets/assignable-engineers")
    assert public_engineers.status_code == 200
    assert {item["id"] for item in public_engineers.json()["items"]} == {
        str(engineer.id),
        str(admin.id),
    }
    created = []
    for kind in ("MANUAL", "INTERNAL"):
        if kind == "INTERNAL":
            login(ticket_client, engineer.login)
        response = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind,
                "description": f"Synthetic {kind}",
                **({"subject": f"Synthetic {kind}", **MANUAL_CONTACT} if kind == "MANUAL" else {}),
                "client_request_id": str(uuid4()),
            },
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
        if kind == "INTERNAL":
            login(ticket_client, operator.login)
    unassigned = ticket_client.get(
        "/api/v1/tickets",
        params={"unassigned": "true", "ticket_type": "MANUAL", "page": 1, "size": 1},
    )
    assert unassigned.status_code == 200
    assert unassigned.json()["total"] == 1
    assert len(unassigned.json()["items"]) == 1
    feed = ticket_client.get("/api/v1/tickets/events/recent", params={"size": 1, "page": 1})
    assert feed.status_code == 200
    assert feed.json()["total"] == 1 and len(feed.json()["items"]) == 1
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
    assert engineers.json()["total"] == 2
    assert ticket_client.get("/api/v1/tickets/events/recent").json()["total"] == 2
    assert engineers.json()["items"] == [{"id": str(admin.id), "display_name": admin.display_name}]
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
        if kind == TicketType.INTERNAL:
            login(ticket_client, engineer.login)
        created = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind.value,
                "description": "Synthetic task without billing",
                **(
                    {"subject": "Synthetic task without billing", **MANUAL_CONTACT}
                    if kind == TicketType.MANUAL
                    else {}
                ),
                "client_request_id": str(uuid4()),
            },
        )
        assert created.status_code == 201, created.text
        ticket_id = created.json()["id"]
        if kind == TicketType.INTERNAL:
            login(ticket_client, operator.login)
        assert ticket_client.post(
            f"/api/v1/tickets/{ticket_id}/start",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
        ).status_code == 403
        if kind == TicketType.INTERNAL:
            login(ticket_client, engineer.login)
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


def test_erroneous_payment_create_validation_replay_and_detail(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "payment-operator", Role.OPERATOR)
    login(ticket_client, operator.login)
    payload = {
        "ticket_type": "ERRONEOUS_PAYMENT",
        "caller_full_name": "Синтетический плательщик",
        "correct_account_no": "CORRECT-001",
        "erroneous_account_no": "WRONG-002",
        "payment_occurred_at": "2026-11-03T12:34:00+03:00",
        "payment_amount": "125.50",
        "client_request_id": str(uuid4()),
    }
    first = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["subject"] == "Ошибочный платёж"
    assert body["description"] == ""
    assert body["category_id"] is None
    assert body["account_no"] is None
    assert body["payment_amount"] == "125.50"
    assert body["payment_occurred_at"] == "2026-11-03T09:34:00Z"
    detail = ticket_client.get(f"/api/v1/tickets/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["correct_account_no"] == "CORRECT-001"
    assert detail.json()["erroneous_account_no"] == "WRONG-002"
    repeated = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == body["id"]
    conflict = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={**payload, "payment_amount": "126.00"},
    )
    assert conflict.status_code == 409
    for invalid in (
        {key: value for key, value in payload.items() if key != "caller_full_name"},
        {**payload, "correct_account_no": "WRONG-002"},
        {**payload, "payment_amount": "0"},
        {**payload, "payment_occurred_at": "2026-11-03T12:34:00"},
        {**payload, "category_id": str(uuid4())},
        {**payload, "subject": "Другая тема"},
    ):
        invalid["client_request_id"] = str(uuid4())
        response = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=invalid)
        assert response.status_code == 422, response.text
    with Session(ticket_engine) as db:
        assert (
            db.scalar(select(func.count()).select_from(Ticket).where(Ticket.id == UUID(body["id"])))
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketEvent)
                .where(
                    TicketEvent.ticket_id == UUID(body["id"]),
                    TicketEvent.event_type == "ticket_created",
                )
            )
            == 1
        )
    admin = seed_user(ticket_engine, "payment-editor-admin", Role.ADMIN)
    login(ticket_client, admin.login)
    payment_edit = ticket_client.patch(
        f"/api/v1/tickets/{body['id']}",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"payment_amount": "126.75", "correct_account_no": "CORRECT-003"},
    )
    assert payment_edit.status_code == 200, payment_edit.text
    assert payment_edit.json()["payment_amount"] == "126.75"
    assert payment_edit.json()["correct_account_no"] == "CORRECT-003"
    assert (
        ticket_client.patch(
            f"/api/v1/tickets/{body['id']}",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"ticket_type": "MANUAL", "reason": "Неверный тип"},
        ).status_code
        == 409
    )


def test_ticket_type_validation_replay_and_legacy_client(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "type_validation_engineer", Role.ENGINEER)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, engineer.login)
    for kind in ("MANUAL", "INTERNAL"):
        request_id = str(uuid4())
        payload = {
            "ticket_type": kind,
            "description": "Synthetic request",
            **({"subject": "Synthetic request", **MANUAL_CONTACT} if kind == "MANUAL" else {}),
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
                json={
                    **{
                        key: value
                        for key, value in payload.items()
                        if key not in ("caller_full_name", "caller_phone", "city")
                    },
                    "ticket_type": other_kind,
                    **(
                        {"subject": "Synthetic request", **MANUAL_CONTACT}
                        if other_kind == "MANUAL"
                        else {}
                    ),
                },
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
    login(ticket_client, operator.login)
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
            "subject": "Concurrent synthetic request",
            "description": "Concurrent synthetic request",
            **MANUAL_CONTACT,
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
            **MANUAL_CONTACT,
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
            "ticket_type": "MANUAL",
            "subject": "Other task",
            "description": "Other description",
            **MANUAL_CONTACT,
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


def test_full_ticket_edit_rebind_and_audit(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "full_edit_operator", Role.OPERATOR)
    other_operator = seed_user(ticket_engine, "full_edit_other_operator", Role.OPERATOR)
    engineer = seed_user(ticket_engine, "full_edit_engineer", Role.ENGINEER)
    admin = seed_user(ticket_engine, "full_edit_admin", Role.ADMIN)
    login(ticket_client, operator.login)
    ticket = make_manual_ticket(ticket_client)
    path = f"/api/v1/tickets/{ticket['id']}"
    login(ticket_client, other_operator.login)
    denied = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"priority": "HIGH"},
    )
    assert denied.status_code == 403
    login(ticket_client, operator.login)
    assert (
        ticket_client.patch(
            path,
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"priority": "HIGH"},
        ).status_code
        == 403
    )
    snapshot, _ = seed_snapshot(ticket_engine, engineer, actual_address="Синтетический адрес")
    login(ticket_client, engineer.login)
    edited = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={
            "subject": "Уточнённое обращение",
            "description": "Уточнённая проблема",
            "priority": "HIGH",
            "caller_phone": "+7 900 222 33 44",
            "city": "Тестовый город 2",
            "address_override": "Адрес для выезда",
            "category_id": None,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    assert edited.json()["address_override"] == "Адрес для выезда"
    assert edited.json()["priority"] == "HIGH"
    assert (
        ticket_client.patch(
            path,
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"priority": "LOW"},
        ).status_code
        == 409
    )
    assert (
        ticket_client.patch(
            path,
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"id": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        ticket_client.patch(
            path,
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"ticket_type": "SUBSCRIBER_LINKED", "lookup_id": str(snapshot.id)},
        ).status_code
        == 422
    )  # A source change requires a reason.
    linked = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"2"'},
        json={
            "ticket_type": "SUBSCRIBER_LINKED",
            "lookup_id": str(snapshot.id),
            "caller_full_name": None,
            "reason": "Синтетическая корректировка привязки",
        },
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["account_no"] == snapshot.account_no
    assert linked.json()["subscriber_snapshot_id"] == str(snapshot.id)
    assert linked.json()["ticket_type"] == "SUBSCRIBER_LINKED"
    assert ticket_client.get(path).json()["subscriber"]["actual_address"] == "Синтетический адрес"
    with Session(ticket_engine) as db:
        assert db.get(SubscriberSnapshot, snapshot.id).actual_address == "Синтетический адрес"
        events = db.scalars(
            select(TicketEvent).where(TicketEvent.ticket_id == UUID(ticket["id"]))
        ).all()
        assert len(events) == 3
        assert events[-1].event_data["changes"]["account_no"]["after"] == snapshot.account_no
        assert events[-1].event_data["reason"] == "Синтетическая корректировка привязки"
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(
                    TicketAudit.ticket_id == UUID(ticket["id"]),
                    TicketAudit.action == "ticket_edited",
                )
            )
            == 2
        )
    login(ticket_client, admin.login)
    admin_edit = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"3"'},
        json={"priority": "URGENT"},
    )
    assert admin_edit.status_code == 200, admin_edit.text
    category = ticket_client.post(
        "/api/v1/ticket-categories",
        headers=csrf(ticket_client),
        json={"name": "Синтетическая категория для правки", "sort_order": 90},
    )
    assert category.status_code == 201, category.text
    recategorized = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"4"'},
        json={"category_id": category.json()["id"]},
    )
    assert recategorized.status_code == 200, recategorized.text
    assert recategorized.json()["category_name"] == "Синтетическая категория для правки"
    assert recategorized.json()["subject"] == "Синтетическая категория для правки"
    with pytest.raises(DBAPIError, match="ticket identity fields are immutable"):
        with ticket_engine.begin() as connection:
            connection.execute(
                text("UPDATE tickets SET number = number + 1000 WHERE id = :id"),
                {"id": UUID(ticket["id"])},
            )
    editor_only = seed_user(ticket_engine, "catalog_editor_only", Role.ENGINEER)
    with Session(ticket_engine) as db:
        user = db.get(User, editor_only.id)
        user.permissions_customized = True
        db.add_all(
            [
                UserPermission(user_id=user.id, permission=permission)
                for permission in (Permission.TICKET_READ, Permission.TICKET_EDIT_ALL)
            ]
        )
        db.commit()
    login(ticket_client, editor_only.login)
    assert ticket_client.get("/api/v1/ticket-categories").status_code == 200
    assert (
        ticket_client.post(
            "/api/v1/ticket-categories",
            headers=csrf(ticket_client),
            json={"name": "Недопустимая категория", "sort_order": 1},
        ).status_code
        == 403
    )
    assert ticket_client.post(
        path + "/assignment",
        headers={**csrf(ticket_client), "If-Match": '"5"'},
        json={"engineer_id": str(engineer.id)},
    ).status_code == 403


def test_linked_ticket_can_change_confirmed_account_without_rewriting_history(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "rebind_engineer", Role.ENGINEER)
    first, _ = seed_snapshot(ticket_engine, engineer, account_no="TEST-101")
    second, _ = seed_snapshot(ticket_engine, engineer, account_no="TEST-102")
    login(ticket_client, engineer.login)
    created = create(ticket_client, first)
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    correction = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"lookup_id": str(second.id), "reason": "Проверен другой лицевой счёт"},
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["account_no"] == "TEST-102"
    assert correction.json()["subscriber_snapshot_id"] == str(second.id)
    history = ticket_client.get(path + "/history").json()["items"]
    edited = next(item for item in history if item["event_type"] == "ticket_edited")
    assert edited["event_data"]["changes"]["account_no"] == {
        "before": "TEST-101",
        "after": "TEST-102",
    }
    assert edited["event_data"]["reason"] == "Проверен другой лицевой счёт"
    with Session(ticket_engine) as db:
        assert db.get(SubscriberSnapshot, first.id).account_no == "TEST-101"
        assert db.get(SubscriberSnapshot, second.id).account_no == "TEST-102"


def test_engineer_takeover_preserves_decisions_and_sec02(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    first = seed_user(ticket_engine, "takeover_first", Role.ENGINEER)
    second = seed_user(ticket_engine, "takeover_second", Role.ENGINEER)
    login(ticket_client, first.login)
    ticket = make_manual_ticket(ticket_client)
    path = f"/api/v1/tickets/{ticket['id']}"
    claimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert claimed.status_code == 200, claimed.text
    previous_decision = ticket_client.post(
        path + "/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Синтетическая первичная проверка"},
    )
    assert previous_decision.status_code == 201, previous_decision.text
    old_version = ticket_client.get(path).json()["version"]
    login(ticket_client, second.login)
    takeover = ticket_client.post(
        path + "/assignment",
        headers={**csrf(ticket_client), "If-Match": f'"{old_version}"'},
        json={"engineer_id": str(second.id)},
    )
    assert takeover.status_code == 200, takeover.text
    assert takeover.json()["assigned_engineer_id"] == str(second.id)
    login(ticket_client, first.login)
    assert (
        ticket_client.post(
            path + "/decisions",
            headers=csrf(ticket_client),
            json={"decision_text": "Попытка чужого решения"},
        ).status_code
        == 403
    )
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketResolution)
                .where(TicketResolution.ticket_id == UUID(ticket["id"]))
            )
            == 1
        )
        assigned = db.scalar(
            select(TicketEvent).where(
                TicketEvent.ticket_id == UUID(ticket["id"]),
                TicketEvent.event_type == "engineer_assigned",
            )
        )
        assert assigned.event_data["previous_engineer_id"] == str(first.id)
    login(ticket_client, second.login)
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{takeover.json()["version"]}"'},
        json={"decision_text": "Синтетическое решение нового инженера"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"
    with Session(ticket_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketResolution)
                .where(TicketResolution.ticket_id == UUID(ticket["id"]))
            )
            == 2
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
            "city": "Тестовый город",
            "description": "Нет интернета",
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
        == 201
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
        "caller_full_name": "Тестовый заявитель",
        "city": "Тестовый город",
        "description": "Synthetic category issue",
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


def test_manual_name_optional_description_and_linked_topics(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    operator = seed_user(ticket_engine, "free_topic_operator", Role.OPERATOR)
    snapshot, _ = seed_snapshot(ticket_engine, operator)
    login(ticket_client, operator.login)
    categories = ticket_client.get("/api/v1/ticket-categories").json()
    ready = next(item for item in categories if item["is_other"])
    manual = {"ticket_type": "MANUAL", **MANUAL_CONTACT, "client_request_id": str(uuid4())}
    for missing in ("caller_full_name", "caller_phone", "city"):
        assert (
            ticket_client.post(
                "/api/v1/tickets",
                headers=csrf(ticket_client),
                json={key: value for key, value in manual.items() if key != missing},
            ).status_code
            == 422
        )
    for topic in ({}, {"category_id": ready["id"]}, {"subject": "Своя тема"}):
        payload = {**manual, **topic, "client_request_id": str(uuid4())}
        response = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["subject"] == (
            topic.get("subject") or ready["name"] if topic else "Ручное обращение"
        )
        assert body["description"] == ""
        assert body["caller_full_name"] == MANUAL_CONTACT["caller_full_name"]
        assert body["subscriber_snapshot_id"] is None
        assert (
            ticket_client.get(f"/api/v1/tickets/{body['id']}").json()["caller_full_name"]
            == MANUAL_CONTACT["caller_full_name"]
        )
        assert (
            ticket_client.post(
                "/api/v1/tickets", headers=csrf(ticket_client), json=payload
            ).status_code
            == 200
        )
        with Session(ticket_engine) as db:
            saved = db.get(Ticket, UUID(body["id"]))
            assert (
                saved is not None
                and saved.description == ""
                and saved.caller_full_name == MANUAL_CONTACT["caller_full_name"]
            )
    for topic in ({"category_id": ready["id"]}, {"subject": "Своя тема"}):
        payload = {
            "ticket_type": "SUBSCRIBER_LINKED",
            "lookup_id": str(snapshot.id),
            "client_request_id": str(uuid4()),
            "duplicate_reason": "Отдельная тема",
            **topic,
        }
        response = ticket_client.post("/api/v1/tickets", headers=csrf(ticket_client), json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["subject"] == (topic.get("subject") or ready["name"])
        assert response.json()["caller_phone"] is None and response.json()["city"] is None
        assert (
            ticket_client.post(
                "/api/v1/tickets", headers=csrf(ticket_client), json=payload
            ).status_code
            == 200
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
            assert ticket.caller_full_name is None
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
        caller_full_name="Тестовый заявитель",
        category_id=category_id,
        caller_phone="+7 900 111 22 33",
        city="Тестовый город",
        description="Synthetic issue",
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
        if kind == "INTERNAL":
            login(ticket_client, engineer.login)
        result = ticket_client.post(
            "/api/v1/tickets",
            headers=csrf(ticket_client),
            json={
                "ticket_type": kind,
                "subject": f"Synthetic {kind}",
                "description": "Synthetic request",
                **(MANUAL_CONTACT if kind == "MANUAL" else {}),
                "client_request_id": str(uuid4()),
            },
        )
        assert result.status_code == 201, result.text
        if kind == "INTERNAL":
            login(ticket_client, operator.login)

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
    assert page.json()["total"] == 4
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
        assert filtered.json()["total"] == (0 if kind == "INTERNAL" else 1)
        if kind != "INTERNAL":
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


def test_multi_duty_operator_can_claim_and_resolve_only_with_both_grants(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    worker = seed_user(ticket_engine, "multi_duty_worker", Role.OPERATOR)
    dispatcher = seed_user(ticket_engine, "multi_duty_dispatcher", Role.ENGINEER)
    grant_ticket_permission(ticket_engine, worker, Permission.TICKET_START)
    login(ticket_client, worker.login)
    ticket = make_manual_ticket(ticket_client, subject="Synthetic multi-duty work")
    path = f"/api/v1/tickets/{ticket['id']}"
    headers = {**csrf(ticket_client), "If-Match": '"1"'}
    assert ticket_client.post(path + "/claim", headers=headers).status_code == 403
    candidates = ticket_client.get("/api/v1/tickets/assignable-engineers")
    assert str(worker.id) not in {item["id"] for item in candidates.json()["items"]}

    grant_ticket_permission(ticket_engine, worker, Permission.TICKET_DECISION)
    candidates = ticket_client.get("/api/v1/tickets/assignable-engineers")
    assert str(worker.id) in {item["id"] for item in candidates.json()["items"]}
    claimed = ticket_client.post(path + "/claim", headers=headers)
    assert claimed.status_code == 200, claimed.text
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{claimed.json()["version"]}"'},
        json={"decision_text": "Synthetic issue resolved", "reason": "NOT_ESTABLISHED"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"

    second = make_manual_ticket(ticket_client, subject="Synthetic assigned multi-duty work")
    login(ticket_client, dispatcher.login)
    assigned = ticket_client.post(
        f"/api/v1/tickets/{second['id']}/assignment",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"engineer_id": str(worker.id)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned_engineer_id"] == str(worker.id)


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

    for actor in (denied,):
        login(ticket_client, actor.login)
        assert ticket_client.post(path + "/claim", headers=headers(1)).status_code == 403
        assert ticket_client.post(
            path + "/assignment", headers=headers(1), json={"engineer_id": str(actor.id)}
        ).status_code == 403
    login(ticket_client, first.login)
    assert ticket_client.post(path + "/claim", headers=headers(1)).status_code == 200
    login(ticket_client, second.login)
    takeover = ticket_client.post(
        path + "/assignment", headers=headers(2), json={"engineer_id": str(second.id)}
    )
    assert takeover.status_code == 200
    assert takeover.json()["assigned_engineer_id"] == str(second.id)
    assert ticket_client.post(path + "/start", headers=headers(2)).status_code == 409
    login(ticket_client, admin.login)
    reassigned = ticket_client.post(
        path + "/assignment", headers=headers(takeover.json()["version"]),
        json={"engineer_id": str(first.id)},
    )
    assert reassigned.status_code == 200 and reassigned.json()["assigned_engineer_id"] == str(
        first.id
    )
    reassigned = ticket_client.post(
        path + "/assignment", headers=headers(reassigned.json()["version"]),
        json={"engineer_id": str(second.id)},
    )
    assert reassigned.status_code == 200
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
    unknown_reason = ticket_client.post(
        path + "/decisions",
        headers=csrf(ticket_client),
        json={"decision_text": "Invalid reason", "reason": "GUESSED_FROM_ONU"},
    )
    assert unknown_reason.status_code == 409
    assert unknown_reason.json()["code"] == "reason_unavailable"
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


def test_decision_versions_require_assignee_for_in_progress_and_resolved(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    creator = seed_user(ticket_engine, "decision_creator", Role.OPERATOR)
    owner = seed_user(ticket_engine, "decision_owner", Role.ENGINEER)
    other = seed_user(ticket_engine, "decision_other", Role.ENGINEER)
    admin = seed_user(ticket_engine, "decision_admin", Role.ADMIN)
    login(ticket_client, creator.login)
    ticket = make_manual_ticket(ticket_client, subject="Synthetic decision scope")
    path = f"/api/v1/tickets/{ticket['id']}"
    login(ticket_client, owner.login)
    claimed = ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["assigned_engineer_id"] == str(owner.id)
    version = claimed.json()["version"]

    def counts() -> tuple[int, int, int, int]:
        with Session(ticket_engine) as db:
            ticket_id = UUID(ticket["id"])
            return (
                db.scalar(select(Ticket.version).where(Ticket.id == ticket_id)),
                db.scalar(
                    select(func.count())
                    .select_from(TicketResolution)
                    .where(TicketResolution.ticket_id == ticket_id)
                ),
                db.scalar(
                    select(func.count())
                    .select_from(TicketEvent)
                    .where(TicketEvent.ticket_id == ticket_id)
                ),
                db.scalar(
                    select(func.count())
                    .select_from(TicketAudit)
                    .where(TicketAudit.ticket_id == ticket_id)
                ),
            )

    payload = {"decision_text": "Synthetic repair", "reason": "CABLE_BREAK"}
    login(ticket_client, other.login)
    assert ticket_client.get("/api/v1/auth/me").json()["user"]["id"] == str(other.id)
    before = counts()
    denied = ticket_client.post(path + "/decisions", headers=csrf(ticket_client), json=payload)
    assert denied.status_code == 403
    assert counts() == before

    login(ticket_client, owner.login)
    allowed = ticket_client.post(path + "/decisions", headers=csrf(ticket_client), json=payload)
    assert allowed.status_code == 201, allowed.text
    assert counts()[1] == before[1] + 1
    resolved = ticket_client.post(
        path + "/resolve",
        headers={**csrf(ticket_client), "If-Match": f'"{version + 1}"'},
        json=payload,
    )
    assert resolved.status_code == 200, resolved.text
    login(ticket_client, other.login)
    before = counts()
    denied = ticket_client.post(path + "/decisions", headers=csrf(ticket_client), json=payload)
    assert denied.status_code == 403
    assert counts() == before
    login(ticket_client, admin.login)
    allowed = ticket_client.post(path + "/decisions", headers=csrf(ticket_client), json=payload)
    assert allowed.status_code == 201


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
    assert queue["items"][0]["resolution_preview"] == "First synthetic fix"
    assert queue["items"][0]["resolved_decision_id"] == decision_one
    assert (
        ticket_client.post(
            path + "/close",
            headers={**csrf(ticket_client), "If-Match": f'"{resolved.json()["version"]}"'},
            json={"decision_id": decision_one, "reason": "Bypass"},
        ).json()["code"]
        == "callback_required"
    )
    login(ticket_client, caller.login)
    current = ticket_client.get(path + f"/decisions/{decision_one}")
    assert current.status_code == 200 and current.json()["decision_text"] == "First synthetic fix"
    assert ticket_client.get(path + f"/decisions/{uuid4()}").status_code == 404
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
    second_queue = ticket_client.get("/api/v1/tickets", params={"callback_queue": True}).json()
    assert second_queue["items"][0]["resolution_preview"] == "Second synthetic fix"
    login(ticket_client, caller.login)
    new_decision_id = second.json()["resolved_decision_id"]
    assert (
        ticket_client.get(path + f"/decisions/{new_decision_id}").json()["decision_text"]
        == "Second synthetic fix"
    )
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
    assert confirmed.json()["closed_decision_id"] == second.json()["resolved_decision_id"]
    assert ticket_client.get(path + f"/decisions/{new_decision_id}").status_code == 200
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

    def send_message(
        self, chat_id: str, message: str, *, completion_callback_data: str | None = None
    ) -> DispatchReceipt:
        self.calls.append((chat_id, message))
        assert completion_callback_data and completion_callback_data.startswith("field_done:")
        return DispatchReceipt(chat_id=chat_id, message_id="701")


class RejectingFieldGateway:
    def __init__(self, uncertain: bool = False) -> None:
        self.calls = 0
        self.uncertain = uncertain

    def send_message(
        self,
        _chat_id: str,
        _message: str,
        *,
        completion_callback_data: str | None = None,
    ) -> DispatchReceipt:
        assert completion_callback_data
        self.calls += 1
        if self.uncertain:
            raise DispatchUncertain
        raise DispatchRejected("telegram_http_400")


def test_ticket_address_correction_precedes_netup_for_new_field_work(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "ticket_address_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(
        ticket_engine, engineer, phone="+000000000", actual_address="Старый адрес NetUP"
    )
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    edited = ticket_client.patch(
        path,
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json={"address_override": "Уточнённый адрес заявки"},
    )
    assert edited.status_code == 200, edited.text
    assert (
        ticket_client.post(
            f"{path}/claim", headers={**csrf(ticket_client), "If-Match": '"2"'}
        ).status_code
        == 200
    )
    requested = ticket_client.post(
        f"{path}/field-work-request",
        headers={**csrf(ticket_client), "If-Match": '"3"'},
        json={"reason": "Синтетическая проверка"},
    )
    assert requested.status_code == 200, requested.text
    handoff = ticket_client.get(f"{path}/field-work-handoffs/latest").json()["handoff"]
    assert "Адрес: Уточнённый адрес заявки" in handoff["message_preview"]
    assert (
        ticket_client.patch(
            path,
            headers={**csrf(ticket_client), "If-Match": f'"{requested.json()["version"]}"'},
            json={"lookup_id": str(snapshot.id), "reason": "Повторное сопоставление"},
        ).status_code
        == 409
    )
    with Session(ticket_engine) as db:
        assert db.get(SubscriberSnapshot, snapshot.id).actual_address == "Старый адрес NetUP"


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
    assert "Телефон звонящего: +7 900 111 22 33" in handoff["message_preview"]
    assert "Адрес: г. Тест, ул. Безопасная, д. 20" in handoff["message_preview"]
    assert "Город: Тестовый город" in handoff["message_preview"]
    assert handoff["visit_address"] is None
    assert "Отправил инженер: Field_Dispatch_Engineer" in handoff["message_preview"]
    assert handoff["generated_preview"] == handoff["message_preview"]

    with Session(ticket_engine) as db:
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.chat_id = "-2222222"
        binding.chat_title = "Moved Synthetic Group"
        binding.chat_type = "supergroup"
        db.commit()

    send_path = f"{path}/field-work-handoffs/{handoff['id']}/send"
    approved_text = handoff["message_preview"] + "\nУточнение инженера: проверить ввод в дом."
    for invalid_text in ("   ", "Недопустимый\x00символ", "x" * 4097):
        rejected = ticket_client.post(
            send_path,
            headers=csrf(ticket_client),
            json={"client_request_id": str(uuid4()), "message_text": invalid_text},
        )
        assert rejected.status_code == 422, rejected.text
    assert gateway.calls == []
    assert (
        ticket_client.post(
            send_path,
            headers=csrf(ticket_client),
            json={"client_request_id": str(uuid4()), "visit_address": "Чужой адрес"},
        ).status_code
        == 422
    )
    request_id = str(uuid4())
    assert ticket_client.post(send_path, json={"client_request_id": request_id}).status_code == 403
    sent = ticket_client.post(
        send_path, headers=csrf(ticket_client),
        json={
            "client_request_id": request_id, "message_text": approved_text, "direction": "SUKHUM"
        },
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["delivery_status"] == "SENT"
    assert sent.json()["telegram_message_id"] == 701
    assert sent.json()["sent_by"] == str(engineer.id)
    assert sent.json()["message_preview"] == approved_text
    assert sent.json()["generated_preview"] == handoff["generated_preview"]
    assert len(gateway.calls) == 1
    assert gateway.calls[0][0] == "-2222222"
    assert gateway.calls[0][1] == approved_text
    with Session(ticket_engine) as db:
        saved_handoff = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert saved_handoff is not None and saved_handoff.chat_id == "-2222222"
        assert saved_handoff.message_text == approved_text
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.chat_id = "-3333333"
        binding.chat_title = "Next Synthetic Group"
        binding.chat_type = "supergroup"
        db.commit()
    for replay_id in (request_id, str(uuid4())):
        replay = ticket_client.post(
            send_path, headers=csrf(ticket_client),
            json={
                "client_request_id": replay_id, "message_text": approved_text, "direction": "SUKHUM"
            },
        )
        assert replay.status_code == 200, replay.text
    changed_after_send = ticket_client.post(
        send_path, headers=csrf(ticket_client),
        json={
            "client_request_id": str(uuid4()), "message_text": "Новый текст", "direction": "SUKHUM"
        },
    )
    assert changed_after_send.status_code == 409
    assert changed_after_send.json()["code"] == "field_send_locked"
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
        second_path, headers=csrf(ticket_client),
        json={
            "client_request_id": str(uuid4()), "message_text": "Первая подготовка",
            "direction": "SUKHUM",
        },
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["delivery_status"] == "FAILED"
    assert failed.json()["last_error_code"] == "telegram_http_400"

    uncertain_gateway = RejectingFieldGateway(uncertain=True)
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: uncertain_gateway
    uncertain = ticket_client.post(
        second_path, headers=csrf(ticket_client),
        json={
            "client_request_id": str(uuid4()), "message_text": "Исправление после отказа",
            "direction": "SUKHUM",
        },
    )
    assert uncertain.status_code == 200, uncertain.text
    assert uncertain.json()["delivery_status"] == "UNCERTAIN"
    assert uncertain.json()["message_preview"] == "Исправление после отказа"
    blocked = ticket_client.post(
        second_path, headers=csrf(ticket_client),
        json={
            "client_request_id": str(uuid4()), "message_text": "Ещё одно исправление",
            "direction": "SUKHUM",
        },
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


def test_ochamchira_dispatch_and_callback_are_bound_to_saved_chat(
    ticket_engine: Engine, ticket_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_OCHAMCHIRA_CHAT_ID", "-1004473201977")
    engineer = seed_user(ticket_engine, "ochamchira_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(ticket_engine, engineer, phone="+000000000")
    gateway = RecordingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: gateway
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    assert ticket_client.post(
        path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
    ).status_code == 200
    with Session(ticket_engine) as db:
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.status = "UNBOUND"
        binding.chat_id = binding.chat_title = binding.chat_type = None
        db.commit()
    assert ticket_client.post(
        path + "/field-work-request",
        headers={**csrf(ticket_client), "If-Match": '"2"'},
        json={"reason": "Synthetic field visit"},
    ).status_code == 200
    handoff = ticket_client.get(path + "/field-work-handoffs/latest").json()["handoff"]
    send_path = path + f"/field-work-handoffs/{handoff['id']}/send"
    missing = ticket_client.post(
        send_path, headers=csrf(ticket_client), json={"client_request_id": str(uuid4())}
    )
    assert missing.status_code == 422 and missing.json()["code"] == "field_direction_required"
    invalid = ticket_client.post(
        send_path, headers=csrf(ticket_client),
        json={"client_request_id": str(uuid4()), "direction": "OCHAMCHIRA", "chat_id": "-1"},
    )
    assert invalid.status_code == 422
    approved = "Заявка для Очамчиры\nУточнение инженера"
    request_id = str(uuid4())
    payload = {"client_request_id": request_id, "direction": "OCHAMCHIRA", "message_text": approved}
    sent = ticket_client.post(send_path, headers=csrf(ticket_client), json=payload)
    assert sent.status_code == 200, sent.text
    assert sent.json()["direction"] == "OCHAMCHIRA"
    assert sent.json()["group_name"] == "OSS Ochamchira"
    assert sent.json()["chat_id"] == "-1004473201977"
    assert gateway.calls == [("-1004473201977", approved)]
    replay = ticket_client.post(send_path, headers=csrf(ticket_client), json=payload)
    assert replay.status_code == 200 and len(gateway.calls) == 1
    changed = ticket_client.post(
        send_path, headers=csrf(ticket_client),
        json={"client_request_id": str(uuid4()), "direction": "SUKHUM", "message_text": approved},
    )
    assert changed.status_code == 409
    with Session(ticket_engine) as db:
        saved = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert saved is not None and saved.direction == "OCHAMCHIRA"
        assert saved.chat_id == "-1004473201977" and saved.message_text == approved

    def callback(update_id: int, chat_id: int, message_id: int) -> dict:
        return {"update_id": update_id, "callback_query": {
            "id": f"callback-{update_id}",
            "from": {"id": 777, "is_bot": False, "first_name": "Synthetic Installer"},
            "data": f"field_done:{handoff['id']}",
            "message": {"message_id": message_id, "date": 1_800_000_000,
                        "chat": {"id": chat_id, "type": "supergroup"}},
        }}

    with Session(ticket_engine) as db:
        assert process_update(db, callback(99901, -5319791765, 701)).outcome == "DENIED"
        assert process_update(db, callback(99902, -1004473201977, 702)).outcome == "DENIED"
        accepted = process_update(db, callback(99903, -1004473201977, 701))
        assert accepted.outcome == "PROCESSED"
        assert process_update(db, callback(99903, -1004473201977, 701)).duplicate
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).work_status == "AWAITING_REPORT"
        assert accepted.prompt_id is not None

        class PromptGateway:
            def send_completion_prompt(self, chat_id: str, message_id: int) -> DispatchReceipt:
                assert chat_id == "-1004473201977" and message_id == 701
                return DispatchReceipt(chat_id, "704")

        _send_prompt(db, PromptGateway(), accepted.prompt_id)
        report = {
            "update_id": 99904,
            "message": {
                "message_id": 705, "date": 1_800_000_010,
                "from": {"id": 888, "is_bot": False, "first_name": "Другой участник"},
                "chat": {"id": -1004473201977, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 704, "date": 1_800_000_005,
                    "from": {"id": 9999, "is_bot": True},
                    "chat": {"id": -1004473201977, "type": "supergroup"},
                    "text": "Что было сделано?",
                },
                "text": "Связь восстановлена.",
            },
        }
        assert process_update(db, report).outcome == "PROCESSED"
        assert process_update(db, report).duplicate
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).work_status == "REPORTED"
        assert db.scalar(select(func.count()).select_from(FieldReportNotification)) == 1


@pytest.mark.parametrize(
    "direction,chat_id",
    [
        ("SUKHUM", -5319791765),
        ("OCHAMCHIRA", -1004473201977),
    ],
)
def test_field_work_rescheduling_telegram_and_oss(
    ticket_engine: Engine,
    ticket_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    direction: str,
    chat_id: int,
) -> None:
    monkeypatch.setenv("TELEGRAM_OCHAMCHIRA_CHAT_ID", "-1004473201977")
    engineer = seed_user(ticket_engine, f"schedule_{direction.lower()}", Role.ENGINEER)
    snapshot, connections = seed_snapshot(ticket_engine, engineer, phone="+000000000")
    sender = RecordingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: sender
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    assert (
        ticket_client.post(
            path + "/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        ticket_client.post(
            path + "/field-work-request",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"reason": "Synthetic visit"},
        ).status_code
        == 200
    )
    handoff = ticket_client.get(path + "/field-work-handoffs/latest").json()["handoff"]
    assert handoff["schedule_status"] == "ORIGINAL" and handoff["schedule_version"] == 0
    sent = ticket_client.post(
        path + f"/field-work-handoffs/{handoff['id']}/send",
        headers=csrf(ticket_client),
        json={"client_request_id": str(uuid4()), "direction": direction},
    )
    assert sent.status_code == 200 and sender.calls[0][0] == str(chat_id)
    history_path = path + f"/field-work-handoffs/{handoff['id']}/reschedules"

    def callback(
        update_id: int,
        data: str,
        *,
        author: int = 777,
        source_chat: int | None = None,
        source_message: int = 701,
    ) -> dict:
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"schedule-callback-{update_id}",
                "from": {"id": author, "is_bot": False, "first_name": "Synthetic Installer"},
                "data": data,
                "message": {
                    "message_id": source_message,
                    "date": 1_800_000_000,
                    "chat": {
                        "id": chat_id if source_chat is None else source_chat,
                        "type": "supergroup",
                    },
                },
            },
        }

    def answer(update_id: int, body: str, *, author: int = 777) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_800_000_010,
                "from": {"id": author, "is_bot": False, "first_name": "Synthetic Installer"},
                "chat": {"id": chat_id, "type": "supergroup"},
                "reply_to_message": {"message_id": 701},
                "text": body,
            },
        }

    class CardGateway:
        edits: list[tuple[str, int, str, dict]]
        fail_once: bool

        def __init__(self) -> None:
            self.edits = []
            self.fail_once = False

        def edit_reschedule_card(
            self, destination: str, message_id: int, body: str, markup: dict
        ) -> None:
            assert destination == str(chat_id) and message_id == 701
            self.edits.append((destination, message_id, body, markup))
            if self.fail_once:
                self.fail_once = False
                raise DispatchRejected("telegram_http_500")

        def send_message(self, *_args, **_kwargs) -> None:
            pytest.fail("Inline rescheduling must not send a separate message")

    gateway = CardGateway()
    start_data = f"field_reschedule:{handoff['id']}"
    with Session(ticket_engine) as db:
        assert (
            process_update(db, callback(98001, start_data, source_chat=-999999)).outcome == "DENIED"
        )
        assert (
            process_update(db, callback(98002, start_data, source_message=999)).outcome == "DENIED"
        )
        first = process_update(db, callback(98003, start_data))
        assert first.outcome == "PROCESSED" and first.dialog_id is not None
        assert process_update(db, callback(98003, start_data)).duplicate
        _sync_dialog(db, gateway, first.dialog_id)
        assert "Причина переноса" in gateway.edits[-1][2]
        assert gateway.edits[-1][2].startswith(sender.calls[0][1])
        def step(dialog: UUID, code: str) -> str:
            return f"rs:{dialog}:{code}"
        assert (
            process_update(db, callback(98004, step(first.dialog_id, "r1"), author=888)).outcome
            == "DENIED"
        )
        assert (
            process_update(db, callback(98005, f"field_done:{handoff['id']}")).outcome == "DENIED"
        )
        choice = process_update(db, callback(98006, step(first.dialog_id, "r1")))
        assert choice.outcome == "PROCESSED"
        _sync_dialog(db, gateway, first.dialog_id)
        assert "Новая дата" in gateway.edits[-1][2]
        assert (
            process_update(db, callback(98007, step(first.dialog_id, "dtn"))).outcome == "PROCESSED"
        )
        _sync_dialog(db, gateway, first.dialog_id)
        saved = process_update(db, callback(98008, step(first.dialog_id, "ok")))
        assert saved.outcome == "PROCESSED" and saved.dialog_id == first.dialog_id
        assert process_update(db, callback(98008, step(first.dialog_id, "ok"))).duplicate
        _sync_schedule_card(db, gateway, UUID(handoff["id"]))
        current = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert current.schedule_status == "DATE_PENDING" and current.scheduled_for is None
        assert current.schedule_card_status == "DONE"
        assert "📅 ПЕРЕНЕСЕНА" in gateway.edits[-1][2]
        assert "дата не согласована" in gateway.edits[-1][2]
        assert db.scalar(select(func.count()).select_from(FieldWorkReschedule)) == 1

        second = process_update(db, callback(98009, start_data))
        assert second.dialog_id is not None and second.dialog_id != first.dialog_id
        process_update(db, callback(98010, step(second.dialog_id, "r0")))
        assert (
            process_update(db, callback(98011, step(second.dialog_id, "back"))).outcome
            == "PROCESSED"
        )
        assert db.get(FieldRescheduleDialog, second.dialog_id).stage == "REASON"
        process_update(db, callback(98012, step(second.dialog_id, "r0")))
        process_update(db, callback(98013, step(second.dialog_id, "dtx")))
        invalid = process_update(db, answer(98014, "31.02.2027"))
        assert invalid.kind == "RESCHEDULE_INVALID" and invalid.dialog_id == second.dialog_id
        _sync_dialog(db, gateway, second.dialog_id)
        assert "Укажите дату" in gateway.edits[-1][2]
        agreed_day = (datetime.now(ABKHAZIA) + timedelta(days=5)).date()
        valid = process_update(db, answer(98015, agreed_day.strftime("%d.%m.%Y")))
        assert valid.outcome == "PROCESSED" and valid.dialog_id == second.dialog_id
        assert (
            process_update(db, callback(98016, step(second.dialog_id, "tn"))).outcome == "PROCESSED"
        )
        assert (
            process_update(db, callback(98017, step(second.dialog_id, "ok"))).outcome == "PROCESSED"
        )
        gateway.fail_once = True
        _sync_schedule_card(db, gateway, UUID(handoff["id"]))
        current = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert current.schedule_status == "SCHEDULED" and current.scheduled_for is None
        assert current.scheduled_date == agreed_day and current.schedule_card_status == "FAILED"
        current.schedule_card_attempted_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
        _sync_schedule_card(db, gateway, UUID(handoff["id"]))
        assert current.schedule_card_status == "DONE"
        assert "время не согласовано" in gateway.edits[-1][2]
        assert db.scalar(select(func.count()).select_from(FieldWorkReschedule)) == 2

        third = process_update(db, callback(98018, start_data))
        process_update(db, callback(98019, step(third.dialog_id, "r2")))
        process_update(db, callback(98020, step(third.dialog_id, "cal")))
        _sync_dialog(db, gateway, third.dialog_id)
        assert "Выберите дату" in gateway.edits[-1][2]
        tomorrow = (datetime.now(ABKHAZIA) + timedelta(days=1)).date()
        month = db.get(FieldRescheduleDialog, third.dialog_id).calendar_month
        if tomorrow.month != month.month or tomorrow.year != month.year:
            process_update(db, callback(98021, step(third.dialog_id, "mn")))
        date_code = f"d{tomorrow:%Y%m%d}"
        process_update(db, callback(98022, step(third.dialog_id, date_code)))
        process_update(db, callback(98023, step(third.dialog_id, "tx")))
        assert process_update(db, answer(98024, "25:99")).kind == "RESCHEDULE_INVALID"
        assert process_update(db, answer(98025, "14:30")).outcome == "PROCESSED"
        assert (
            process_update(db, callback(98026, step(third.dialog_id, "ok"))).outcome == "PROCESSED"
        )
        _sync_schedule_card(db, gateway, UUID(handoff["id"]))
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).scheduled_for is not None
        assert "14:30" in gateway.edits[-1][2]
        assert db.scalar(select(func.count()).select_from(FieldWorkReschedule)) == 3

        fourth = process_update(db, callback(98027, start_data))
        assert (
            process_update(db, callback(98028, step(fourth.dialog_id, "cancel"))).outcome
            == "PROCESSED"
        )
        _sync_dialog(db, gateway, fourth.dialog_id)
        assert gateway.edits[-1][3]["inline_keyboard"][0][1]["text"] == "📅 Перенести"
        assert db.scalar(select(func.count()).select_from(FieldWorkReschedule)) == 3

    page = ticket_client.get(history_path)
    assert page.status_code == 200 and page.json()["total"] == 3
    assert page.json()["items"][1]["scheduled_date"] == agreed_day.isoformat()
    changed_date = (datetime.now(UTC) + timedelta(days=7)).astimezone(ABKHAZIA)
    payload = {
        "reason": "Новая договорённость",
        "scheduled_for": changed_date.isoformat(),
        "expected_version": 3,
    }
    updated = ticket_client.post(history_path, headers=csrf(ticket_client), json=payload)
    assert updated.status_code == 200 and updated.json()["schedule_version"] == 4
    assert (
        ticket_client.post(history_path, headers=csrf(ticket_client), json=payload).status_code
        == 409
    )
    with Session(ticket_engine) as db:
        _sync_schedule_card(db, gateway, UUID(handoff["id"]))
        assert "Новая договорённость" in gateway.edits[-1][2]
        assert db.scalar(select(func.count()).select_from(FieldWorkReschedule)) == 4
        completed = process_update(db, callback(98029, f"field_done:{handoff['id']}"))
        assert completed.outcome == "PROCESSED" and completed.prompt_id is not None
        assert process_update(db, callback(98030, start_data)).outcome == "DENIED"

def test_reschedule_date_validation() -> None:
    for value in (
        "Причина: перенос\nДата: 31.02.2027 12:00",
        "Причина: перенос\nДата: 01.01.2020 12:00",
        "Причина: перенос\nДата: завтра",
        "Дата: не согласована",
    ):
        with pytest.raises(ValueError):
            parse_telegram_reschedule(value)
    assert parse_telegram_reschedule(
        "Причина: абонент попросил\nДата: не согласована"
    ) == ("абонент попросил", None)


def test_field_work_text_is_locked_during_concurrent_send(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "field_text_lock_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(ticket_engine, engineer, phone="+000000000")
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
        json={"reason": "Проверить подключение"},
    )
    assert requested.status_code == 200, requested.text
    latest = ticket_client.get(f"{path}/field-work-handoffs/latest")
    handoff_id = UUID(latest.json()["handoff"]["id"])
    ticket_id = UUID(created.json()["id"])
    with Session(ticket_engine) as db:
        prepared = prepare_send(
            db, engineer.id, ticket_id, handoff_id, uuid4(),
            message_text="Точный текст инженера", direction="SUKHUM",
        )
    with Session(ticket_engine) as db, pytest.raises(ApiError) as blocked:
        prepare_send(db, engineer.id, ticket_id, handoff_id, uuid4(), message_text="Чужой текст")
    assert blocked.value.code == "field_send_in_progress"
    with Session(ticket_engine) as db:
        saved = db.get(FieldWorkHandoff, handoff_id)
        assert saved is not None and saved.message_text == "Точный текст инженера"
        assert saved.delivery_status == "SENDING"
    with Session(ticket_engine) as db:
        finalized = finalize_send(
            db, prepared, receipt=DispatchReceipt(chat_id=prepared.chat_id, message_id="701")
        )
    assert finalized.message_text == "Точный текст инженера"


@pytest.mark.parametrize(
    ("visit_address", "expected_address"),
    [
        ("г. Тест, ул. Ручная, д. 7", "г. Тест, ул. Ручная, д. 7"),
        (None, "не указан"),
    ],
)
def test_field_work_allows_optional_visit_address_without_changing_subscriber_snapshot(
    ticket_engine: Engine,
    ticket_client: TestClient,
    visit_address: str | None,
    expected_address: str,
) -> None:
    engineer = seed_user(ticket_engine, "field_optional_address_engineer", Role.ENGINEER)
    snapshot, connections = seed_snapshot(
        ticket_engine, engineer, phone="+000000000", house_number="7"
    )
    gateway = RecordingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: gateway
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    assert created.status_code == 201, created.text
    path = f"/api/v1/tickets/{created.json()['id']}"
    assert (
        ticket_client.post(
            f"{path}/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        ticket_client.post(
            f"{path}/field-work-request",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"reason": "Synthetic field visit"},
        ).status_code
        == 200
    )
    handoff = ticket_client.get(f"{path}/field-work-handoffs/latest").json()["handoff"]
    assert handoff["ready_to_send"] is True
    assert handoff["missing_fields"] == []
    assert "Адрес: не указан" in handoff["message_preview"]
    send_path = f"{path}/field-work-handoffs/{handoff['id']}/send"
    payload = {
        "client_request_id": str(uuid4()), "visit_address": visit_address, "direction": "SUKHUM"
    }
    sent = ticket_client.post(send_path, headers=csrf(ticket_client), json=payload)
    assert sent.status_code == 200, sent.text
    assert sent.json()["delivery_status"] == "SENT"
    assert sent.json()["visit_address"] == visit_address
    assert f"Адрес: {expected_address}" in sent.json()["message_preview"]
    assert len(gateway.calls) == 1
    assert f"Адрес: {expected_address}" in gateway.calls[0][1]
    repeated = ticket_client.post(send_path, headers=csrf(ticket_client), json=payload)
    assert repeated.status_code == 200
    assert len(gateway.calls) == 1
    with Session(ticket_engine) as db:
        unchanged = db.get(SubscriberSnapshot, snapshot.id)
        saved = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert unchanged is not None and unchanged.actual_address is None
        assert saved is not None and saved.visit_address == visit_address


def test_optional_visit_address_does_not_relax_field_dispatch_contact(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "field_missing_contact_engineer", Role.ENGINEER)
    snapshot, _ = seed_snapshot(ticket_engine, engineer)
    login(ticket_client, engineer.login)
    created = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "lookup_id": str(snapshot.id),
            "subject": "Synthetic field visit",
            "description": "Synthetic field visit",
            "city": "Тестовый город",
            "client_request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    path = f"/api/v1/tickets/{ticket_id}"
    assert (
        ticket_client.post(
            f"{path}/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        ticket_client.post(
            f"{path}/field-work-request",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"reason": "Synthetic field visit"},
        ).status_code
        == 200
    )
    handoff = ticket_client.get(f"{path}/field-work-handoffs/latest").json()["handoff"]
    assert handoff["ready_to_send"] is False
    assert "contact" in handoff["missing_fields"]


def test_telegram_replies_completion_and_engineer_verification_are_independent(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "telegram_t2_engineer", Role.ENGINEER)
    second_engineer = seed_user(ticket_engine, "telegram_t2_second_engineer", Role.ENGINEER)
    own_only_engineer = seed_user(ticket_engine, "telegram_t2_own_only", Role.ENGINEER)
    operator = seed_user(ticket_engine, "telegram_t2_operator", Role.OPERATOR)
    with Session(ticket_engine) as db:
        own_only = db.get(User, own_only_engineer.id)
        assert own_only is not None
        own_only.permissions_customized = True
        db.add(UserPermission(user_id=own_only.id, permission=Permission.TICKET_READ_OWN))
        db.add(UserPermission(user_id=own_only.id, permission=Permission.TICKET_FIELD_WORK))
        db.commit()
    snapshot, connections = seed_snapshot(
        ticket_engine,
        engineer,
        phone="+000000001",
        actual_address="г. Тест, ул. Монтажная",
        house_number="7",
    )
    gateway = RecordingFieldGateway()
    ticket_client.app.dependency_overrides[get_field_dispatch_gateway] = lambda: gateway
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot, connections[0])
    ticket_id = created.json()["id"]
    path = f"/api/v1/tickets/{ticket_id}"
    assert (
        ticket_client.post(
            f"{path}/claim", headers={**csrf(ticket_client), "If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        ticket_client.post(
            f"{path}/field-work-request",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"reason": "Synthetic field visit"},
        ).status_code
        == 200
    )
    handoff = ticket_client.get(f"{path}/field-work-handoffs/latest").json()["handoff"]
    assert (
        ticket_client.post(
            f"{path}/field-work-handoffs/{handoff['id']}/send",
            headers=csrf(ticket_client),
            json={"client_request_id": str(uuid4()), "direction": "SUKHUM"},
        ).status_code
        == 200
    )

    callback = {
        "update_id": 9001,
        "callback_query": {
            "id": "synthetic-callback-1",
            "from": {
                "id": 777,
                "is_bot": False,
                "first_name": "Новый участник",
                "last_name": "Группы",
            },
            "data": f"field_done:{handoff['id']}",
            "message": {
                "message_id": 701,
                "date": 1_800_000_000,
                "chat": {"id": -5319791765, "type": "group"},
            },
        },
    }
    reply = {
        "update_id": 9002,
        "message": {
            "message_id": 702,
            "date": 1_800_000_010,
            "from": {
                "id": 777,
                "is_bot": False,
                "first_name": "Новый участник",
                "last_name": "Группы",
            },
            "chat": {"id": -5319791765, "type": "group"},
            "reply_to_message": {
                "message_id": 704,
                "date": 1_800_000_009,
                "from": {"id": 9999, "is_bot": True},
                "chat": {"id": -5319791765, "type": "group"},
                "text": "Что было сделано?",
                "reply_to_message": {
                    "message_id": 701,
                    "chat": {"id": -5319791765, "type": "group"},
                },
            },
            "text": "Кабель заменён, связь проверена.",
        },
    }
    denied = {
        "update_id": 9003,
        "message": {
            "message_id": 703,
            "date": 1_800_000_020,
            "from": {"id": 999, "is_bot": False, "first_name": "Посторонний"},
            "chat": {"id": -5319791765, "type": "group"},
            "reply_to_message": {"message_id": 999999},
            "text": "Must not be imported",
        },
    }
    ordinary_reply = {
        "update_id": 9006,
        "message": {
            "message_id": 706,
            "date": 1_800_000_005,
            "from": {"id": 501, "is_bot": False, "first_name": "Монтажник", "last_name": "Тест"},
            "chat": {"id": -5319791765, "type": "group"},
            "reply_to_message": {"message_id": 701},
            "text": "Приступили к работе.",
        },
    }
    wrong_installer = {
        "update_id": 9005,
        "message": {
            "message_id": 705,
            "date": 1_800_000_015,
            "from": {"id": 502, "is_bot": False, "first_name": "Другой монтажник"},
            "chat": {"id": -5319791765, "type": "group"},
            "reply_to_message": {
                "message_id": 704,
                "date": 1_800_000_009,
                "from": {"id": 9999, "is_bot": True},
                "chat": {"id": -5319791765, "type": "group"},
                "text": "Что было сделано?",
                "reply_to_message": {
                    "message_id": 701,
                    "chat": {"id": -5319791765, "type": "group"},
                },
            },
            "text": "Работы выполнены участником без заранее сохранённого Telegram ID.",
        },
    }
    with Session(ticket_engine) as db:
        first = process_update(db, callback)
        assert first.outcome == "PROCESSED"
        saved = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert saved is not None and saved.work_status == "AWAITING_REPORT"
        prompt = db.get(FieldWorkCompletionPrompt, first.prompt_id)
        assert prompt is not None and prompt.status == "REQUESTING"

        class PromptGateway:
            calls = 0

            def send_completion_prompt(self, chat_id: str, message_id: int) -> DispatchReceipt:
                self.calls += 1
                assert chat_id == "-5319791765" and message_id == 701
                with Session(ticket_engine) as separate_db:
                    persisted = separate_db.get(FieldWorkCompletionPrompt, first.prompt_id)
                    assert persisted is not None and persisted.status == "UNCERTAIN"
                return DispatchReceipt(chat_id, "704")

        prompt_gateway = PromptGateway()
        _send_prompt(db, prompt_gateway, first.prompt_id)
        _send_prompt(db, prompt_gateway, first.prompt_id)
        assert prompt_gateway.calls == 1
        prompt = db.get(FieldWorkCompletionPrompt, first.prompt_id)
        assert prompt is not None and prompt.status == "WAITING"
        assert prompt.error_code is None and prompt.telegram_message_id == 704
        # The API accepted sendMessage, but its response was lost before the
        # returned prompt message_id committed. A participant reply must
        # recover the matching prompt from Telegram's quoted handoff message.
        prompt.status = "UNCERTAIN"
        prompt.error_code = "telegram_transport_uncertain"
        prompt.telegram_message_id = None
        prompt.sent_at = None
        db.commit()
        assert (
            db.scalar(
                select(func.count())
                .select_from(FieldWorkCompletionPrompt)
                .join(FieldWorkHandoff, FieldWorkHandoff.id == FieldWorkCompletionPrompt.handoff_id)
                .where(
                    FieldWorkCompletionPrompt.chat_id == "-5319791765",
                    FieldWorkCompletionPrompt.status == "UNCERTAIN",
                    FieldWorkHandoff.delivery_status == "SENT",
                    FieldWorkHandoff.work_status == "AWAITING_REPORT",
                    FieldWorkHandoff.telegram_message_id == 701,
                )
            )
            == 1
        )
        assert process_update(db, ordinary_reply).outcome == "IGNORED"
        assert (
            process_update(
                db,
                {**ordinary_reply, "update_id": 9007},
            ).outcome
            == "IGNORED"
        )
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).work_status == "AWAITING_REPORT"
        assert process_update(db, callback).outcome == "DUPLICATE"
        repeated = {
            **callback,
            "update_id": 9004,
            "callback_query": {
                **callback["callback_query"],
                "id": "synthetic-callback-2",
            },
        }
        assert process_update(db, repeated).outcome == "DUPLICATE"
        assert db.scalar(select(func.count()).select_from(FieldWorkCompletionPrompt)) == 1
        report_result = process_update(db, wrong_installer)
        assert report_result.outcome == "PROCESSED", db.scalar(
            select(FieldDispatchUpdate.error_code).where(FieldDispatchUpdate.update_id == 9005)
        )
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).work_status == "REPORTED"
        assert process_update(db, reply).outcome == "DUPLICATE"
        assert (
            process_update(
                db,
                {**reply, "update_id": 9008},
            ).outcome
            == "DUPLICATE"
        )
        assert process_update(db, denied).outcome == "IGNORED"
        saved = db.get(FieldWorkHandoff, UUID(handoff["id"]))
        assert saved is not None and saved.work_status == "REPORTED"
        assert db.scalar(select(func.count()).select_from(FieldExternalMessage)) == 1
        assert db.scalar(select(func.count()).select_from(FieldReportNotification)) == 1
        saved_report = db.scalar(select(FieldExternalMessage))
        assert saved_report is not None and saved_report.sender_telegram_id == 502
        saved_prompt = db.get(FieldWorkCompletionPrompt, first.prompt_id)
        assert saved_prompt is not None and saved_prompt.status == "COMPLETED"
        assert saved_prompt.telegram_message_id == 704
        assert saved_prompt.deletion_status == "PENDING"
        saved_update = db.scalar(
            select(FieldDispatchUpdate).where(FieldDispatchUpdate.update_id == 9005)
        )
        assert saved_update is not None
        assert saved_update.report_delete_status == "PENDING"
        assert saved_update.card_edit_status == "PENDING"

        class ConfirmationGateway:
            deleted_ids: list[int] = []
            edits: list[str] = []

            def delete_message(self, chat_id: str, message_id: int) -> None:
                assert chat_id == "-5319791765" and message_id in {704, 705}
                self.deleted_ids.append(message_id)
                if self.deleted_ids.count(message_id) == 1:
                    raise DispatchUncertain
                if message_id == 705:
                    raise DispatchRejected("telegram_message_not_found")

            def edit_handoff_card(self, chat_id: str, message_id: int, text: str) -> None:
                assert chat_id == "-5319791765" and message_id == 701
                assert text.startswith(f"Заявка #{db.get(Ticket, UUID(ticket_id)).number}")
                assert text.endswith("✅ Выполнено\nМонтажник: Другой монтажник")
                self.edits.append(text)
                if len(self.edits) == 1:
                    raise DispatchUncertain
                raise DispatchRejected("telegram_message_not_modified")

        confirmation_gateway = ConfirmationGateway()
        _sync_completion(db, confirmation_gateway, 9005)
        assert saved_prompt.deletion_status == "FAILED"
        assert saved_prompt.deletion_attempts == 1
        assert saved_update.report_delete_status == "FAILED"
        assert saved_update.card_edit_status == "FAILED"
        assert db.get(FieldWorkHandoff, UUID(handoff["id"])).work_status == "REPORTED"
        saved_prompt.deletion_attempted_at = datetime.now(UTC) - timedelta(minutes=2)
        saved_update.report_delete_attempted_at = datetime.now(UTC) - timedelta(minutes=2)
        saved_update.card_edit_attempted_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()
        _sync_completion(db, confirmation_gateway, 9005)
        assert saved_prompt.deletion_status == "DELETED"
        assert saved_prompt.deletion_attempts == 2
        assert saved_update.report_delete_status == "DONE"
        assert saved_update.card_edit_status == "DONE"
        _sync_completion(db, confirmation_gateway, 9005)
        assert saved_update.confirmation_status == "NOT_REQUIRED"
        assert confirmation_gateway.deleted_ids == [704, 705, 704, 705]
        assert len(confirmation_gateway.edits) == 2
        assert db.scalar(select(func.count()).select_from(FieldDispatchUpdate)) == 8
        ticket = db.get(Ticket, UUID(ticket_id))
        assert ticket is not None and ticket.status == TicketStatus.IN_PROGRESS

    messages_path = f"{path}/field-work-handoffs/{handoff['id']}/messages"
    messages = ticket_client.get(messages_path)
    assert messages.status_code == 200
    assert [item["kind"] for item in messages.json()["items"]] == ["COMPLETION_REPORT"]
    assert messages.json()["items"][0]["body"] == (
        "Работы выполнены участником без заранее сохранённого Telegram ID."
    )
    inbox_path = "/api/v1/notifications"
    inbox = ticket_client.get(inbox_path)
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["total"] == inbox.json()["unread_count"] == 1
    notification = inbox.json()["items"][0]
    assert notification["ticket_id"] == ticket_id
    assert notification["sender_name"] == "Другой монтажник"
    assert notification["awaiting_verification"] is True
    assert ticket_client.get("/api/v1/tickets").json()["items"][0]["field_report_pending"] is True
    read_path = f"{inbox_path}/{notification['id']}/read"
    assert ticket_client.post(read_path).status_code == 403
    assert ticket_client.post(read_path, headers=csrf(ticket_client)).status_code == 204
    assert ticket_client.post(read_path, headers=csrf(ticket_client)).status_code == 204
    assert ticket_client.get(inbox_path).json()["unread_count"] == 0
    login(ticket_client, second_engineer.login)
    assert ticket_client.get(inbox_path).json()["unread_count"] == 1
    login(ticket_client, own_only_engineer.login)
    assert ticket_client.get(inbox_path).json()["total"] == 0
    assert ticket_client.post(read_path, headers=csrf(ticket_client)).status_code == 404
    login(ticket_client, operator.login)
    assert ticket_client.get(inbox_path).status_code == 403
    assert ticket_client.post(read_path, headers=csrf(ticket_client)).status_code == 403
    login(ticket_client, engineer.login)
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(FieldReportNotificationRead)) == 1
    verify_path = f"{path}/field-work-handoffs/{handoff['id']}/verify"
    assert ticket_client.post(verify_path).status_code == 403
    verified = ticket_client.post(verify_path, headers=csrf(ticket_client))
    assert verified.status_code == 200, verified.text
    assert verified.json()["work_status"] == "VERIFIED"
    assert verified.json()["verified_by"] == str(engineer.id)
    assert ticket_client.get(path).json()["status"] == "IN_PROGRESS"
    assert ticket_client.get("/api/v1/tickets").json()["items"][0]["field_report_pending"] is False
    assert ticket_client.get(inbox_path).json()["items"][0]["awaiting_verification"] is False


def test_dynamic_group_binding_requires_admin_and_tracks_supergroup_migration(
    ticket_engine: Engine,
) -> None:
    class AdminGateway:
        sent: list[tuple[str, str]] = []
        admin_ids = {501, 900}

        def get_me(self) -> dict[str, int]:
            return {"id": 900}

        def get_chat_member(self, _chat_id: str, user_id: int) -> dict[str, str]:
            return {"status": "administrator" if user_id in self.admin_ids else "member"}

        def send_message(self, chat_id: str, text: str, **_kwargs: object) -> DispatchReceipt:
            self.sent.append((chat_id, text))
            return DispatchReceipt(chat_id, "1")

    gateway = AdminGateway()
    command_update = {
        "update_id": 9901,
        "message": {
            "message_id": 1,
            "from": {"id": 501, "is_bot": False, "first_name": "Admin"},
            "chat": {"id": -7654321, "type": "supergroup", "title": "Synthetic Destination"},
            "text": "/oss_bind",
        },
    }
    with Session(ticket_engine) as db:
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.status = "UNBOUND"
        binding.chat_id = binding.chat_title = binding.chat_type = None
        db.commit()
        with pytest.raises(ApiError, match="telegram_group_not_bound"):
            destination_chat_id(db)

        result = process_group_control(db, gateway, command_update)
        assert result is not None and result.outcome == "PROCESSED"
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        assert binding.chat_id == "-7654321" and binding.status == "ACTIVE"
        assert gateway.sent[-1][0] == "-7654321"

        migration = {
            "update_id": 9902,
            "message": {
                "message_id": 2,
                "from": {"id": 900, "is_bot": True},
                "chat": {"id": -1007654321, "type": "supergroup", "title": "Synthetic Destination"},
                "migrate_from_chat_id": -7654321,
            },
        }
        result = process_group_control(db, gateway, migration)
        assert result is not None and result.outcome == "PROCESSED"
        assert binding.chat_id == "-1007654321"
        assert chat_id_aliases(db, binding.chat_id) == {"-7654321", "-1007654321"}

        denied = {
            **command_update,
            "update_id": 9903,
            "message": {
                **command_update["message"],
                "chat": {"id": -1111111, "type": "group", "title": "Untrusted"},
                "from": {"id": 502, "is_bot": False, "first_name": "Member"},
            },
        }
        result = process_group_control(db, gateway, denied)
        assert result is not None and result.outcome == "DENIED"
        assert binding.chat_id == "-1007654321"

        move = {
            **command_update,
            "update_id": 9904,
            "message": {
                **command_update["message"],
                "chat": {"id": -8888888, "type": "group", "title": "Next Synthetic Group"},
            },
        }
        result = process_group_control(db, gateway, move)
        assert result is not None and result.outcome == "PROCESSED"
        assert binding.chat_id == "-8888888"

        removed = {
            "update_id": 9905,
            "my_chat_member": {
                "chat": {"id": -8888888, "type": "group", "title": "Next Synthetic Group"},
                "new_chat_member": {"status": "kicked", "user": {"id": 900, "is_bot": True}},
            },
        }
        result = process_group_control(db, gateway, removed)
        assert result is not None and result.outcome == "PROCESSED"
        with pytest.raises(ApiError, match="telegram_bot_not_in_bound_group"):
            destination_chat_id(db)


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (DispatchRejected("telegram_http_400"), "FAILED", "telegram_http_400"),
        (DispatchRejected("telegram_http_403"), "FAILED", "telegram_http_403"),
        (DispatchUncertain(), "UNCERTAIN", "telegram_transport_uncertain"),
    ],
)
def test_completion_prompt_failure_never_retries_automatically(
    ticket_engine: Engine,
    ticket_client: TestClient,
    failure: Exception,
    expected_status: str,
    expected_code: str,
) -> None:
    engineer = seed_user(ticket_engine, f"prompt_failure_{expected_status.lower()}", Role.ENGINEER)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client)
    with Session(ticket_engine) as db:
        handoff = FieldWorkHandoff(
            ticket_id=UUID(ticket["id"]),
            ticket_version=1,
            reason="Synthetic field visit",
            delivery_status="SENT",
            work_status="AWAITING_REPORT",
            chat_id="-5319791765",
            telegram_message_id=701,
            created_by=engineer.id,
            sent_by=engineer.id,
            sent_at=datetime.now(UTC),
        )
        db.add(handoff)
        db.flush()
        update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot",
            update_id=91001,
            kind="COMPLETION_CALLBACK",
            outcome="PROCESSED",
            handoff_id=handoff.id,
            sync_status="PENDING",
        )
        db.add(update)
        db.flush()
        prompt = FieldWorkCompletionPrompt(
            handoff_id=handoff.id,
            callback_update_id=update.id,
            chat_id=handoff.chat_id,
            sender_telegram_id=501,
            sender_name="Монтажник Тест",
            status="REQUESTING",
        )
        db.add(prompt)
        db.commit()
        prompt_id = prompt.id

        class FailingGateway:
            calls = 0
            alerts: list[tuple[str, bool]] = []

            def send_completion_prompt(self, chat_id: str, message_id: int) -> DispatchReceipt:
                self.calls += 1
                assert chat_id == "-5319791765" and message_id == 701
                with Session(ticket_engine) as separate_db:
                    persisted = separate_db.get(FieldWorkCompletionPrompt, prompt_id)
                    assert persisted is not None and persisted.status == "UNCERTAIN"
                raise failure

            def acknowledge_callback(
                self, _callback_id: str, message: str, *, show_alert: bool = False,
            ) -> None:
                self.alerts.append((message, show_alert))

        gateway = FailingGateway()
        _sync_callback(db, gateway, "synthetic-callback", prompt_id)
        _send_prompt(db, gateway, prompt_id)
        assert gateway.calls == 1
        assert len(gateway.alerts) == 1 and gateway.alerts[0][1] is True
        assert "Вопрос" in gateway.alerts[0][0] or "вопрос" in gateway.alerts[0][0]
        saved = db.get(FieldWorkCompletionPrompt, prompt_id)
        assert saved is not None and saved.status == expected_status
        assert saved.error_code == expected_code
        assert saved.telegram_message_id is None
        receipt = db.get(FieldDispatchUpdate, update.id)
        assert receipt is not None and receipt.sync_status == "FAILED"
        assert receipt.sync_error_code == expected_code

        def retry_callback(update_id: int) -> dict:
            return {
                "update_id": update_id,
                "callback_query": {
                    "id": f"retry-{update_id}",
                    "from": {"id": 501, "is_bot": False, "first_name": "Synthetic"},
                    "data": f"field_done:{handoff.id}",
                    "message": {
                        "message_id": 701,
                        "chat": {"id": -5319791765, "type": "group"},
                    },
                },
            }

        immediate = process_update(db, retry_callback(91002))
        assert immediate.outcome == "DUPLICATE" and immediate.prompt_id == prompt_id
        saved.created_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()
        later = process_update(db, retry_callback(91003))
        assert later.outcome == "PROCESSED" and later.prompt_id != prompt_id


def test_completion_prompt_after_group_migration_keeps_report_bound_to_handoff(
    ticket_engine: Engine, ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "migrated_prompt_engineer", Role.ENGINEER)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client)
    old_chat, new_chat = "-5319791765", "-1005319791765"
    with Session(ticket_engine) as db:
        binding = db.get(FieldDispatchGroupBinding, 1)
        assert binding is not None
        binding.chat_id, binding.chat_type, binding.status = new_chat, "supergroup", "ACTIVE"
        binding.chat_title = "Synthetic destination"
        migration_update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot", update_id=92001,
            kind="GROUP_EVENT", outcome="PROCESSED",
        )
        db.add(migration_update)
        db.flush()
        db.add(FieldDispatchChatMigration(
            old_chat_id=old_chat, new_chat_id=new_chat,
            update_record_id=migration_update.id,
        ))
        handoff = FieldWorkHandoff(
            ticket_id=UUID(ticket["id"]), ticket_version=1, reason="Synthetic visit",
            delivery_status="SENT", work_status="AWAITING_REPORT",
            chat_id=old_chat, telegram_message_id=204,
            created_by=engineer.id, sent_by=engineer.id, sent_at=datetime.now(UTC),
        )
        db.add(handoff)
        db.flush()
        update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot", update_id=92002,
            kind="COMPLETION_CALLBACK", outcome="PROCESSED",
            handoff_id=handoff.id, sync_status="PENDING",
        )
        db.add(update)
        db.flush()
        prompt = FieldWorkCompletionPrompt(
            handoff_id=handoff.id, callback_update_id=update.id, chat_id=old_chat,
            sender_telegram_id=501, sender_name="Synthetic installer",
            status="REQUESTING", prompt_type="COMPLETION",
        )
        db.add(prompt)
        db.commit()
        prompt_id = prompt.id
        handoff_id = handoff.id

        class Gateway:
            sends: list[tuple[str, int | None, str | None]] = []
            answers: list[tuple[str, bool]] = []

            def send_completion_prompt(
                self, chat_id: str, message_id: int | None, *, context: str | None = None,
            ) -> DispatchReceipt:
                self.sends.append((chat_id, message_id, context))
                if chat_id == old_chat:
                    raise DispatchRejected("telegram_http_403")
                return DispatchReceipt(chat_id, "704")

            def acknowledge_callback(
                self, _callback_id: str, text: str, *, show_alert: bool = False,
            ) -> None:
                self.answers.append((text, show_alert))

        gateway = Gateway()
        forged_callback = {
            "update_id": 92000,
            "callback_query": {
                "id": "wrong-migrated-chat",
                "from": {"id": 501, "is_bot": False, "first_name": "Installer"},
                "data": f"field_done:{handoff_id}",
                "message": {"message_id": 204, "chat": {"id": int(new_chat), "type": "supergroup"}},
            },
        }
        assert process_update(db, forged_callback).outcome == "DENIED"
        _sync_callback(db, gateway, "synthetic-callback", prompt_id)
        context = f"Заявка #{ticket['number']} · {prompt_id}"
        assert gateway.sends == [(old_chat, 204, None), (new_chat, None, context)]
        assert gateway.answers == [("Вопрос отправлен в группу. Ответьте на него", False)]
        saved_prompt = db.get(FieldWorkCompletionPrompt, prompt_id)
        assert saved_prompt is not None
        assert saved_prompt.status == "WAITING" and saved_prompt.chat_id == new_chat
        assert saved_prompt.telegram_message_id == 704
        _send_prompt(db, gateway, prompt_id)
        assert len(gateway.sends) == 2

        # Message IDs are scoped to a chat. The old chat may independently
        # contain a prompt numbered 704; it must not capture a new-chat reply.
        other_handoff = FieldWorkHandoff(
            ticket_id=UUID(ticket["id"]), ticket_version=2, reason="Other synthetic visit",
            delivery_status="SENT", work_status="AWAITING_REPORT",
            chat_id=old_chat, telegram_message_id=205,
            created_by=engineer.id, sent_by=engineer.id, sent_at=datetime.now(UTC),
        )
        db.add(other_handoff)
        db.flush()
        other_update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot", update_id=92004,
            kind="COMPLETION_CALLBACK", outcome="PROCESSED",
            handoff_id=other_handoff.id, sync_status="SYNCED",
        )
        db.add(other_update)
        db.flush()
        db.add(FieldWorkCompletionPrompt(
            handoff_id=other_handoff.id, callback_update_id=other_update.id,
            chat_id=old_chat, sender_telegram_id=503, sender_name="Other installer",
            status="WAITING", prompt_type="COMPLETION",
            telegram_message_id=704, sent_at=datetime.now(UTC),
        ))
        db.commit()
        other_handoff_id = other_handoff.id

        # Simulate a lost sendMessage receipt for the migrated-chat fallback.
        # Its prompt has no quoted old card; the exact context must recover it.
        saved_prompt.status = "UNCERTAIN"
        saved_prompt.error_code = "telegram_transport_uncertain"
        saved_prompt.telegram_message_id = None
        saved_prompt.sent_at = None
        db.commit()

        reply = {
            "update_id": 92003,
            "message": {
                "message_id": 705, "date": 1_800_000_010,
                "from": {"id": 502, "is_bot": False, "first_name": "Synthetic"},
                "chat": {"id": int(new_chat), "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 704, "date": 1_800_000_009,
                    "from": {"id": 9999, "is_bot": True},
                    "chat": {"id": int(new_chat), "type": "supergroup"},
                    "text": completion_prompt_text(None, context),
                },
                "text": "Synthetic work completed",
            },
        }
        result = process_update(db, reply)
        assert result.outcome == "PROCESSED" and result.handoff_id == handoff_id
        assert db.get(FieldWorkHandoff, handoff_id).work_status == "REPORTED"
        assert db.get(FieldWorkHandoff, other_handoff_id).work_status == "AWAITING_REPORT"
        assert process_update(db, reply).duplicate
        assert db.scalar(select(func.count()).select_from(FieldExternalMessage).where(
            FieldExternalMessage.handoff_id == handoff_id,
            FieldExternalMessage.kind == "COMPLETION_REPORT",
        )) == 1


def test_ochamchira_completion_retry_and_late_old_reply_are_idempotent(
    ticket_engine: Engine, ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "ochamchira_retry_engineer", Role.ENGINEER)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client)
    chat_id = "-1004473201977"
    with Session(ticket_engine) as db:
        handoff = FieldWorkHandoff(
            ticket_id=UUID(ticket["id"]), ticket_version=1, reason="Synthetic visit",
            delivery_status="SENT", work_status="AWAITING_REPORT",
            chat_id=chat_id, telegram_message_id=35,
            created_by=engineer.id, sent_by=engineer.id, sent_at=datetime.now(UTC),
        )
        db.add(handoff)
        db.flush()
        original_update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot", update_id=93001,
            kind="COMPLETION_CALLBACK", outcome="PROCESSED",
            handoff_id=handoff.id, sync_status="SYNCED",
        )
        db.add(original_update)
        db.flush()
        old_prompt = FieldWorkCompletionPrompt(
            handoff_id=handoff.id, callback_update_id=original_update.id,
            chat_id=chat_id, sender_telegram_id=501, sender_name="Synthetic installer",
            status="WAITING", prompt_type="COMPLETION", telegram_message_id=38,
            created_at=datetime.now(UTC) - timedelta(minutes=2), sent_at=datetime.now(UTC),
        )
        db.add(old_prompt)
        db.commit()
        handoff_id, old_prompt_id = handoff.id, old_prompt.id

        def callback(update_id: int, sender_id: int) -> dict:
            return {
                "update_id": update_id,
                "callback_query": {
                    "id": f"synthetic-{update_id}",
                    "from": {"id": sender_id, "is_bot": False, "first_name": "Synthetic"},
                    "data": f"field_done:{handoff_id}",
                    "message": {
                        "message_id": 35,
                        "chat": {"id": int(chat_id), "type": "supergroup"},
                    },
                },
            }

        first = process_update(db, callback(93002, 501))
        assert first.outcome == "PROCESSED" and first.prompt_id != old_prompt_id
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).status == "CANCELED"
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).deletion_status == "NOT_REQUIRED"
        second = process_update(db, callback(93003, 502))
        assert second.outcome == "DUPLICATE" and second.prompt_id == first.prompt_id

        class Gateway:
            calls: list[tuple[str, int, str]] = []

            def send_completion_prompt(
                self, target: str, message_id: int, *, context: str,
            ) -> DispatchReceipt:
                self.calls.append((target, message_id, context))
                return DispatchReceipt(target, str(38 + len(self.calls)))

        gateway = Gateway()
        _send_prompt(db, gateway, first.prompt_id)
        assert gateway.calls == [
            (chat_id, 35, f"Заявка #{ticket['number']} · {first.prompt_id}")
        ]
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).deletion_status == "PENDING"
        _delete_prompt(db, gateway, old_prompt_id)
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).deletion_status == "PENDING"
        assert db.get(FieldWorkCompletionPrompt, first.prompt_id).status == "WAITING"
        db.get(FieldWorkCompletionPrompt, first.prompt_id).created_at = (
            datetime.now(UTC) - timedelta(minutes=2)
        )
        db.commit()
        third = process_update(db, callback(93006, 501))
        assert third.outcome == "PROCESSED"
        _send_prompt(db, gateway, third.prompt_id)
        assert db.get(FieldWorkCompletionPrompt, third.prompt_id).telegram_message_id == 40
        db.get(FieldWorkCompletionPrompt, third.prompt_id).created_at = (
            datetime.now(UTC) - timedelta(minutes=2)
        )
        db.commit()
        limited = process_update(db, callback(93007, 502))
        assert limited.outcome == "DUPLICATE" and limited.prompt_id == third.prompt_id
        assert db.scalar(select(func.count()).select_from(FieldWorkCompletionPrompt).where(
            FieldWorkCompletionPrompt.handoff_id == handoff_id,
        )) == 3

        def reply(update_id: int, reply_to: int, sender_id: int) -> dict:
            return {
                "update_id": update_id,
                "message": {
                    "message_id": update_id, "date": 1_800_000_010,
                    "from": {"id": sender_id, "is_bot": False, "first_name": "Synthetic"},
                    "chat": {"id": int(chat_id), "type": "supergroup"},
                    "reply_to_message": {"message_id": reply_to},
                    "text": "Synthetic repair completed",
                },
            }

        accepted = process_update(db, reply(93004, 38, 502))
        assert accepted.outcome == "PROCESSED" and accepted.handoff_id == handoff_id
        assert db.get(FieldWorkHandoff, handoff_id).work_status == "REPORTED"
        assert db.get(FieldWorkCompletionPrompt, third.prompt_id).status == "CANCELED"
        assert process_update(db, reply(93005, 39, 501)).outcome == "DUPLICATE"
        assert process_update(db, reply(93004, 38, 502)).duplicate
        assert db.scalar(select(func.count()).select_from(FieldExternalMessage).where(
            FieldExternalMessage.handoff_id == handoff_id,
            FieldExternalMessage.kind == "COMPLETION_REPORT",
        )) == 1


def test_uncertain_completion_can_retry_and_accept_the_old_delivered_question(
    ticket_engine: Engine, ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "uncertain_retry_engineer", Role.ENGINEER)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client)
    chat_id = "-5319791765"
    with Session(ticket_engine) as db:
        handoff = FieldWorkHandoff(
            ticket_id=UUID(ticket["id"]), ticket_version=1, reason="Synthetic visit",
            delivery_status="SENT", work_status="AWAITING_REPORT",
            chat_id=chat_id, telegram_message_id=701,
            created_by=engineer.id, sent_by=engineer.id, sent_at=datetime.now(UTC),
        )
        db.add(handoff)
        db.flush()
        old_update = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot", update_id=94001,
            kind="COMPLETION_CALLBACK", outcome="PROCESSED", handoff_id=handoff.id,
            sync_status="FAILED", sync_error_code="telegram_transport_uncertain",
        )
        db.add(old_update)
        db.flush()
        old_prompt = FieldWorkCompletionPrompt(
            handoff_id=handoff.id, callback_update_id=old_update.id,
            chat_id=chat_id, sender_telegram_id=501, sender_name="Synthetic installer",
            status="UNCERTAIN", prompt_type="COMPLETION",
            error_code="telegram_transport_uncertain",
            created_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        db.add(old_prompt)
        db.commit()
        handoff_id, old_prompt_id = handoff.id, old_prompt.id
        retried = process_update(db, {
            "update_id": 94002,
            "callback_query": {
                "id": "retry-uncertain",
                "from": {"id": 502, "is_bot": False, "first_name": "Other installer"},
                "data": f"field_done:{handoff_id}",
                "message": {
                    "message_id": 701,
                    "chat": {"id": int(chat_id), "type": "group"},
                },
            },
        })
        assert retried.outcome == "PROCESSED"
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).status == "FAILED"
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).error_code == "superseded_uncertain"
        late_reply = {
            "update_id": 94003,
            "message": {
                "message_id": 705, "date": 1_800_000_010,
                "from": {"id": 503, "is_bot": False, "first_name": "Third installer"},
                "chat": {"id": int(chat_id), "type": "group"},
                "reply_to_message": {
                    "message_id": 704, "date": 1_800_000_009,
                    "from": {"id": 9999, "is_bot": True},
                    "chat": {"id": int(chat_id), "type": "group"},
                    "text": "Что было сделано?",
                    "reply_to_message": {
                        "message_id": 701,
                        "chat": {"id": int(chat_id), "type": "group"},
                    },
                },
                "text": "Synthetic work completed",
            },
        }
        accepted = process_update(db, late_reply)
        assert accepted.outcome == "PROCESSED" and accepted.handoff_id == handoff_id
        assert db.get(FieldWorkHandoff, handoff_id).work_status == "REPORTED"
        assert db.get(FieldWorkCompletionPrompt, old_prompt_id).status == "COMPLETED"
        assert db.get(FieldWorkCompletionPrompt, retried.prompt_id).status == "FAILED"
        assert process_update(db, late_reply).duplicate

def test_general_report_period_events_and_operator_scope(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    """Events count once, even after a second resolution; INTERNAL never leaks."""
    from datetime import date

    from app.modules.reports.service import utc_bounds

    assert utc_bounds(date(2026, 11, 10), date(2026, 11, 10)) == (
        datetime(2026, 11, 9, 21, tzinfo=UTC),
        datetime(2026, 11, 10, 21, tzinfo=UTC),
    )
    engineer = seed_user(ticket_engine, "report_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "report_operator", Role.OPERATOR)
    login(ticket_client, engineer.login)
    internal = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "ticket_type": "INTERNAL",
            "subject": "Private synthetic report",
            "description": "Synthetic",
            "client_request_id": str(uuid4()),
        },
    )
    assert internal.status_code == 201, internal.text
    login(ticket_client, operator.login)
    manual = make_manual_ticket(ticket_client, subject="Visible synthetic report")
    current = datetime.now(UTC)
    start = (current + timedelta(hours=3)).date()
    with Session(ticket_engine) as db:
        db.add_all(
            [
                TicketEvent(
                    ticket_id=UUID(manual["id"]),
                    event_type=kind,
                    actor_id=engineer.id,
                    event_data={},
                    ticket_version=index + 2,
                    occurred_at=current + timedelta(minutes=index),
                )
                for index, kind in enumerate(
                    (
                        "ticket_resolved",
                        "callback_returned_engineers",
                        "ticket_resolved",
                        "callback_confirmed_closed",
                    )
                )
            ]
        )
        db.commit()
    params = {"start": start.isoformat(), "end": start.isoformat(), "page": 1, "size": 1}
    assert ticket_client.get("/api/v1/reports/general", params=params).status_code == 403
    admin = seed_user(ticket_engine, "report_admin", Role.ADMIN)
    login(ticket_client, admin.login)
    granted = ticket_client.put(
        f"/api/v1/users/{operator.id}/permissions",
        headers=csrf(ticket_client),
        json={"permissions": [Permission.TICKET_READ, Permission.REPORTS_READ]},
    )
    assert granted.status_code == 200, granted.text
    login(ticket_client, operator.login)
    response = ticket_client.get("/api/v1/reports/general", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metrics"]["created"] == 1
    assert body["metrics"]["resolved_events"] == 2
    assert body["metrics"]["returned_events"] == 1
    assert body["metrics"]["closed_events"] == 1
    assert body["metrics"]["average_resolution_seconds"] is not None
    assert 0 <= body["metrics"]["average_resolution_seconds"] < 120
    assert body["metrics"]["average_closure_seconds"] is not None
    assert body["details"]["total"] == 1
    assert body["details"]["items"][0]["id"] == manual["id"]
    assert body["distribution"]["types"] == {"MANUAL": 1}
    assert body["daily"][0]["closed"] == 1
    assert (
        ticket_client.get(
            "/api/v1/reports/general",
            params={
                **params,
                "start": (start + timedelta(days=1)).isoformat(),
            },
        ).status_code
        == 422
    )
    assert ticket_client.get(
        "/api/v1/reports/general", params={**params, "start": "9999-12-31", "end": "9999-12-31"}
    ).status_code == 422
    empty = ticket_client.get(
        "/api/v1/reports/general",
        params={
            **params,
            "start": (start - timedelta(days=20)).isoformat(),
            "end": (start - timedelta(days=20)).isoformat(),
        },
    ).json()
    assert empty["metrics"]["created"] == 0
    assert empty["metrics"]["average_resolution_seconds"] is None
    assert empty["metrics"]["active_now"] == 1
    login(ticket_client, admin.login)
    revoked = ticket_client.put(
        f"/api/v1/users/{operator.id}/permissions",
        headers=csrf(ticket_client),
        json={"permissions": [Permission.TICKET_READ]},
    )
    assert revoked.status_code == 200, revoked.text
    login(ticket_client, operator.login)
    assert ticket_client.get("/api/v1/reports/general", params=params).status_code == 403
    login(ticket_client, engineer.login)
    visible = ticket_client.get("/api/v1/reports/general", params=params).json()
    assert visible["metrics"]["created"] == 2
    assert visible["metrics"]["active_now"] == 2
    assert visible["details"]["total"] == 2
    assert ticket_client.get("/api/v1/reports/general", params={**params, "page": 2}).json()[
        "details"
    ]["items"]

    restricted = seed_user(ticket_engine, "report_restricted", Role.ENGINEER)
    with Session(ticket_engine) as db:
        user = db.get(User, restricted.id)
        assert user is not None
        user.permissions_customized = True
        db.add_all(
            [
                UserPermission(user_id=user.id, permission=Permission.TICKET_READ_OWN),
                UserPermission(user_id=user.id, permission=Permission.REPORTS_READ),
            ]
        )
        db.commit()
    login(ticket_client, restricted.login)
    own = ticket_client.get("/api/v1/reports/general", params=params)
    assert own.status_code == 200 and own.json()["metrics"]["created"] == 0
    with Session(ticket_engine) as db:
        db.query(UserPermission).filter(
            UserPermission.user_id == restricted.id,
            UserPermission.permission == Permission.TICKET_READ_OWN,
        ).delete()
        db.commit()
    assert ticket_client.get("/api/v1/reports/general", params=params).status_code == 403
def test_employee_and_callback_reports_use_action_authors_and_visibility(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "phase2_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "phase2_operator", Role.OPERATOR)
    login(ticket_client, engineer.login)
    internal = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "ticket_type": "INTERNAL",
            "subject": "Private phase2",
            "description": "Synthetic",
            "client_request_id": str(uuid4()),
        },
    ).json()
    login(ticket_client, operator.login)
    manual = make_manual_ticket(ticket_client, subject="Visible phase2")
    deleted = make_manual_ticket(ticket_client, subject="Deleted phase2")
    now = datetime.now(UTC)
    local_day = (now + timedelta(hours=3)).date().isoformat()
    with Session(ticket_engine) as db:
        removed = db.get(Ticket, UUID(deleted["id"]))
        assert removed is not None
        removed.deleted_at = now
        removed.deleted_by = operator.id
        db.flush()
        for ticket, outcomes in (
            (manual, ("CONFIRMED", "NO_ANSWER")),
            (internal, ("STILL_BROKEN",)),
            (deleted, ("CONFIRMED",)),
        ):
            for outcome in outcomes:
                decision = TicketResolution(
                    ticket_id=UUID(ticket["id"]),
                    engineer_id=engineer.id,
                    decision_text="Synthetic decision",
                    created_at=now,
                )
                db.add(decision)
                db.flush()
                db.add(
                    TicketCallback(
                        ticket_id=UUID(ticket["id"]),
                        decision_id=decision.id,
                        client_request_id=uuid4(),
                        actor_id=operator.id,
                        outcome=outcome,
                        comment="Synthetic" if outcome == "STILL_BROKEN" else None,
                        occurred_at=now,
                    )
                )
        db.commit()
    params = {"start": local_day, "end": local_day}
    assert ticket_client.get("/api/v1/reports/employees", params=params).status_code == 403
    assert ticket_client.get("/api/v1/reports/callbacks", params=params).status_code == 403
    with Session(ticket_engine) as db:
        account = db.get(User, operator.id)
        assert account is not None
        account.permissions_customized = True
        db.add_all(
            [
                UserPermission(user_id=operator.id, permission=Permission.TICKET_READ),
                UserPermission(user_id=operator.id, permission=Permission.REPORTS_READ),
            ]
        )
        db.commit()
    employees = ticket_client.get("/api/v1/reports/employees", params=params)
    assert employees.status_code == 200, employees.text
    by_id = {row["user_id"]: row for row in employees.json()["items"]}
    assert by_id[str(operator.id)]["created_tickets"] == 1
    assert by_id[str(operator.id)]["callback_events"] == 2
    assert by_id[str(operator.id)]["callback_tickets"] == 1
    assert by_id[str(operator.id)]["outcomes"] == {
        "CONFIRMED": 1,
        "STILL_BROKEN": 0,
        "NO_ANSWER": 1,
    }
    assert by_id[str(engineer.id)]["decision_events"] == 2
    assert sum(row["decisions"] for row in employees.json()["daily"]) == 2
    callbacks = ticket_client.get(
        "/api/v1/reports/callbacks", params={**params, "page": 1, "size": 1}
    )
    assert callbacks.status_code == 200, callbacks.text
    body = callbacks.json()
    assert body["total_events"] == body["details"]["total"] == 2
    assert body["unique_tickets"] == 1
    assert body["outcomes"] == {"CONFIRMED": 1, "STILL_BROKEN": 0, "NO_ANSWER": 1}
    assert sum(row["count"] for row in body["daily"]) == 2
    assert sum(row["count"] for row in body["employees"]) == 2
    assert len(body["details"]["items"]) == 1
    assert body["details"]["items"][0]["ticket_id"] == manual["id"]
    second = ticket_client.get(
        "/api/v1/reports/callbacks", params={**params, "page": 2, "size": 1}
    ).json()
    assert second["details"]["items"][0]["ticket_id"] == manual["id"]
    empty_period = {"start": (now.date() - timedelta(days=3)).isoformat(),
                    "end": (now.date() - timedelta(days=3)).isoformat()}
    assert ticket_client.get("/api/v1/reports/callbacks", params=empty_period).json()[
        "total_events"
    ] == 0
    assert ticket_client.get("/api/v1/reports/employees", params=empty_period).json()[
        "daily"
    ] == []
    login(ticket_client, engineer.login)
    assert ticket_client.get("/api/v1/reports/callbacks", params=params).json()["total_events"] == 3
    restricted = seed_user(ticket_engine, "phase2_restricted", Role.ENGINEER)
    with Session(ticket_engine) as db:
        account = db.get(User, restricted.id)
        assert account is not None
        account.permissions_customized = True
        db.add_all([
            UserPermission(user_id=restricted.id, permission=Permission.TICKET_READ_OWN),
            UserPermission(user_id=restricted.id, permission=Permission.REPORTS_READ),
        ])
        db.commit()
    login(ticket_client, restricted.login)
    assert ticket_client.get("/api/v1/reports/callbacks", params=params).json()["total_events"] == 0
    assert ticket_client.get("/api/v1/reports/employees", params=params).json()["items"] == []


def test_field_work_and_reasons_reports_count_events_without_leaks(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "phase2b_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "phase2b_operator", Role.OPERATOR)
    login(ticket_client, engineer.login)
    internal_response = ticket_client.post(
        "/api/v1/tickets", headers=csrf(ticket_client),
        json={"ticket_type": "INTERNAL", "subject": "Private phase2b",
              "description": "Synthetic", "client_request_id": str(uuid4())},
    )
    assert internal_response.status_code == 201
    internal = internal_response.json()
    login(ticket_client, operator.login)
    manual = make_manual_ticket(ticket_client, subject="Visible phase2b")
    with Session(ticket_engine) as db:
        category_id = db.scalar(
            select(TicketCategory.id).where(TicketCategory.name == "Нет интернета")
        )
    assert category_id is not None
    categorized_response = ticket_client.post(
        "/api/v1/tickets", headers=csrf(ticket_client),
        json={"ticket_type": "MANUAL", "category_id": str(category_id),
              "description": "Synthetic", **MANUAL_CONTACT, "client_request_id": str(uuid4())},
    )
    assert categorized_response.status_code == 201, categorized_response.text
    categorized = categorized_response.json()
    deleted = make_manual_ticket(ticket_client, subject="Deleted phase2b")
    created_day = (datetime.now(UTC) + timedelta(hours=3)).date()
    now = datetime(created_day.year, created_day.month, created_day.day, 20, 30, tzinfo=UTC)
    next_day = now + timedelta(hours=1)
    event_day = created_day + timedelta(days=1)
    with Session(ticket_engine) as db:
        for ticket, direction in ((manual, "SUKHUM"), (categorized, "OCHAMCHIRA"),
                                  (internal, "OCHAMCHIRA"), (deleted, "SUKHUM")):
            item = db.get(Ticket, UUID(ticket["id"]))
            assert item is not None
            if ticket is deleted:
                item.deleted_at = now
                item.deleted_by = operator.id
            if ticket is categorized:
                replacement = db.scalar(
                    select(TicketCategory).where(TicketCategory.name != "Нет интернета")
                )
                assert replacement is not None
                item.category_id = replacement.id
                item.category_name = replacement.name
                item.version += 1
                db.add(TicketEvent(
                    ticket_id=item.id, event_type="ticket_edited", actor_id=engineer.id,
                    event_data={"changes": {"category_name": {
                        "before": "Нет интернета", "after": replacement.name,
                    }}}, ticket_version=item.version, occurred_at=now,
                ))
            handoff = FieldWorkHandoff(
                ticket_id=item.id, ticket_version=1, reason="Synthetic visit",
                delivery_status="SENT",
                work_status="VERIFIED" if ticket is categorized else "AWAITING_REPORT",
                chat_id="-5319791765",
                direction=direction, telegram_message_id=100 + item.number,
                created_by=engineer.id, sent_by=engineer.id, sent_at=now,
                reported_at=next_day if ticket is categorized else None,
                verified_at=next_day + timedelta(minutes=5) if ticket is categorized else None,
                verified_by=engineer.id if ticket is categorized else None,
            )
            db.add(handoff)
            db.flush()
            reschedule_versions = (1, 2) if ticket is manual else ()
            for version in reschedule_versions:
                db.add(FieldWorkReschedule(
                    handoff_id=handoff.id, schedule_version=version,
                    reason="Попросил позже", source="OSS", author_name="Synthetic engineer",
                    actor_id=engineer.id, created_at=next_day,
                ))
            for index in (1, 2) if ticket is manual else (1,):
                db.add(TicketResolution(
                    ticket_id=item.id, engineer_id=engineer.id,
                    decision_text="Synthetic decision",
                    reason_name="Причина B" if ticket is categorized else "Причина A",
                    created_at=next_day + timedelta(minutes=index),
                ))
        db.commit()
    params = {"start": event_day.isoformat(), "end": event_day.isoformat()}
    assert ticket_client.get("/api/v1/reports/field-work", params=params).status_code == 403
    assert ticket_client.get("/api/v1/reports/reasons", params=params).status_code == 403
    with Session(ticket_engine) as db:
        user = db.get(User, operator.id)
        assert user is not None
        user.permissions_customized = True
        db.add_all([
            UserPermission(user_id=operator.id, permission=Permission.TICKET_READ),
            UserPermission(user_id=operator.id, permission=Permission.REPORTS_READ),
        ])
        db.commit()
    work = ticket_client.get("/api/v1/reports/field-work", params=params)
    assert work.status_code == 200, work.text
    assert work.json()["metrics"]["sent_handoffs"] == 0
    assert work.json()["metrics"]["reschedule_events"] == 2
    assert work.json()["metrics"]["rescheduled_handoffs"] == 1
    assert work.json()["metrics"]["awaiting_report_now"] == 1
    assert work.json()["metrics"]["reported_handoffs"] == 1
    assert work.json()["metrics"]["verified_handoffs"] == 1
    assert work.json()["details"]["total"] == 2
    assert {row["ticket_id"] for row in work.json()["details"]["items"]} == {
        manual["id"], categorized["id"]
    }
    assert work.json()["daily"][0]["date"] == event_day.isoformat()
    assert ticket_client.get("/api/v1/reports/field-work", params={
        **params, "direction": "OCHAMCHIRA"
    }).json()["metrics"]["reschedule_events"] == 0
    sent = ticket_client.get("/api/v1/reports/field-work", params={
        "start": created_day.isoformat(), "end": created_day.isoformat()
    }).json()
    assert sent["metrics"]["sent_handoffs"] == 2
    assert sent["metrics"]["sent_unique_tickets"] == 2
    reasons = ticket_client.get("/api/v1/reports/reasons", params=params)
    assert reasons.status_code == 200, reasons.text
    assert reasons.json()["metrics"]["decision_events"] == 3
    assert reasons.json()["metrics"]["decision_unique_tickets"] == 2
    decision_details = ticket_client.get("/api/v1/reports/reasons", params={
        **params, "detail_kind": "decision", "size": 1, "page": 2,
    }).json()["details"]
    assert decision_details["total"] == 3 and len(decision_details["items"]) == 1
    assert decision_details["items"][0]["ticket_id"] in {manual["id"], categorized["id"]}
    created = ticket_client.get("/api/v1/reports/reasons", params={
        "start": created_day.isoformat(), "end": created_day.isoformat()
    }).json()
    assert created["metrics"]["created_tickets"] == 2
    assert created["metrics"]["without_category"] == 1
    assert created["metrics"]["created_without_reason_now"] == 0
    assert {row["name"] for row in created["categories"]} == {None, "Нет интернета"}
    filtered = ticket_client.get("/api/v1/reports/reasons", params={
        **params, "category": "Нет интернета"
    }).json()
    assert filtered["metrics"]["decision_events"] == 1


def test_management_exports_are_complete_scoped_and_safe(
    ticket_engine: Engine, ticket_client: TestClient
) -> None:
    engineer = seed_user(ticket_engine, "export_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "export_operator", Role.OPERATOR)
    login(ticket_client, engineer.login)
    internal_response = ticket_client.post(
        "/api/v1/tickets", headers=csrf(ticket_client),
        json={"ticket_type": "INTERNAL", "subject": "Private export ticket",
              "description": "Synthetic", "client_request_id": str(uuid4())},
    )
    assert internal_response.status_code == 201
    login(ticket_client, operator.login)
    manual = make_manual_ticket(ticket_client, subject="=1+1")
    now = datetime.now(UTC)
    day = (now + timedelta(hours=3)).date().isoformat()
    with Session(ticket_engine) as db:
        decision = None
        for _ in range(2):
            decision = TicketResolution(
                ticket_id=UUID(manual["id"]), engineer_id=engineer.id,
                decision_text="Synthetic", reason_name="Обрыв линии", created_at=now,
            )
            db.add(decision)
            db.flush()
        assert decision is not None
        db.add(TicketCallback(
            ticket_id=UUID(manual["id"]), decision_id=decision.id,
            client_request_id=uuid4(), actor_id=operator.id, outcome="NO_ANSWER",
            occurred_at=now,
        ))
        handoff = FieldWorkHandoff(
            ticket_id=UUID(manual["id"]), ticket_version=1, reason="Synthetic visit",
            delivery_status="SENT", work_status="AWAITING_REPORT", chat_id="-5319791765",
            direction="SUKHUM", telegram_message_id=903, created_by=engineer.id,
            sent_by=engineer.id, sent_at=now,
        )
        db.add(handoff)
        db.flush()
        for index in (1, 2):
            db.add(FieldWorkReschedule(
                handoff_id=handoff.id, schedule_version=index, reason="Попросил позже",
                source="OSS", author_name="Synthetic engineer", actor_id=engineer.id,
                created_at=now,
            ))
        db.commit()

    params = {"start": day, "end": day}
    for suffix in ("xlsx", "pdf"):
        response = ticket_client.get(f"/api/v1/reports/export.{suffix}", params=params)
        assert response.status_code == 403
    with Session(ticket_engine) as db:
        user = db.get(User, operator.id)
        assert user is not None
        user.permissions_customized = True
        db.add_all([
            UserPermission(user_id=operator.id, permission=Permission.TICKET_READ),
            UserPermission(user_id=operator.id, permission=Permission.REPORTS_READ),
        ])
        db.commit()

    excel = ticket_client.get("/api/v1/reports/export.xlsx", params=params)
    assert excel.status_code == 200, excel.text[:300] if excel.status_code != 200 else ""
    assert excel.content[:2] == b"PK"
    assert "no-store" in excel.headers["cache-control"]
    book = load_workbook(BytesIO(excel.content), read_only=True, data_only=False)
    assert book.sheetnames == [
        "Сводка OSS", "Общая статистика", "Сотрудники", "Контрольные звонки",
        "Монтажные работы", "Причины обращений",
    ]
    general_cells = [cell for row in book["Общая статистика"] for cell in row]
    assert any(cell.value == "'=1+1" and cell.data_type == "s" for cell in general_cells)
    assert not any(cell.value == "Private export ticket" for cell in general_cells)
    field_values = [cell.value for row in book["Монтажные работы"] for cell in row]
    assert 2 in field_values and "Попросил позже" in field_values
    reason_values = [cell.value for row in book["Причины обращений"] for cell in row]
    assert "Обрыв линии" in reason_values
    book.close()

    pdf = ticket_client.get("/api/v1/reports/export.pdf", params=params)
    assert pdf.status_code == 200, pdf.text[:300] if pdf.status_code != 200 else ""
    assert pdf.content.startswith(b"%PDF-")
    text_content = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf.content)).pages
    )
    assert "Контрольный обзвон" in text_content
    assert "Обрыв линии" in text_content
    assert "Private export ticket" not in text_content

    no_events = ticket_client.get("/api/v1/reports/export.xlsx", params={
        "start": "2000-01-01", "end": "2000-01-01",
    })
    assert no_events.status_code == 200
    empty_book = load_workbook(BytesIO(no_events.content), read_only=True)
    assert any(
        cell.value == "Нет данных за период"
        for row in empty_book["Контрольные звонки"] for cell in row
    )
    empty_book.close()

    with Session(ticket_engine) as db:
        account = db.get(User, operator.id)
        assert account is not None
        with pytest.raises(ApiError) as error:
            load_bundle(
                db,
                start=(now + timedelta(hours=3)).date(),
                end=(now + timedelta(hours=3)).date(),
                user=account,
                read_all=True,
                direction=None,
                category=None,
                max_rows=1,
            )
    assert error.value.status_code == 413


def test_internal_task_conversation_notifications_and_isolated_ticket_state(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "task_engineer", Role.ENGINEER)
    sima = seed_user(ticket_engine, "task_sima", Role.OPERATOR)
    outsider = seed_user(ticket_engine, "task_outsider", Role.OPERATOR)
    grant_ticket_permission(ticket_engine, sima, Permission.TASK_EXECUTE)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client, subject="Synthetic task ticket")
    ticket_id = ticket["id"]
    ticket_before = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()
    request_id = str(uuid4())
    payload = {
        "ticket_id": ticket_id,
        "assignee_id": str(sima.id),
        "description": "Позвонить абоненту и уточнить время",
        "client_request_id": request_id,
    }
    created = ticket_client.post("/api/v1/tasks", headers=csrf(ticket_client), json=payload)
    assert created.status_code == 201, created.text
    task = created.json()
    task_id = task["id"]
    assert task["title"] == payload["description"]
    assert task["due_at"] is None
    assert (
        ticket_client.post("/api/v1/tasks", headers=csrf(ticket_client), json=payload).json()["id"]
        == task_id
    )
    assert ticket_client.get("/api/v1/tasks", params={"scope": "created"}).json()["total"] == 1

    login(ticket_client, outsider.login)
    assert ticket_client.get(f"/api/v1/tasks/{task_id}").status_code == 404
    login(ticket_client, sima.login)
    detail = ticket_client.get(f"/api/v1/tasks/{task_id}").json()
    assert detail["subscriber_name"] == ticket_before["caller_full_name"]
    assert detail["phone"] == ticket_before["caller_phone"]
    assert detail["account_no"] is None
    inbox = ticket_client.get("/api/v1/tasks/notifications").json()
    assert inbox["unread_count"] == 1 and inbox["items"][0]["kind"] == "CREATED"
    assert (
        ticket_client.post(
            f"/api/v1/tasks/notifications/{inbox['items'][0]['id']}/read",
            headers=csrf(ticket_client),
        ).status_code
        == 204
    )
    answer = "Абонент попросил перезвонить завтра"
    action = {"client_request_id": str(uuid4()), "kind": "COMPLETED", "body": answer}
    completed = ticket_client.post(
        f"/api/v1/tasks/{task_id}/events",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json=action,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["latest_answer"] == answer
    duplicate = ticket_client.post(
        f"/api/v1/tasks/{task_id}/events",
        headers={**csrf(ticket_client), "If-Match": '"1"'},
        json=action,
    )
    assert duplicate.status_code == 200 and duplicate.json()["version"] == 2
    for kind in ("MESSAGE", "NO_ANSWER", "REOPENED"):
        response = ticket_client.post(
            f"/api/v1/tasks/{task_id}/events",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"client_request_id": str(uuid4()), "kind": kind, "body": "Не нужно"},
        )
        assert response.status_code == 422
    assert (
        ticket_client.post(
            f"/api/v1/tasks/{task_id}/events",
            headers={**csrf(ticket_client), "If-Match": '"2"'},
            json={"client_request_id": str(uuid4()), "kind": "COMPLETED", "body": "Ещё раз"},
        ).status_code
        == 409
    )
    login(ticket_client, engineer.login)
    assert ticket_client.get("/api/v1/tasks/notifications").json()["unread_count"] == 1
    ticket_tasks = ticket_client.get(
        "/api/v1/tasks", params={"ticket_id": ticket_id, "scope": "created", "state": "completed"}
    ).json()
    assert ticket_tasks["items"][0]["latest_answer"] == answer
    ticket_after = ticket_client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert (
        ticket_after["status"],
        ticket_after["version"],
        ticket_after["assigned_engineer_id"],
    ) == (ticket_before["status"], ticket_before["version"], ticket_before["assigned_engineer_id"])
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(InternalTask)) == 1
        assert db.scalar(select(func.count()).select_from(InternalTaskEvent)) == 2
        assert db.scalar(select(func.count()).select_from(InternalTaskNotification)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketCallback)
                .where(TicketCallback.ticket_id == UUID(ticket_id))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(FieldWorkHandoff)
                .where(FieldWorkHandoff.ticket_id == UUID(ticket_id))
            )
            == 0
        )


def test_internal_task_never_exposes_internal_ticket_to_operator(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "task_internal_engineer", Role.ENGINEER)
    operator = seed_user(ticket_engine, "task_internal_operator", Role.OPERATOR)
    grant_ticket_permission(ticket_engine, operator, Permission.TASK_EXECUTE)
    login(ticket_client, engineer.login)
    created = ticket_client.post(
        "/api/v1/tickets",
        headers=csrf(ticket_client),
        json={
            "ticket_type": "INTERNAL",
            "subject": "Synthetic private task",
            "description": "Synthetic",
            "client_request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    assert str(operator.id) not in [
        str(entry["id"])
        for entry in ticket_client.get(
            "/api/v1/tasks/assignees", params={"ticket_id": ticket_id}
        ).json()
    ]
    denied = ticket_client.post(
        "/api/v1/tasks",
        headers=csrf(ticket_client),
        json={
            "ticket_id": ticket_id,
            "assignee_id": str(operator.id),
            "title": "Private",
            "description": "Synthetic",
            "client_request_id": str(uuid4()),
        },
    )
    assert denied.status_code == 422
    created_task = ticket_client.post(
        "/api/v1/tasks",
        headers=csrf(ticket_client),
        json={
            "ticket_id": ticket_id,
            "assignee_id": str(engineer.id),
            "title": "Private",
            "description": "Synthetic",
            "client_request_id": str(uuid4()),
        },
    )
    assert created_task.status_code == 201, created_task.text
    login(ticket_client, operator.login)
    assert ticket_client.get("/api/v1/tasks", params={"ticket_id": ticket_id}).status_code == 404
    assert ticket_client.get(f"/api/v1/tasks/{created_task.json()['id']}").status_code == 404
    assert ticket_client.get("/api/v1/tasks/notifications").json()["total"] == 0


def test_internal_task_contact_uses_confirmed_linked_snapshot(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "task_linked_engineer", Role.ENGINEER)
    snapshot, _ = seed_snapshot(
        ticket_engine, engineer, phone="+7 900 222 33 44", account_no="5113"
    )
    login(ticket_client, engineer.login)
    created = create(ticket_client, snapshot)
    assert created.status_code == 201, created.text
    task = ticket_client.post(
        "/api/v1/tasks", headers=csrf(ticket_client),
        json={"ticket_id": created.json()["id"], "assignee_id": str(engineer.id),
              "description": "Проверить услугу", "client_request_id": str(uuid4())},
    )
    assert task.status_code == 201, task.text
    detail = ticket_client.get(f"/api/v1/tasks/{task.json()['id']}").json()
    assert detail["subscriber_name"] == "Synthetic Subscriber"
    assert detail["phone"] == CONTACT["caller_phone"]
    assert detail["account_no"] == "5113"


def test_internal_task_revoked_permission_and_csrf_are_enforced(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    engineer = seed_user(ticket_engine, "task_revocation_engineer", Role.ENGINEER)
    assignee = seed_user(ticket_engine, "task_revocation_operator", Role.OPERATOR)
    grant_ticket_permission(ticket_engine, assignee, Permission.TASK_EXECUTE)
    login(ticket_client, engineer.login)
    ticket = make_manual_ticket(ticket_client)
    payload = {
        "ticket_id": ticket["id"],
        "assignee_id": str(assignee.id),
        "title": "Проверить связь",
        "description": "Позвонить и сообщить результат",
        "client_request_id": str(uuid4()),
    }
    assert ticket_client.post("/api/v1/tasks", json=payload).status_code == 403
    response = ticket_client.post("/api/v1/tasks", headers=csrf(ticket_client), json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    login(ticket_client, assignee.login)
    assert ticket_client.get(f"/api/v1/tasks/{task_id}").status_code == 200
    with Session(ticket_engine) as db:
        grant = db.get(UserPermission, (assignee.id, Permission.TASK_EXECUTE))
        assert grant is not None
        db.delete(grant)
        db.commit()
    assert ticket_client.get(f"/api/v1/tasks/{task_id}").status_code == 404
    assert ticket_client.get("/api/v1/tasks/notifications").status_code == 403
    assert (
        ticket_client.post(
            f"/api/v1/tasks/{task_id}/events",
            headers={**csrf(ticket_client), "If-Match": '"1"'},
            json={"client_request_id": str(uuid4()), "kind": "COMPLETED", "body": "Ответ"},
        ).status_code
        == 403
    )


def test_internal_task_records_follow_confirmed_ticket_purge(
    ticket_engine: Engine,
    ticket_client: TestClient,
) -> None:
    admin = seed_user(ticket_engine, "task_purge_admin", Role.ADMIN)
    login(ticket_client, admin.login)
    ticket = make_manual_ticket(ticket_client)
    task = ticket_client.post(
        "/api/v1/tasks",
        headers=csrf(ticket_client),
        json={
            "ticket_id": ticket["id"],
            "assignee_id": str(admin.id),
            "title": "Проверить",
            "description": "Тестовое поручение",
            "client_request_id": str(uuid4()),
        },
    )
    assert task.status_code == 201, task.text
    path = f"/api/v1/tickets/{ticket['id']}"
    version = ticket_client.get(path).json()["version"]
    deleted = ticket_client.delete(
        path, headers={**csrf(ticket_client), "If-Match": f'"{version}"'}
    )
    assert deleted.status_code == 200, deleted.text
    assert ticket_client.get(f"/api/v1/tasks/{task.json()['id']}").status_code == 404
    purged = ticket_client.request(
        "DELETE", f"{path}/purge",
        headers={**csrf(ticket_client), "If-Match": f'"{version + 1}"'},
        json={"confirm_number": ticket["number"]},
    )
    assert purged.status_code == 204, purged.text
    with Session(ticket_engine) as db:
        assert db.scalar(select(func.count()).select_from(InternalTask)) == 0
        assert db.scalar(select(func.count()).select_from(InternalTaskEvent)) == 0
        assert db.scalar(select(func.count()).select_from(InternalTaskNotification)) == 0
