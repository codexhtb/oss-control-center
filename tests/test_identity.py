"""Identity API and database invariants against an isolated Compose PostgreSQL database."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.modules.identity import bootstrap_admin
from app.modules.identity.crypto import hash_password, hash_token
from app.modules.identity.models import AuthSession, IdentityAuditEvent, User, UserPermission
from app.modules.identity.roles import Permission, Role
from app.modules.identity.service import deactivate_user, utc_now

ORIGIN = "https://oss.example.test"
PASSWORD = "A-long-test-password-42!"
AUTH_KEY = "test-only-key-with-at-least-32-bytes"


@pytest.fixture
def identity_engine(
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
def identity_client(
    identity_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", AUTH_KEY)
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(identity_engine, autoflush=False, expire_on_commit=False)

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


def seed_user(engine: Engine, login: str, role: Role, *, active: bool = True) -> User:
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            login=login,
            display_name=login.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user


def login(client: TestClient, login_name: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"login": login_name, "password": password})


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["oss_csrf"]}


def test_upgrade_from_existing_foundation(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0001_foundation")
    engine = create_engine(isolated_postgres_url)
    try:
        command.upgrade(config, "head")
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == expected_head
            assert {
                "app_users",
                "auth_sessions",
                "auth_login_buckets",
                "identity_audit_events",
            } <= set(inspect(connection).get_table_names())
            assert connection.execute(text("SELECT count(*) FROM app_users")).scalar_one() == 0
            triggers = (
                connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'app_users'::regclass")
                )
                .scalars()
                .all()
            )
            assert "ck_app_users_last_admin_update" in triggers
            assert "ck_app_users_last_admin_delete" in triggers
    finally:
        engine.dispose()


def test_login_wrong_password_inactive_user_and_public_response(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    user = seed_user(identity_engine, "operator", Role.OPERATOR)
    seed_user(identity_engine, "inactive", Role.ENGINEER, active=False)
    assert login(identity_client, "operator", "wrong").status_code == 401
    assert login(identity_client, "inactive").status_code == 401
    response = login(identity_client, " OPERATOR ")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user.id)
    assert "password_hash" not in response.text
    assert "token" not in response.text
    cookie_headers = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookie_headers if value.startswith("oss_session="))
    csrf_cookie = next(value for value in cookie_headers if value.startswith("oss_csrf="))
    assert "httponly" in session_cookie.lower()
    assert "secure" in session_cookie.lower()
    assert "samesite=strict" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()
    assert "secure" in csrf_cookie.lower()
    assert "samesite=strict" in csrf_cookie.lower()
    assert "oss_session" in identity_client.cookies
    assert identity_client.get("/api/v1/auth/me").json()["user"]["role"] == "operator"


def test_expired_and_revoked_sessions(identity_engine: Engine, identity_client: TestClient) -> None:
    seed_user(identity_engine, "engineer", Role.ENGINEER)
    assert login(identity_client, "engineer").status_code == 200
    token = identity_client.cookies["oss_session"]
    with Session(identity_engine) as db:
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == hash_token(token))
            .values(
                created_at=utc_now() - timedelta(hours=2), expires_at=utc_now() - timedelta(hours=1)
            )
        )
        db.commit()
    assert identity_client.get("/api/v1/auth/me").status_code == 401
    assert login(identity_client, "engineer").status_code == 200
    token = identity_client.cookies["oss_session"]
    with Session(identity_engine) as db:
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == hash_token(token))
            .values(revoked_at=utc_now())
        )
        db.commit()
    assert identity_client.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_session(identity_engine: Engine, identity_client: TestClient) -> None:
    seed_user(identity_engine, "engineer", Role.ENGINEER)
    assert login(identity_client, "engineer").status_code == 200
    token = identity_client.cookies["oss_session"]
    response = identity_client.post("/api/v1/auth/logout", headers=csrf(identity_client))
    assert response.status_code == 200
    assert identity_client.get("/api/v1/auth/me").status_code == 401
    with Session(identity_engine) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        assert session is not None and session.revoked_at is not None
        assert session.token_hash != token


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (
            Role.OPERATOR,
            {Permission.SUBSCRIBER_READ, Permission.TICKET_CREATE, Permission.TICKET_READ},
        ),
        (
            Role.ENGINEER,
            {
                Permission.SUBSCRIBER_READ,
                Permission.TICKET_CREATE,
                Permission.TICKET_CLOSE,
                Permission.TICKET_READ,
            },
        ),
        (Role.ADMIN, {Permission.IDENTITY_MANAGE}),
    ],
)
def test_permissions_by_role(
    identity_engine: Engine, identity_client: TestClient, role: Role, expected: set[Permission]
) -> None:
    seed_user(identity_engine, "account", role)
    assert login(identity_client, "account").status_code == 200
    permissions = set(identity_client.get("/api/v1/auth/permissions").json()["permissions"])
    assert expected <= permissions
    assert (Permission.IDENTITY_MANAGE in permissions) == (role == Role.ADMIN)
    assert (Permission.TICKET_CLOSE in permissions) == (role != Role.OPERATOR)
    assert Permission.TICKET_CREATE in permissions
    assert Permission.SUBSCRIBER_READ in permissions


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.ENGINEER])
def test_no_horizontal_or_vertical_privilege_escalation(
    identity_engine: Engine, identity_client: TestClient, role: Role
) -> None:
    actor = seed_user(identity_engine, "actor", role)
    other = seed_user(identity_engine, "other", Role.OPERATOR)
    assert login(identity_client, "actor").status_code == 200
    headers = csrf(identity_client)
    assert (
        identity_client.patch(
            f"/api/v1/users/{actor.id}/role", json={"role": "admin"}, headers=headers
        ).status_code
        == 403
    )
    assert (
        identity_client.patch(
            f"/api/v1/users/{other.id}/role", json={"role": "admin"}, headers=headers
        ).status_code
        == 403
    )
    assert (
        identity_client.post(
            "/api/v1/users",
            json={"login": "newuser", "display_name": "New", "role": "admin"},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        identity_client.post(f"/api/v1/users/{other.id}/deactivate", headers=headers).status_code
        == 403
    )
    assert (
        identity_client.put(
            f"/api/v1/users/{other.id}/password",
            json={"new_password": "1", "must_change_password": False},
            headers=headers,
        ).status_code
        == 403
    )


def test_no_self_registration_and_admin_user_management(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    seed_user(identity_engine, "admin", Role.ADMIN)
    assert identity_client.post("/api/v1/auth/register", json={}).status_code == 404
    assert identity_client.post("/api/v1/users", json={}).status_code == 401
    assert login(identity_client, "admin").status_code == 200
    headers = csrf(identity_client)
    created = identity_client.post(
        "/api/v1/users",
        json={
            "login": " New.User ",
            "display_name": "New User",
            "role": "operator",
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["login"] == "new.user"
    assert "password_hash" not in created.text
    user_id = UUID(created.json()["id"])
    assert (
        identity_client.post(
            "/api/v1/users",
            json={
                "login": "NEW.USER",
                "display_name": "Duplicate",
                "role": "operator",
            },
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        identity_client.patch(
            f"/api/v1/users/{user_id}/role", json={"role": "engineer"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        identity_client.post(f"/api/v1/users/{user_id}/deactivate", headers=headers).status_code
        == 200
    )
    assert login(identity_client, "new.user").status_code == 401
    with Session(identity_engine) as db:
        actions = set(db.scalars(select(IdentityAuditEvent.action)).all())
        assert {"user_created", "role_changed", "user_deactivated", "sessions_revoked"} <= actions


def test_csrf_and_origin_checks(identity_engine: Engine, identity_client: TestClient) -> None:
    seed_user(identity_engine, "admin", Role.ADMIN)
    assert login(identity_client, "admin").status_code == 200
    payload = {
        "login": "someone",
        "display_name": "Someone",
        "role": "operator",
    }
    assert identity_client.post("/api/v1/users", json=payload).status_code == 403
    assert (
        identity_client.post(
            "/api/v1/users", json=payload, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        identity_client.post(
            "/api/v1/users",
            json=payload,
            headers={**csrf(identity_client), "Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        identity_client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": PASSWORD},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        identity_client.post(
            "/api/v1/users",
            json=payload,
            headers={**csrf(identity_client), "Host": "evil.test"},
        ).status_code
        == 403
    )


def test_rate_limit_persists_across_client_recreation(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    seed_user(identity_engine, "operator", Role.OPERATOR)
    for index in range(5):
        response = identity_client.post(
            "/api/v1/auth/login",
            json={"login": "operator", "password": "wrong"},
            headers={"X-Forwarded-For": f"203.0.113.{index + 1}"},
        )
        assert response.status_code == 401
    restarted_app = create_app()
    restarted_app.dependency_overrides[get_session] = identity_client.app.dependency_overrides[
        get_session
    ]
    with TestClient(restarted_app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as restarted_client:
        assert login(restarted_client, "operator").status_code == 429


def test_source_rate_limit_applies_across_different_logins(
    identity_client: TestClient,
) -> None:
    for index in range(20):
        assert login(identity_client, f"unknown{index}", "wrong").status_code == 401
    assert login(identity_client, "another", "wrong").status_code == 429


def test_deactivation_invalidates_all_sessions(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    seed_user(identity_engine, "admin", Role.ADMIN)
    target = seed_user(identity_engine, "operator", Role.OPERATOR)
    assert login(identity_client, "operator").status_code == 200
    token = identity_client.cookies["oss_session"]
    assert login(identity_client, "admin").status_code == 200
    assert (
        identity_client.post(
            f"/api/v1/users/{target.id}/deactivate", headers=csrf(identity_client)
        ).status_code
        == 200
    )
    with Session(identity_engine) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        assert session is not None and session.revoked_at is not None
    with TestClient(identity_client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as old_client:
        old_client.cookies.set("oss_session", token, domain="oss.example.test", path="/api/v1")
        assert old_client.get("/api/v1/auth/me").status_code == 401


def test_role_change_revokes_target_session(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    seed_user(identity_engine, "admin", Role.ADMIN)
    target = seed_user(identity_engine, "operator", Role.OPERATOR)
    assert login(identity_client, "operator").status_code == 200
    token = identity_client.cookies["oss_session"]
    assert login(identity_client, "admin").status_code == 200
    assert (
        identity_client.patch(
            f"/api/v1/users/{target.id}/role",
            json={"role": "engineer"},
            headers=csrf(identity_client),
        ).status_code
        == 200
    )
    with Session(identity_engine) as db:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        assert session is not None and session.revoked_at is not None
    with TestClient(identity_client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as old_client:
        old_client.cookies.set("oss_session", token, domain="oss.example.test", path="/api/v1")
        assert old_client.get("/api/v1/auth/me").status_code == 401


def test_last_admin_and_database_guard(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    admin = seed_user(identity_engine, "admin", Role.ADMIN)
    assert login(identity_client, "admin").status_code == 200
    assert (
        identity_client.post(
            f"/api/v1/users/{admin.id}/deactivate", headers=csrf(identity_client)
        ).status_code
        == 409
    )
    assert (
        identity_client.patch(
            f"/api/v1/users/{admin.id}/role",
            json={"role": "operator"},
            headers=csrf(identity_client),
        ).status_code
        == 409
    )
    with Session(identity_engine) as db, pytest.raises(IntegrityError):
        db.execute(update(User).where(User.id == admin.id).values(is_active=False))
        db.commit()
    with Session(identity_engine) as db:
        assert db.scalar(select(User.is_active).where(User.id == admin.id)) is True


def test_concurrent_admin_deactivation_preserves_one(identity_engine: Engine) -> None:
    first = seed_user(identity_engine, "adminone", Role.ADMIN)
    second = seed_user(identity_engine, "admintwo", Role.ADMIN)

    def deactivate(actor_id: UUID, target_id: UUID) -> int:
        from app.core.errors import ApiError

        with Session(identity_engine, expire_on_commit=False) as db:
            actor = db.get(User, actor_id)
            assert actor is not None
            try:
                deactivate_user(db, actor, target_id)
                return 200
            except ApiError as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda pair: deactivate(*pair), [(first.id, second.id), (second.id, first.id)]
            )
        )
    assert sorted(outcomes) == [200, 403]
    with Session(identity_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == Role.ADMIN, User.is_active.is_(True))
            )
            == 1
        )


def test_bootstrap_creates_only_first_admin_interactively(
    identity_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class InteractiveStream(StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(bootstrap_admin.sys, "stdin", InteractiveStream())
    monkeypatch.setattr(bootstrap_admin.sys, "stderr", InteractiveStream())
    monkeypatch.setattr(bootstrap_admin, "ensure_non_root_runtime", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "get_settings", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "get_engine", lambda: identity_engine)
    answers = iter(["firstadmin", "First Administrator", "secondadmin", "Second Administrator"])
    monkeypatch.setattr(bootstrap_admin, "input", lambda _prompt: next(answers), raising=False)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda _prompt: PASSWORD)
    assert bootstrap_admin.main() == 0
    assert bootstrap_admin.main() == 1
    with Session(identity_engine) as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].login == "firstadmin"
        assert users[0].role == Role.ADMIN
        assert users[0].password_hash.startswith("$argon2id$")


def test_audit_contains_no_password_or_token(
    identity_engine: Engine, identity_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    seed_user(identity_engine, "admin", Role.ADMIN)
    assert login(identity_client, "admin", "wrong").status_code == 401
    assert login(identity_client, "admin").status_code == 200
    token = identity_client.cookies["oss_session"]
    assert (
        identity_client.post("/api/v1/auth/logout", headers=csrf(identity_client)).status_code
        == 200
    )
    with Session(identity_engine) as db:
        events = db.scalars(select(IdentityAuditEvent)).all()
        assert {event.outcome for event in events} >= {"denied", "success"}
        assert all(event.login_digest != "admin" for event in events)
        assert all(event.source_digest != "testclient" for event in events)
    assert PASSWORD not in caplog.text
    assert token not in caplog.text


def test_personal_grants_temporary_password_and_live_session_revocation(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    admin = seed_user(identity_engine, "admin", Role.ADMIN)
    assert login(identity_client, admin.login).status_code == 200
    created = identity_client.post(
        "/api/v1/users",
        headers=csrf(identity_client),
        json={
            "login": "limited",
            "display_name": "Limited Worker",
            "role": "operator",
            "permissions": [
                Permission.TICKET_CREATE,
                Permission.TICKET_READ_OWN,
                Permission.TICKET_EDIT_OWN,
            ],
        },
    )
    assert created.status_code == 201, created.text
    temp = created.json()["temporary_password"]
    user_id = UUID(created.json()["id"])
    assert len(temp) >= 24 and created.json()["must_change_password"] is True
    with Session(identity_engine) as db:
        target = db.get(User, user_id)
        assert target.password_hash.startswith("$argon2id$")
        assert temp not in target.password_hash

    with TestClient(identity_client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as worker:
        assert login(worker, "limited", temp).status_code == 200
        assert worker.get("/api/v1/auth/me").json()["user"]["must_change_password"] is True
        blocked = worker.get("/api/v1/tickets")
        assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
        changed = worker.post(
            "/api/v1/auth/password",
            headers=csrf(worker),
            json={"current_password": temp, "new_password": "Distinct-test-password-876!"},
        )
        assert changed.status_code == 200, changed.text
        assert worker.get("/api/v1/auth/me").status_code == 401
        assert login(worker, "limited", "Distinct-test-password-876!").status_code == 200
        assert worker.get("/api/v1/auth/permissions").json()["permissions"] == sorted(
            [Permission.TICKET_CREATE, Permission.TICKET_READ_OWN, Permission.TICKET_EDIT_OWN]
        )
        revised = identity_client.put(
            f"/api/v1/users/{user_id}/permissions",
            headers=csrf(identity_client),
            json={"permissions": [Permission.TICKET_READ_OWN]},
        )
        assert revised.status_code == 200
        assert worker.get("/api/v1/auth/permissions").json()["permissions"] == [
            Permission.TICKET_READ_OWN
        ]
        assert worker.post("/api/v1/tickets", headers=csrf(worker), json={}).status_code == 403
        assert (
            identity_client.post(
                f"/api/v1/users/{user_id}/deactivate", headers=csrf(identity_client)
            ).status_code
            == 200
        )
        assert worker.get("/api/v1/auth/me").status_code == 401

    with Session(identity_engine) as db:
        events = db.scalars(
            select(IdentityAuditEvent).where(IdentityAuditEvent.target_user_id == user_id)
        ).all()
        assert {"user_created", "password_change", "permissions_changed", "user_deactivated"} <= {
            event.action for event in events
        }
        assert temp not in str([event.details for event in events])


def test_admin_sets_unrestricted_password_and_employee_changes_it(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    admin = seed_user(identity_engine, "admin", Role.ADMIN)
    assert login(identity_client, admin.login).status_code == 200
    headers = csrf(identity_client)
    created = identity_client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "login": "short-password-worker",
            "display_name": "Short Password Worker",
            "role": "operator",
            "password": "1",
            "must_change_password": False,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["temporary_password"] is None
    assert created.json()["must_change_password"] is False
    user_id = UUID(created.json()["id"])

    with TestClient(identity_client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as worker:
        assert login(worker, "short-password-worker", "1").status_code == 200
        changed = identity_client.put(
            f"/api/v1/users/{user_id}/password",
            headers=headers,
            json={"new_password": "2", "must_change_password": True},
        )
        assert changed.status_code == 200, changed.text
        assert changed.headers["cache-control"] == "no-store"
        assert worker.get("/api/v1/auth/me").status_code == 401
        assert login(worker, "short-password-worker", "2").status_code == 200
        assert worker.get("/api/v1/auth/me").json()["user"]["must_change_password"] is True
        own_change = worker.post(
            "/api/v1/auth/password",
            headers=csrf(worker),
            json={"current_password": "2", "new_password": "3"},
        )
        assert own_change.status_code == 200, own_change.text
        assert worker.get("/api/v1/auth/me").status_code == 401
        assert login(worker, "short-password-worker", "3").status_code == 200

    assert identity_client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "login": "empty-password-worker",
            "display_name": "Empty Password Worker",
            "role": "operator",
            "password": "",
        },
    ).status_code == 422
    with Session(identity_engine) as db:
        target = db.get(User, user_id)
        assert target.password_hash.startswith("$argon2id$")
        events = db.scalars(
            select(IdentityAuditEvent).where(IdentityAuditEvent.target_user_id == user_id)
        ).all()
        assert {event.action for event in events} >= {
            "user_created",
            "password_set",
            "password_change",
        }
        assert all(
            not ({"password", "new_password", "current_password"} & set(event.details or {}))
            for event in events
        )


def test_admin_edit_reset_reactivate_and_last_grant_guard(
    identity_engine: Engine, identity_client: TestClient
) -> None:
    admin = seed_user(identity_engine, "admin", Role.ADMIN)
    with Session(identity_engine) as db:
        current = db.get(User, admin.id)
        current.permissions_customized = True
        db.add(UserPermission(user_id=admin.id, permission=Permission.IDENTITY_MANAGE))
        db.add(UserPermission(user_id=admin.id, permission=Permission.TICKET_AUDIT_READ))
        db.commit()
    assert login(identity_client, admin.login).status_code == 200
    headers = csrf(identity_client)
    created = identity_client.post(
        "/api/v1/users",
        headers=headers,
        json={"login": "worker", "display_name": "Worker", "role": "engineer"},
    )
    assert created.status_code == 201
    user_id = UUID(created.json()["id"])
    edited = identity_client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"login": "new.worker", "display_name": "New Worker"},
    )
    assert edited.status_code == 200 and edited.json()["login"] == "new.worker"
    reset = identity_client.post(f"/api/v1/users/{user_id}/reset-password", headers=headers)
    assert reset.status_code == 200 and len(reset.json()["temporary_password"]) >= 24
    assert (
        identity_client.post(f"/api/v1/users/{user_id}/deactivate", headers=headers).status_code
        == 200
    )
    assert (
        identity_client.post(f"/api/v1/users/{user_id}/reactivate", headers=headers).status_code
        == 200
    )
    assert (
        identity_client.put(
            f"/api/v1/users/{admin.id}/permissions",
            headers=headers,
            json={"permissions": []},
        ).status_code
        == 409
    )
    with Session(identity_engine) as db:
        with pytest.raises(IntegrityError):
            db.query(UserPermission).filter(
                UserPermission.user_id == admin.id,
                UserPermission.permission == Permission.IDENTITY_MANAGE,
            ).delete()
            db.commit()
        db.rollback()
    audit_response = identity_client.get("/api/v1/users/audit")
    assert audit_response.status_code == 200, audit_response.json().get("code")
    assert any(row["action"] == "user_updated" for row in audit_response.json()["items"])


def test_0013_backfills_existing_users_without_changing_password_or_sessions(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0012_oss_diagnostics")
    engine = create_engine(isolated_postgres_url)
    user_id = UUID("00000000-0000-4000-8000-000000000123")
    session_id = UUID("00000000-0000-4000-8000-000000000124")
    old_hash = hash_password(PASSWORD)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO app_users (id, login, display_name, role, password_hash)
                     VALUES (:id, 'legacyadmin', 'Legacy Admin', 'admin', :hash)"""),
                {"id": user_id, "hash": old_hash},
            )
            connection.execute(
                text("""INSERT INTO auth_sessions
                     (id, user_id, token_hash, csrf_hash, expires_at)
                     VALUES (:id, :user_id, :token_hash, :csrf_hash, now() + interval '1 hour')"""),
                {
                    "id": session_id,
                    "user_id": user_id,
                    "token_hash": hash_token("synthetic-old-session"),
                    "csrf_hash": hash_token("synthetic-old-csrf"),
                },
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT password_hash FROM app_users WHERE id = :id"), {"id": user_id}
                ).scalar_one()
                == old_hash
            )
            assert (
                connection.execute(
                    text("SELECT permissions_customized FROM app_users WHERE id = :id"),
                    {"id": user_id},
                ).scalar_one()
                is True
            )
            assert (
                connection.execute(
                    text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
                    {"id": session_id},
                ).scalar_one()
                is None
            )
            grants = set(
                connection.execute(
                    text("SELECT permission FROM app_user_permissions WHERE user_id = :id"),
                    {"id": user_id},
                ).scalars()
            )
            assert {
                Permission.IDENTITY_MANAGE,
                Permission.TICKET_READ,
                Permission.TICKET_EDIT_ALL,
            } <= grants
    finally:
        engine.dispose()
