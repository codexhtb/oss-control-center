"""Shared staff chat: persistence, access, cursors, idempotency and reconnect."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.chat import _poll
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.modules.chat.models import ChatMessage
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User
from app.modules.identity.roles import Role

ORIGIN = "https://oss.example.test"
PASSWORD = "A-long-test-password-42!"


@pytest.fixture
def chat_engine(isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = create_engine(isolated_postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def chat_clients(chat_engine: Engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(chat_engine, expire_on_commit=False)

    def test_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = test_session
    with Session(chat_engine) as db:
        for name, role, active in [
            ("operator", Role.OPERATOR, True),
            ("engineer", Role.ENGINEER, True),
            ("admin", Role.ADMIN, True),
            ("inactive", Role.OPERATOR, False),
        ]:
            db.add(
                User(
                    login=name,
                    display_name=name.title(),
                    role=role,
                    password_hash=hash_password(PASSWORD),
                    is_active=active,
                )
            )
        db.commit()
    try:
        with (
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as operator,
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as engineer,
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as admin,
        ):
            for client, login in [(operator, "operator"), (engineer, "engineer"), (admin, "admin")]:
                assert (
                    client.post(
                        "/api/v1/auth/login", json={"login": login, "password": PASSWORD}
                    ).status_code
                    == 200
                )
            yield operator, engineer, admin
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def send(client: TestClient, body: str, client_id: str | None = None):
    return client.post(
        "/api/v1/chat/messages",
        json={"body": body, "client_id": client_id or str(uuid4())},
        headers={"X-CSRF-Token": client.cookies["oss_csrf"]},
    )


def read(client: TestClient, through_id: int):
    return client.post(
        "/api/v1/chat/read",
        json={"through_id": through_id},
        headers={"X-CSRF-Token": client.cookies["oss_csrf"]},
    )


def test_chat_send_history_unread_read_and_reconnect(chat_engine: Engine, chat_clients):
    operator, engineer, admin = chat_clients
    assert operator.get("/api/v1/chat/summary").json()["unread_count"] == 0
    first = send(operator, "  Первая строка\nВторая строка  ")
    assert first.status_code == 201
    item = first.json()
    assert item["body"] == "Первая строка\nВторая строка"
    assert item["sender_name"] == "Operator"
    assert operator.get("/api/v1/chat/summary").json()["unread_count"] == 0
    assert engineer.get("/api/v1/chat/summary").json()["unread_count"] == 1
    assert admin.get("/api/v1/chat/summary").json()["unread_count"] == 1
    assert engineer.get("/api/v1/chat/messages").json()["items"] == [item]
    assert read(engineer, item["id"]).json()["unread_count"] == 0
    assert read(engineer, 0).json()["last_read_id"] == item["id"]
    assert admin.get("/api/v1/chat/summary").json()["unread_count"] == 1
    second = send(admin, "Ответ").json()
    assert [m["id"] for m in operator.get("/api/v1/chat/messages?limit=1").json()["items"]] == [
        second["id"]
    ]
    older = operator.get(f"/api/v1/chat/messages?before={second['id']}").json()
    assert [m["id"] for m in older["items"]] == [item["id"]]
    # A reconnect resumes strictly after the last delivered event.
    assert [m["id"] for m in _poll(chat_engine, operator.cookies["oss_session"], item["id"])] == [
        second["id"]
    ]
    with Session(chat_engine) as db:
        assert db.scalar(select(ChatMessage).where(ChatMessage.id == item["id"])) is not None


def test_chat_idempotency_validation_and_inactive_account(chat_engine: Engine, chat_clients):
    operator, engineer, _ = chat_clients
    client_id = str(uuid4())
    first = send(operator, "Same", client_id)
    assert first.status_code == 201
    repeated = send(operator, "Same", client_id)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert send(operator, "Different", client_id).status_code == 409
    assert engineer.get("/api/v1/chat/summary").json()["unread_count"] == 1
    assert send(operator, " ").status_code == 422
    assert send(operator, "x" * 2001).status_code == 422
    assert (
        operator.post(
            "/api/v1/chat/messages",
            json={"body": "x", "client_id": str(uuid4()), "sender_id": str(uuid4())},
            headers={"X-CSRF-Token": operator.cookies["oss_csrf"]},
        ).status_code
        == 422
    )
    assert (
        operator.post(
            "/api/v1/chat/messages", json={"body": "x", "client_id": str(uuid4())}
        ).status_code
        == 403
    )
    assert read(operator, 999999).status_code == 422
    with Session(chat_engine) as db:
        db.execute(text("UPDATE app_users SET is_active = false WHERE login = 'operator'"))
        db.commit()
    assert operator.get("/api/v1/chat/messages").status_code == 401
    assert send(operator, "Denied").status_code == 401
    assert _poll(chat_engine, operator.cookies["oss_session"], 0) is None


def test_chat_concurrent_senders_and_rate_limit(chat_clients):
    operator, engineer, _ = chat_clients
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: send(pair[0], pair[1]),
                [(operator, "operator message"), (engineer, "engineer message")],
            )
        )
    assert [result.status_code for result in results] == [201, 201]
    assert results[0].json()["id"] != results[1].json()["id"]
    for index in range(11):
        assert send(operator, f"Message {index}").status_code == 201
    assert send(operator, "Rate limit").status_code == 429
