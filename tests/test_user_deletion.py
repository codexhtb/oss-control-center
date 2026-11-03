"""Administrator removal preserves historical records and revokes access."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.modules.chat.models import ChatMessage
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import AuthSession, IdentityAuditEvent, User, UserPermission
from app.modules.identity.roles import Permission, Role

ORIGIN = "https://oss.example.test"
PASSWORD = "A-long-test-password-42!"


@pytest.fixture
def deletion_env(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Engine, TestClient, TestClient, TestClient]]:
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = create_engine(isolated_postgres_url)
    with Session(engine) as db:
        for name, role in (
            ("admin", Role.ADMIN),
            ("worker", Role.OPERATOR),
            ("manager", Role.ENGINEER),
        ):
            user = User(
                login=name,
                display_name=name.title(),
                role=role,
                password_hash=hash_password(PASSWORD),
            )
            db.add(user)
            db.flush()
            if name == "manager":
                user.permissions_customized = True
                db.add_all(
                    (
                        UserPermission(user_id=user.id, permission=Permission.IDENTITY_MANAGE),
                        UserPermission(user_id=user.id, permission=Permission.USERS_READ),
                    )
                )
        db.commit()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(engine, expire_on_commit=False)

    def test_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = test_session
    try:
        with (
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as admin,
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as worker,
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as manager,
        ):
            for client, name in ((admin, "admin"), (worker, "worker"), (manager, "manager")):
                assert (
                    client.post(
                        "/api/v1/auth/login", json={"login": name, "password": PASSWORD}
                    ).status_code
                    == 200
                )
            yield engine, admin, worker, manager
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["oss_csrf"]}


def test_admin_deletion_revokes_access_and_preserves_history(deletion_env):
    engine, admin, worker, manager = deletion_env
    with Session(engine) as db:
        target = db.scalar(select(User).where(User.login == "worker"))
        assert target is not None
        target_id = target.id
    sent = worker.post(
        "/api/v1/chat/messages",
        json={"body": "Historical message", "client_id": "00000000-0000-4000-8000-000000000099"},
        headers=csrf(worker),
    )
    assert sent.status_code == 201
    path = f"/api/v1/users/{target_id}"
    assert worker.delete(path, headers=csrf(worker)).status_code == 403
    assert manager.delete(path, headers=csrf(manager)).status_code == 403
    assert admin.delete(path).status_code == 403
    assert admin.delete(path, headers=csrf(admin)).status_code == 204
    assert admin.delete(path, headers=csrf(admin)).status_code == 204
    assert admin.get(path).status_code == 404
    assert all(item["id"] != str(target_id) for item in admin.get("/api/v1/users").json()["items"])
    assert admin.post(f"{path}/reactivate", headers=csrf(admin)).status_code == 404
    assert worker.get("/api/v1/auth/me").status_code == 401
    assert (
        worker.post(
            "/api/v1/auth/login", json={"login": "worker", "password": PASSWORD}
        ).status_code
        == 401
    )
    with Session(engine) as db:
        target = db.get(User, target_id)
        assert target is not None and target.deleted_at is not None and not target.is_active
        assert db.scalar(select(ChatMessage).where(ChatMessage.sender_id == target_id)) is not None
        assert (
            db.scalar(
                select(AuthSession).where(
                    AuthSession.user_id == target_id, AuthSession.revoked_at.is_(None)
                )
            )
            is None
        )
        assert (
            db.scalar(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.action == "user_deleted",
                    IdentityAuditEvent.target_user_id == target_id,
                )
            )
            is not None
        )
        assert (
            db.scalar(
                text("SELECT count(*) FROM identity_audit_events WHERE action='user_deleted'")
            )
            == 1
        )
        with pytest.raises(IntegrityError):
            db.execute(update(User).where(User.id == target_id).values(is_active=True))
            db.flush()


def test_admin_cannot_delete_self_or_last_administrator(deletion_env):
    engine, admin, _, _ = deletion_env
    with Session(engine) as db:
        own_id = db.scalar(select(User.id).where(User.login == "admin"))
        assert own_id is not None
    assert admin.delete(f"/api/v1/users/{own_id}", headers=csrf(admin)).status_code == 409
    with Session(engine) as db:
        assert db.get(User, own_id).deleted_at is None


def test_deleted_login_can_be_reused_for_a_new_employee(deletion_env):
    engine, admin, worker, _ = deletion_env
    with Session(engine) as db:
        previous_id = db.scalar(select(User.id).where(User.login == "worker"))
    assert previous_id is not None
    assert admin.delete(f"/api/v1/users/{previous_id}", headers=csrf(admin)).status_code == 204

    replacement_password = "Replacement-test-password-43!"
    created = admin.post(
        "/api/v1/users",
        headers=csrf(admin),
        json={
            "login": "worker",
            "display_name": "Replacement Worker",
            "role": "operator",
            "password": replacement_password,
        },
    )
    assert created.status_code == 201
    replacement_id = created.json()["id"]
    assert replacement_id != str(previous_id)
    assert (
        worker.post(
            "/api/v1/auth/login", json={"login": "worker", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        worker.post(
            "/api/v1/auth/login",
            json={"login": "worker", "password": replacement_password},
        ).status_code
        == 200
    )
    assert worker.get("/api/v1/auth/me").json()["user"]["id"] == replacement_id
    with Session(engine) as db:
        assert db.get(User, previous_id).deleted_at is not None
        assert db.scalar(
            select(User.id).where(User.login == "worker", User.deleted_at.is_(None))
        ) == UUID(replacement_id)


def test_deleted_login_can_be_assigned_to_another_employee(deletion_env):
    engine, admin, _, manager = deletion_env
    with Session(engine) as db:
        deleted_id = db.scalar(select(User.id).where(User.login == "worker"))
        manager_id = db.scalar(select(User.id).where(User.login == "manager"))
    assert deleted_id is not None and manager_id is not None
    assert admin.delete(f"/api/v1/users/{deleted_id}", headers=csrf(admin)).status_code == 204
    path = f"/api/v1/users/{manager_id}"
    updated = admin.patch(path, headers=csrf(admin), json={"login": "worker"})
    assert updated.status_code == 200
    assert updated.json()["login"] == "worker"
    unchanged = admin.patch(path, headers=csrf(admin), json={"login": "worker"})
    assert unchanged.status_code == 409 and unchanged.json()["code"] == "no_change"
    occupied = admin.patch(path, headers=csrf(admin), json={"login": "admin"})
    assert occupied.status_code == 409 and occupied.json()["code"] == "user_conflict"
    assert (
        manager.post(
            "/api/v1/auth/login", json={"login": "worker", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert manager.get("/api/v1/auth/me").json()["user"]["id"] == str(manager_id)
