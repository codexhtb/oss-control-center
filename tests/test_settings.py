"""Managed Service Desk catalogs in disposable PostgreSQL databases."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import IdentityAuditEvent, User, UserPermission
from app.modules.identity.roles import Permission, Role
from app.modules.tickets.models import TicketResolution

ORIGIN = "https://oss.example.test"
PASSWORD = "Test-password-with-sufficient-length-42!"


@pytest.fixture
def settings_engine(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
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
def settings_client(
    settings_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BILLING_PROVIDER", "MOCK")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(settings_engine, autoflush=False, expire_on_commit=False)

    def session() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    try:
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def user(
    engine: Engine, login_name: str, role: Role, *, grants: tuple[Permission, ...] = ()
) -> User:
    with Session(engine, expire_on_commit=False) as db:
        item = User(
            login=login_name,
            display_name=login_name.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=True,
            permissions_customized=bool(grants),
        )
        db.add(item)
        db.flush()
        db.add_all(UserPermission(user_id=item.id, permission=permission) for permission in grants)
        db.commit()
        return item


def login(client: TestClient, name: str) -> None:
    result = client.post("/api/v1/auth/login", json={"login": name, "password": PASSWORD})
    assert result.status_code == 200, result.text


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["oss_csrf"]}


def test_template_crud_rbac_csrf_origin_conflict_and_audit(
    settings_engine: Engine,
    settings_client: TestClient,
) -> None:
    user(settings_engine, "settings_admin", Role.ADMIN, grants=(Permission.SETTINGS_MANAGE,))
    user(settings_engine, "settings_operator", Role.OPERATOR)
    user(settings_engine, "settings_other", Role.ADMIN)
    path = "/api/v1/settings/templates"
    payload = {"name": "Линия", "body": "Проверена линия", "purpose": "comment", "sort_order": 10}

    login(settings_client, "settings_admin")
    assert settings_client.post(path, json=payload).status_code == 403
    assert (
        settings_client.post(
            path, headers={**csrf(settings_client), "Origin": "https://bad.example"}, json=payload
        ).status_code
        == 403
    )
    created = settings_client.post(path, headers=csrf(settings_client), json=payload)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["version"] == 1
    assert len(settings_client.get(f"{path}?include_inactive=true").json()) == 1
    target = f"{path}/{item['id']}"
    assert (
        settings_client.patch(
            target, headers=csrf(settings_client), json={"body": "Новое"}
        ).status_code
        == 428
    )
    updated = settings_client.patch(
        target,
        headers={**csrf(settings_client), "If-Match": '"1"'},
        json={"body": "Новое", "is_active": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert (
        settings_client.patch(
            target,
            headers={**csrf(settings_client), "If-Match": '"1"'},
            json={"name": "Устаревшее"},
        ).status_code
        == 409
    )
    assert settings_client.get(path).json() == []
    with Session(settings_engine) as db:
        events = db.scalars(
            select(IdentityAuditEvent).where(
                IdentityAuditEvent.action.in_(("text_template_created", "text_template_updated"))
            )
        ).all()
        assert len(events) == 2
        assert events[-1].details["before"]["body"] == "Проверена линия"
        assert events[-1].details["after"]["body"] == "Новое"

    login(settings_client, "settings_operator")
    assert settings_client.get(f"{path}?purpose=decision").status_code == 403
    assert settings_client.get(f"{path}?purpose=comment").json() == []
    assert settings_client.get(f"{path}?include_inactive=true&purpose=comment").status_code == 403
    assert (
        settings_client.post(path, headers=csrf(settings_client), json=payload).status_code == 403
    )
    login(settings_client, "settings_other")
    assert (
        settings_client.post(path, headers=csrf(settings_client), json=payload).status_code == 403
    )


def test_template_text_is_literal_and_only_action_grants_allow_read(
    settings_engine: Engine,
    settings_client: TestClient,
) -> None:
    user(settings_engine, "literal_admin", Role.ADMIN, grants=(Permission.SETTINGS_MANAGE,))
    user(settings_engine, "literal_engineer", Role.ENGINEER)
    login(settings_client, "literal_admin")
    literal = "<script>window.unwanted=true</script>\n${do_not_interpolate}"
    result = settings_client.post(
        "/api/v1/settings/templates",
        headers=csrf(settings_client),
        json={"name": "Буквальный текст", "body": literal, "purpose": "decision", "sort_order": 0},
    )
    assert result.status_code == 201, result.text
    assert result.json()["body"] == literal
    assert (
        settings_client.post(
            "/api/v1/settings/templates",
            headers=csrf(settings_client),
            json={"name": "Пустой", "body": "   ", "purpose": "comment"},
        ).status_code
        == 422
    )
    login(settings_client, "literal_engineer")
    visible = settings_client.get("/api/v1/settings/templates?purpose=decision")
    assert visible.status_code == 200
    assert visible.json()[0]["body"] == literal
    assert settings_client.get("/api/v1/settings/templates?purpose=callback").status_code == 403


def test_reason_label_is_snapshot_and_disabled_reason_cannot_be_reused(
    settings_engine: Engine,
    settings_client: TestClient,
) -> None:
    user(settings_engine, "reason_admin", Role.ADMIN, grants=(Permission.SETTINGS_MANAGE,))
    user(settings_engine, "reason_engineer", Role.ENGINEER)
    user(settings_engine, "reason_operator", Role.OPERATOR)
    base = "/api/v1/settings/reasons/NETWORK_FAULT"
    login(settings_client, "reason_admin")
    changed = settings_client.patch(
        base,
        headers={**csrf(settings_client), "If-Match": '"1"'},
        json={"name": "Сетевой узел", "sort_order": 5},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["code"] == "NETWORK_FAULT"
    assert (
        settings_client.patch(
            base,
            headers={**csrf(settings_client), "If-Match": '"1"'},
            json={"name": "Старая версия"},
        ).status_code
        == 409
    )
    assert (
        settings_client.patch(
            "/api/v1/settings/reasons/NOT_ESTABLISHED",
            headers={**csrf(settings_client), "If-Match": '"1"'},
            json={"is_active": False},
        ).status_code
        == 409
    )

    login(settings_client, "reason_engineer")
    assert settings_client.get("/api/v1/settings/reasons").status_code == 200
    created = settings_client.post(
        "/api/v1/tickets",
        headers=csrf(settings_client),
        json={
            "ticket_type": "MANUAL",
            "subject": "Тестовая заявка",
            "caller_full_name": "Тестовый сотрудник",
            "caller_phone": "+0 000 000 00 00",
            "city": "Тестовый город",
            "client_request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    ticket = created.json()
    claimed = settings_client.post(
        f"/api/v1/tickets/{ticket['id']}/claim",
        headers={**csrf(settings_client), "If-Match": f'"{ticket["version"]}"'},
    )
    assert claimed.status_code == 200, claimed.text
    solved = settings_client.post(
        f"/api/v1/tickets/{ticket['id']}/resolve",
        headers={**csrf(settings_client), "If-Match": f'"{claimed.json()["version"]}"'},
        json={"decision_text": "Исправлено", "reason": "NETWORK_FAULT"},
    )
    assert solved.status_code == 200, solved.text
    decision_id = solved.json()["resolved_decision_id"]
    detail = settings_client.get(f"/api/v1/tickets/{ticket['id']}/decisions/{decision_id}")
    assert detail.json()["reason"] == "NETWORK_FAULT"
    assert detail.json()["reason_name"] == "Сетевой узел"

    login(settings_client, "reason_admin")
    renamed = settings_client.patch(
        base,
        headers={**csrf(settings_client), "If-Match": '"2"'},
        json={"name": "Новая формулировка", "is_active": False},
    )
    assert renamed.status_code == 200, renamed.text
    login(settings_client, "reason_engineer")
    old = settings_client.get(f"/api/v1/tickets/{ticket['id']}/decisions/{decision_id}")
    assert old.json()["reason_name"] == "Сетевой узел"
    refused = settings_client.post(
        f"/api/v1/tickets/{ticket['id']}/decisions",
        headers=csrf(settings_client),
        json={"decision_text": "Повтор", "reason": "NETWORK_FAULT"},
    )
    assert refused.status_code == 409
    assert settings_client.get(f"/api/v1/tickets/{ticket['id']}/decisions").json()["total"] == 1
    assert "NETWORK_FAULT" not in {
        row["code"] for row in settings_client.get("/api/v1/settings/reasons").json()
    }
    with Session(settings_engine) as db:
        assert db.get(TicketResolution, decision_id).reason_name == "Сетевой узел"
    login(settings_client, "reason_operator")
    assert settings_client.get("/api/v1/settings/reasons").status_code == 403
    assert (
        settings_client.patch(
            base, headers={**csrf(settings_client), "If-Match": '"3"'}, json={"name": "Нельзя"}
        ).status_code
        == 403
    )
