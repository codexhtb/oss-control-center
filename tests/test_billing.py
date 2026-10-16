"""Synthetic BillingProvider contract and PostgreSQL Subscriber 360 flow."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.billing import get_billing_provider
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.main import create_app
from app.modules.billing.mock import MockBillingProvider
from app.modules.billing.netup.dto import (
    NetupAccount,
    NetupGroup,
    NetupGroupRow,
    NetupService,
    NetupSubscriber,
)
from app.modules.billing.netup.provider import NetUPBillingProvider
from app.modules.billing.provider import BillingAccount, BillingUnavailable, FinancialAccount
from app.modules.billing.quality import DataQuality, FieldOrigin
from app.modules.billing.service import create_lookup
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role
from app.modules.tickets.models import (
    ConnectionSnapshot,
    SubscriberSnapshot,
    Ticket,
    TicketAudit,
    TicketEvent,
)
from app.modules.tickets.schemas import CreateTicket
from app.modules.tickets.service import create_ticket, ticket_detail

ORIGIN = "https://oss.example.test"
PASSWORD = "Test-password-with-sufficient-length-42!"


@pytest.fixture
def billing_engine(isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
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
def billing_client(billing_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BILLING_PROVIDER", "MOCK")
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("AUTH_ALLOW_HTTP_LOOPBACK", "false")
    monkeypatch.setenv("AUTH_HASH_KEY", "test-only-key-with-at-least-32-bytes")
    get_settings.cache_clear()
    app = create_app()
    factory = sessionmaker(billing_engine, autoflush=False, expire_on_commit=False)

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


def login(client: TestClient, name: str) -> None:
    response = client.post("/api/v1/auth/login", json={"login": name, "password": PASSWORD})
    assert response.status_code == 200, response.text


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["oss_csrf"]}


def lookup(client: TestClient, account: str):
    return client.post(
        "/api/v1/subscriber-lookups", headers=csrf(client), json={"account_no": account}
    )


def ticket_payload(card: dict, *, request_id: UUID | None = None) -> dict:
    return {
        "lookup_id": card["lookup_id"],
        "connection_snapshot_id": card["connections"][0]["connection_snapshot_id"],
        "selected_service_id": card["services"][0]["service_id"],
        "description": "Synthetic access issue",
        "client_request_id": str(request_id or uuid4()),
    }


def test_engineer_can_lookup_and_create_linked_ticket_via_existing_api(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    engineer = seed_user(billing_engine, "linked_engineer", Role.ENGINEER)
    login(billing_client, engineer.login)
    card_response = lookup(billing_client, "MOCK-ETH-001")
    assert card_response.status_code == 200
    card = card_response.json()
    assert billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}").status_code == 200
    created = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
    )
    assert created.status_code == 201, created.text
    with Session(billing_engine) as db:
        ticket = db.get(Ticket, UUID(created.json()["id"]))
        assert ticket is not None
        assert ticket.created_by == engineer.id
        assert ticket.ticket_type == "SUBSCRIBER_LINKED"


def test_finance_permission_and_subscriber_history_across_lookups(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    class SyntheticFinanceProvider(MockBillingProvider):
        def lookup_by_account(self, account_no: str):
            card = super().lookup_by_account(account_no)
            assert card is not None
            meta = FieldOrigin(
                source="MOCK", observed_at=card.fetched_at, quality=DataQuality.KNOWN
            )
            card.accounts = [BillingAccount(account_id=account_no, is_primary=True)]
            card.financial_accounts = [
                FinancialAccount(
                    account_id=account_no,
                    balance="12.5",
                    credit="0",
                    account_blocked=True,
                    field_meta={"balance": meta, "credit": meta, "account_blocked": meta},
                )
            ]
            return card

    billing_client.app.dependency_overrides[get_billing_provider] = SyntheticFinanceProvider
    seed_user(billing_engine, "finance_operator", Role.OPERATOR)
    seed_user(billing_engine, "finance_engineer", Role.ENGINEER)
    seed_user(billing_engine, "finance_admin", Role.ADMIN)

    login(billing_client, "finance_operator")
    operator_card = lookup(billing_client, "MOCK-ETH-001").json()
    assert operator_card["financial_accounts"][0]["balance"] == "12.5"
    assert operator_card["technical_state"] == "UNKNOWN"
    response = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(operator_card)
    )
    assert response.status_code == 201
    history = billing_client.get(
        f"/api/v1/subscriber-lookups/{operator_card['lookup_id']}/tickets?page=1&size=10"
    )
    assert history.status_code == 200
    assert history.headers["Cache-Control"] == "no-store"
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["id"] == response.json()["id"]
    assert (
        billing_client.post("/api/v1/auth/logout", headers=csrf(billing_client)).status_code == 200
    )

    login(billing_client, "finance_engineer")
    engineer_card = lookup(billing_client, "MOCK-ETH-001").json()
    assert engineer_card["financial_accounts"] == []
    assert (
        billing_client.get(
            f"/api/v1/subscriber-lookups/{engineer_card['lookup_id']}/tickets"
        ).json()["total"]
        == 1
    )
    assert (
        billing_client.get(f"/api/v1/subscriber-lookups/{operator_card['lookup_id']}").status_code
        == 404
    )
    assert (
        billing_client.post("/api/v1/auth/logout", headers=csrf(billing_client)).status_code == 200
    )

    login(billing_client, "finance_admin")
    assert lookup(billing_client, "MOCK-ETH-001").status_code == 200


def test_subscriber_history_includes_other_account_of_same_source(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    class TwoAccountProvider(MockBillingProvider):
        def lookup_by_account(self, account_no: str):
            card = super().lookup_by_account("MOCK-ETH-001")
            assert card is not None
            card.billing_account = account_no
            card.subscriber_id = "synthetic-two-account-subscriber"
            card.accounts = [
                BillingAccount(account_id="SYNTHETIC-ACCOUNT-A", is_primary=True),
                BillingAccount(account_id="SYNTHETIC-ACCOUNT-B", is_primary=False),
            ]
            card.services[0].account_id = account_no
            return card

    billing_client.app.dependency_overrides[get_billing_provider] = TwoAccountProvider
    seed_user(billing_engine, "multi_account_operator", Role.OPERATOR)
    login(billing_client, "multi_account_operator")
    first = lookup(billing_client, "SYNTHETIC-ACCOUNT-A")
    assert first.status_code == 200
    created = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(first.json())
    )
    assert created.status_code == 201
    second = lookup(billing_client, "SYNTHETIC-ACCOUNT-B")
    assert second.status_code == 200
    history = billing_client.get(
        f"/api/v1/subscriber-lookups/{second.json()['lookup_id']}/tickets?page=1&size=10"
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["id"] == created.json()["id"]
    active = billing_client.get(
        f"/api/v1/subscriber-lookups/{second.json()['lookup_id']}/active-tickets"
    )
    assert active.status_code == 200
    assert active.json()["total_visible"] == 1
    assert active.json()["items"][0]["id"] == created.json()["id"]
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(second.json())
        ).json()["code"]
        == "active_ticket_exists"
    )
    assert (
        billing_client.get(
            f"/api/v1/subscriber-lookups/{second.json()['lookup_id']}/tickets?page=0"
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("account", "access_type"),
    [
        ("MOCK-ETH-001", "ETHERNET"),
        ("MOCK-EPON-001", "EPON"),
        ("MOCK-GPON-001", "GPON"),
    ],
)
def test_mock_access_types_and_field_origins(account: str, access_type: str) -> None:
    data = MockBillingProvider().lookup_by_account(account)
    assert data is not None and data.source == "MOCK"
    assert data.billing_account == account
    assert data.connections[0].access_type == access_type
    assert data.field_meta["billing_account"].source == "MOCK"
    assert data.field_meta["subscriber_id"].observed_at.tzinfo is not None
    assert data.connections[0].field_meta["access_type"].source == "MOCK"
    assert data.connections[0].field_meta["device"].observed_at <= data.fetched_at
    if access_type == "ETHERNET":
        assert data.connections[0].onu_id is None
    else:
        assert data.connections[0].onu_id is not None
    assert len(data.services) == (2 if access_type == "GPON" else 1)


def test_mock_missing_stale_conflict_and_unavailable() -> None:
    provider = MockBillingProvider()
    partial = provider.lookup_by_account("MOCK-PARTIAL-001")
    assert partial.connections[0].port is None
    assert partial.connections[0].vlan is None
    assert partial.connections[0].onu_id is None
    assert partial.connections[0].field_meta["port"].quality == DataQuality.UNKNOWN
    stale = provider.lookup_by_account("MOCK-STALE-001")
    assert stale.connections[0].field_meta["device"].quality == DataQuality.STALE
    assert stale.connections[0].field_meta["device"].observed_at < stale.fetched_at
    conflict = provider.lookup_by_account("MOCK-CONFLICT-001")
    assert conflict.connections[0].vlan is None
    assert conflict.connections[0].field_meta["vlan"].quality == DataQuality.CONFLICT
    assert provider.lookup_by_account("MOCK-NOT-FOUND-001") is None
    with pytest.raises(BillingUnavailable):
        provider.lookup_by_account("MOCK-UNAVAILABLE-001")
    assert provider.lookup_by_account("MOCK-NO-CONNECTION-001").connections == []


def test_lookup_persists_source_and_immutable_snapshot(
    billing_engine: Engine, billing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    seed_user(billing_engine, "operator", Role.OPERATOR)
    login(billing_client, "operator")
    response = lookup(billing_client, "MOCK-ETH-001")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["source"] == "MOCK" and card["technical_state"] == "UNKNOWN"
    assert card["subscriber"]["subscriber_id"] == "MOCK-SUBSCRIBER-ETH"
    assert card["subscriber"]["billing_account"] == "MOCK-ETH-001"
    assert card["subscriber"]["field_meta"]["full_name"]["source"] == "MOCK"
    assert card["connections"][0]["onu_id"] is None
    assert card["connections"][0]["field_meta"]["onu_id"]["quality"] == "UNKNOWN"
    assert card["contracts"][0]["field_meta"]["contract_id"]["source"] == "MOCK"
    assert card["services"][0]["field_meta"]["service_id"]["observed_at"]
    assert billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}").json() == card
    with Session(billing_engine) as db:
        snapshot = db.get(SubscriberSnapshot, UUID(card["lookup_id"]))
        assert snapshot.field_meta["billing_account"]["source"] == "MOCK"
        assert snapshot.fetched_at == datetime.fromisoformat(card["fetched_at"])
        assert db.scalar(select(func.count()).select_from(ConnectionSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(TicketAudit)) == 1
    with pytest.raises(DBAPIError, match="immutable Service Desk record"):
        with billing_engine.begin() as connection:
            connection.execute(
                text("UPDATE subscriber_snapshots SET field_meta = '{}'::jsonb WHERE id = :id"),
                {"id": UUID(card["lookup_id"])},
            )
    assert "Тестовый абонент" not in caplog.text
    assert "+0-000-000-0001" not in caplog.text


def test_lookup_errors_rbac_and_size_limit(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    seed_user(billing_engine, "operator", Role.OPERATOR)
    seed_user(billing_engine, "engineer", Role.ENGINEER)
    seed_user(billing_engine, "admin", Role.ADMIN)
    assert (
        billing_client.post(
            "/api/v1/subscriber-lookups", json={"account_no": "MOCK-ETH-001"}
        ).status_code
        == 401
    )
    login(billing_client, "operator")
    assert (
        billing_client.post(
            "/api/v1/subscriber-lookups", json={"account_no": "MOCK-ETH-001"}
        ).status_code
        == 403
    )
    assert lookup(billing_client, "MOCK-NOT-FOUND-001").status_code == 404
    unavailable = lookup(billing_client, "MOCK-UNAVAILABLE-001")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "billing_unavailable"
    assert lookup(billing_client, "MOCK-ETH-001" * 20).status_code == 422
    assert (
        billing_client.post(
            "/api/v1/subscriber-lookups",
            headers=csrf(billing_client),
            json={"account_no": "MOCK-ETH-001", "extra": "x" * 3000},
        ).status_code
        == 413
    )
    card = lookup(billing_client, "MOCK-ETH-001").json()
    login(billing_client, "engineer")
    assert billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}").status_code == 404
    assert lookup(billing_client, "MOCK-EPON-001").status_code == 200
    login(billing_client, "admin")
    assert lookup(billing_client, "MOCK-ETH-001").status_code == 200
    assert billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}").status_code == 404
    with Session(billing_engine) as db:
        assert db.scalar(select(func.count()).select_from(SubscriberSnapshot)) == 3


def test_multiple_choices_snapshot_confirmation_and_ticket_flow(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    operator = seed_user(billing_engine, "operator", Role.OPERATOR)
    engineer = seed_user(billing_engine, "engineer", Role.ENGINEER)
    login(billing_client, "operator")
    card = lookup(billing_client, "MOCK-GPON-001").json()
    assert len(card["connections"]) == 2 and len(card["services"]) == 2
    assert card["subscriber"]["organization_name"] == "MOCK Организация GPON"
    payload = ticket_payload(card)
    without_service = {**payload, "selected_service_id": None}
    without_connection = {**payload, "connection_snapshot_id": None}
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=without_service
        ).status_code
        == 422
    )
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=without_connection
        ).status_code
        == 422
    )
    spoof = {**payload, "full_name": "Forged Name"}
    assert (
        billing_client.post("/api/v1/tickets", headers=csrf(billing_client), json=spoof).status_code
        == 422
    )
    wrong_service = {**payload, "selected_service_id": "MOCK-SERVICE-OTHER"}
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=wrong_service
        ).status_code
        == 422
    )
    request_id = uuid4()
    payload["client_request_id"] = str(request_id)
    created = billing_client.post("/api/v1/tickets", headers=csrf(billing_client), json=payload)
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert created.json()["selected_service_id"] == payload["selected_service_id"]
    replay = billing_client.post("/api/v1/tickets", headers=csrf(billing_client), json=payload)
    assert replay.status_code == 200 and replay.json()["id"] == ticket_id
    detail = billing_client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["subscriber"]["organization_name"] == "MOCK Организация GPON"
    assert len(detail["subscriber"]["services"]) == 2
    assert detail["technical_state"] == "UNKNOWN"
    login(billing_client, "engineer")
    assert (
        billing_client.post(
            f"/api/v1/tickets/{ticket_id}/assignment",
            headers={**csrf(billing_client), "If-Match": '"1"'},
            json={"engineer_id": str(engineer.id)},
        ).status_code
        == 200
    )
    assert (
        billing_client.post(
            f"/api/v1/tickets/{ticket_id}/start",
            headers={**csrf(billing_client), "If-Match": '"2"'},
        ).status_code
        == 200
    )
    decision = billing_client.post(
        f"/api/v1/tickets/{ticket_id}/decisions",
        headers=csrf(billing_client),
        json={"decision_text": "Synthetic decision", "performed_actions": "Synthetic check"},
    )
    assert decision.status_code == 201
    resolved = billing_client.post(
        f"/api/v1/tickets/{ticket_id}/resolve",
        headers={**csrf(billing_client), "If-Match": '"4"'},
        json={"decision_text": "Synthetic decision", "performed_actions": "Synthetic check"},
    )
    assert resolved.status_code == 200, resolved.text
    closed = billing_client.post(
        f"/api/v1/tickets/{ticket_id}/close",
        headers={**csrf(billing_client), "If-Match": f'"{resolved.json()["version"]}"'},
        json={"decision_id": resolved.json()["resolved_decision_id"]},
    )
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    with Session(billing_engine) as db:
        ticket = db.get(Ticket, UUID(ticket_id))
        assert ticket.created_by == operator.id
        assert ticket.selected_service_id == payload["selected_service_id"]
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(TicketAudit.ticket_id == ticket.id)
            )
            == 6
        )


def test_expired_lookup_cannot_create_ticket(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    seed_user(billing_engine, "operator", Role.OPERATOR)

    class StaleProvider:
        def lookup_by_account(self, account_no: str):
            data = MockBillingProvider().lookup_by_account(account_no)
            return data.model_copy(update={"fetched_at": datetime.now(UTC) - timedelta(hours=1)})

    billing_client.app.dependency_overrides[get_billing_provider] = lambda: StaleProvider()
    login(billing_client, "operator")
    card = lookup(billing_client, "MOCK-ETH-001").json()
    assert card["is_expired"] is True
    response = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
    )
    assert response.status_code == 409 and response.json()["code"] == "lookup_expired"


def test_no_connection_remains_unknown_and_is_not_invented(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    seed_user(billing_engine, "operator", Role.OPERATOR)
    login(billing_client, "operator")
    card = lookup(billing_client, "MOCK-NO-CONNECTION-001").json()
    assert card["connections"] == [] and card["technical_state"] == "UNKNOWN"
    created = billing_client.post(
        "/api/v1/tickets",
        headers=csrf(billing_client),
        json={
            "lookup_id": card["lookup_id"],
            "selected_service_id": card["services"][0]["service_id"],
            "description": "No known connection",
            "client_request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["connection_snapshot_id"] is None


def test_concurrent_create_uses_one_ticket(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    operator = seed_user(billing_engine, "operator", Role.OPERATOR)
    login(billing_client, "operator")
    card = lookup(billing_client, "MOCK-ETH-001").json()
    payload = CreateTicket.model_validate(ticket_payload(card))

    def attempt():
        with Session(billing_engine) as db:
            ticket, created = create_ticket(db, operator.id, payload)
            return ticket.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert outcomes[0][0] == outcomes[1][0]
    assert sorted(created for _, created in outcomes) == [False, True]
    with Session(billing_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketAudit)
                .where(TicketAudit.ticket_id.is_not(None))
            )
            == 1
        )


def test_lookup_rechecks_role_after_remote_wait_without_holding_actor_lock(
    billing_engine: Engine,
) -> None:
    operator = seed_user(billing_engine, "lookup_operator", Role.OPERATOR)
    started, release = Event(), Event()

    class SlowProvider:
        def lookup_by_account(self, account_no: str):
            started.set()
            assert release.wait(timeout=5)
            return MockBillingProvider().lookup_by_account(account_no)

    def attempt() -> None:
        with Session(billing_engine) as db:
            create_lookup(db, operator.id, "MOCK-ETH-001", SlowProvider())

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(attempt)
        assert started.wait(timeout=5)
        try:
            # If lookup still owns FOR UPDATE, PostgreSQL lock_timeout fails here.
            with Session(billing_engine) as db:
                db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                current = db.get(User, operator.id, with_for_update=True)
                assert current is not None
                current.is_active = False
                db.commit()
        finally:
            release.set()
        with pytest.raises(ApiError) as error:
            future.result(timeout=5)
        assert error.value.status_code == 403
    with Session(billing_engine) as db:
        assert db.scalar(select(func.count()).select_from(SubscriberSnapshot)) == 0


def test_synthetic_netup_http_snapshot_ticket_round_trip(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    seed_user(billing_engine, "netup_operator", Role.OPERATOR)
    seed_user(billing_engine, "netup_engineer", Role.ENGINEER)
    seed_user(billing_engine, "netup_admin", Role.ADMIN)
    when = datetime.now(UTC)

    def row(row_id: int, switch_id: int) -> NetupGroupRow:
        return NetupGroupRow(
            row_id=row_id,
            ip_type=0,
            ip=0,
            mask=0,
            ip_ext=0,
            mask_ext=0,
            pool_id=0,
            switch_id=switch_id,
            switch_name=f"synthetic-{switch_id}",
            port_id=2,
            vlan_id=100,
            ranges=(),
        )

    raw = NetupSubscriber(
        user_id=101,
        requested_account_id=202,
        full_name="Synthetic Person",
        is_juridical=False,
        mobile_phone="000-000",
        work_phone="111-111",
        home_phone="",
        email="",
        actual_address="Synthetic address",
        accounts=(NetupAccount(202, True),),
        services=(
            NetupService(
                301,
                202,
                "Synthetic Internet",
                3,
                (NetupGroup(9, (row(11, 7), row(12, 8))), NetupGroup(10, (row(13, 7),))),
            ),
            NetupService(302, 202, "Synthetic extra", 2, ()),
        ),
        fetched_at=when,
    )

    class SyntheticRepository:
        def lookup_by_account(self, account_no: str) -> NetupSubscriber:
            assert account_no == "202"
            return raw

    billing_client.app.dependency_overrides[get_billing_provider] = lambda: NetUPBillingProvider(
        SyntheticRepository()
    )
    login(billing_client, "netup_operator")
    response = lookup(billing_client, "202")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    card = response.json()
    assert card["source"] == "NETUP"
    assert card["subscriber"]["phone"] is None
    assert len(card["subscriber"]["contacts"]) == 2
    assert card["connections"][0]["service_id"] == "301"
    assert [group["group_id"] for group in card["connections"][0]["ip_groups"]] == ["9", "10"]
    assert len(card["connections"][0]["ip_groups"][0]["rows"]) == 2
    assert card["connections"][0]["field_meta"]["device"]["quality"] == "CONFLICT"
    assert billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}").json() == card
    with Session(billing_engine) as db:
        snapshot = db.get(SubscriberSnapshot, UUID(card["lookup_id"]))
        assert snapshot is not None and len(snapshot.contacts) == 2
        stored_group = db.scalar(
            select(ConnectionSnapshot).where(
                ConnectionSnapshot.subscriber_snapshot_id == snapshot.id
            )
        )
        assert stored_group is not None
        assert [group["group_id"] for group in stored_group.ip_groups] == ["9", "10"]
    created = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
    )
    assert created.status_code == 201, created.text
    assert created.json()["selected_service_id"] == "301"
    detail = billing_client.get(f"/api/v1/tickets/{created.json()['id']}")
    assert detail.status_code == 200
    for protected_response in (
        billing_client.get("/api/v1/auth/me"),
        billing_client.get("/api/v1/tickets"),
        billing_client.get(f"/api/v1/tickets/{created.json()['id']}/comments"),
        detail,
    ):
        assert protected_response.status_code == 200
        assert protected_response.headers["Cache-Control"] == "no-store"
    assert detail.json()["subscriber"]["contacts"] == card["subscriber"]["contacts"]
    assert detail.json()["selected_connection"]["ip_groups"] == card["connections"][0]["ip_groups"]
    login(billing_client, "netup_engineer")
    assert len(lookup(billing_client, "202").json()["subscriber"]["contacts"]) == 2
    login(billing_client, "netup_admin")
    assert lookup(billing_client, "202").status_code == 200


def test_upgrade_existing_ticket_from_0003(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0003_ticket_engine")
    engine = create_engine(isolated_postgres_url)
    user_id, snapshot_id, ticket_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app_users "
                    "(id, login, display_name, role, password_hash, is_active) "
                    "VALUES (:id, 'legacy', 'Legacy', 'operator', :hash, true)"
                ),
                {"id": user_id, "hash": hash_password(PASSWORD)},
            )
            connection.execute(
                text(
                    "INSERT INTO subscriber_snapshots "
                    "(id, account_no, source, fetched_at, requested_by, "
                    "expires_at, contracts, services) "
                    "VALUES (:id, 'MOCK-LEGACY', 'MOCK', now(), :user_id, "
                    "now() + interval '1 hour', '[]'::jsonb, '[]'::jsonb)"
                ),
                {"id": snapshot_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO tickets "
                    "(id, account_no, subscriber_snapshot_id, description, status, priority, "
                    "created_by, client_request_id, version) "
                    "VALUES (:id, 'MOCK-LEGACY', :snapshot_id, 'Old ticket', 'OPEN', 'NORMAL', "
                    ":user_id, :request_id, 1)"
                ),
                {
                    "id": ticket_id,
                    "snapshot_id": snapshot_id,
                    "user_id": user_id,
                    "request_id": uuid4(),
                },
            )
        command.upgrade(config, "0004_subscriber_360")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004_subscriber_360"
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM tickets WHERE id = :id"), {"id": ticket_id}
                ).scalar_one()
                == 1
            )
            assert {"selected_service_id"} <= {
                column["name"] for column in inspect(connection).get_columns("tickets")
            }
            assert (
                connection.execute(
                    text("SELECT selected_service_id FROM tickets WHERE id = :id"),
                    {"id": ticket_id},
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    text("SELECT field_meta FROM subscriber_snapshots WHERE id = :id"),
                    {"id": snapshot_id},
                ).scalar_one()
                == {}
            )
            assert "actual_address" not in {
                column["name"] for column in inspect(connection).get_columns("subscriber_snapshots")
            }
        # The current ORM includes 0007 columns; inspect 0004/0005 only with raw SQL.
        command.upgrade(config, "0005_netup_groups")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0005_netup_groups"
            )
        command.upgrade(config, "head")
        with Session(engine) as db:
            detail = ticket_detail(db, ticket_id)
            assert detail.account_no == "MOCK-LEGACY"
            assert detail.selected_service_id is None
            assert detail.subscriber.field_meta == {}
            assert detail.subscriber.actual_address is None
            assert detail.subscriber.accounts == []
            assert detail.subscriber.contacts == []
            assert db.get(SubscriberSnapshot, snapshot_id).financial_accounts == []
        with engine.connect() as connection:
            assert "ix_subscriber_snapshots_source_subscriber" in {
                item["name"] for item in inspect(connection).get_indexes("subscriber_snapshots")
            }
    finally:
        engine.dispose()


def test_active_ticket_warning_override_and_inactive_tickets(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    admin = seed_user(billing_engine, "duplicate_admin", Role.ADMIN)
    login(billing_client, admin.login)
    card = lookup(billing_client, "MOCK-ETH-001").json()
    path = f"/api/v1/subscriber-lookups/{card['lookup_id']}/active-tickets"
    assert billing_client.get(path).json()["total_visible"] == 0
    first = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
    )
    assert first.status_code == 201
    result = billing_client.get(path)
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json()["items"][0]["number"] == first.json()["number"]
    assert result.json()["total_visible"] == 1
    rejected = billing_client.post(
        "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "active_ticket_exists"
    assert "number" not in rejected.json()
    payload = {**ticket_payload(card), "duplicate_reason": "Separate synthetic issue"}
    second = billing_client.post("/api/v1/tickets", headers=csrf(billing_client), json=payload)
    assert second.status_code == 201
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=payload
        ).status_code
        == 200
    )
    assert (
        billing_client.post(
            "/api/v1/tickets",
            headers=csrf(billing_client),
            json={**payload, "duplicate_reason": "Changed reason"},
        ).json()["code"]
        == "request_id_conflict"
    )
    assert billing_client.get(path).json()["total_visible"] == 2
    assert billing_client.get(f"{path}?size=1&page=2").json()["total_visible"] == 2
    assert billing_client.get(f"{path}?size=51").status_code == 422
    assert (
        billing_client.post(
            "/api/v1/tickets",
            headers=csrf(billing_client),
            json={**ticket_payload(card), "duplicate_reason": "  "},
        ).status_code
        == 422
    )
    assert (
        billing_client.post(
            "/api/v1/tickets",
            headers=csrf(billing_client),
            json={
                "ticket_type": "MANUAL",
                "description": "Manual",
                "duplicate_reason": "Other",
                "client_request_id": str(uuid4()),
            },
        ).status_code
        == 422
    )
    with Session(billing_engine) as db:
        event = db.scalar(
            select(TicketEvent).where(
                TicketEvent.ticket_id == UUID(second.json()["id"]),
                TicketEvent.event_type == "ticket_created",
            )
        )
        assert event.event_data["duplicate_reason"] == "Separate synthetic issue"
    start = billing_client.post(
        f"/api/v1/tickets/{first.json()['id']}/start",
        headers={**csrf(billing_client), "If-Match": '"1"'},
    )
    assert start.status_code == 200
    assert {item["status"] for item in billing_client.get(path).json()["items"]} == {
        "OPEN",
        "IN_PROGRESS",
    }
    decision = billing_client.post(
        f"/api/v1/tickets/{first.json()['id']}/decisions",
        headers=csrf(billing_client),
        json={"decision_text": "Synthetic resolution", "performed_actions": "Verified"},
    )
    assert decision.status_code == 201
    version = billing_client.get(f"/api/v1/tickets/{first.json()['id']}").json()["version"]
    resolved = billing_client.post(
        f"/api/v1/tickets/{first.json()['id']}/resolve",
        headers={**csrf(billing_client), "If-Match": f'"{version}"'},
        json={"decision_text": "Synthetic resolution", "performed_actions": "Verified"},
    )
    assert resolved.status_code == 200, resolved.text
    unresolved = billing_client.get(path).json()
    assert unresolved["total_visible"] == 2
    assert "RESOLVED" in {item["status"] for item in unresolved["items"]}
    closed = billing_client.post(
        f"/api/v1/tickets/{first.json()['id']}/close",
        headers={**csrf(billing_client), "If-Match": f'"{resolved.json()["version"]}"'},
        json={"decision_id": resolved.json()["resolved_decision_id"]},
    )
    assert closed.status_code == 200
    deleted = billing_client.request(
        "DELETE",
        f"/api/v1/tickets/{second.json()['id']}",
        headers={**csrf(billing_client), "If-Match": '"1"'},
        json={},
    )
    assert deleted.status_code == 200
    assert billing_client.get(path).json()["total_visible"] == 0
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
        ).status_code
        == 201
    )


def test_hidden_active_ticket_does_not_disclose_details(
    billing_engine: Engine, billing_client: TestClient
) -> None:
    owner = seed_user(billing_engine, "duplicate_owner", Role.OPERATOR)
    restricted = seed_user(billing_engine, "duplicate_restricted", Role.OPERATOR)
    login(billing_client, owner.login)
    owner_card = lookup(billing_client, "MOCK-ETH-001").json()
    first = billing_client.post(
        "/api/v1/tickets",
        headers=csrf(billing_client),
        json={**ticket_payload(owner_card), "subject": "Private synthetic subject"},
    )
    assert first.status_code == 201
    with Session(billing_engine) as db:
        user = db.get(User, restricted.id)
        user.permissions_customized = True
        db.add_all(
            UserPermission(user_id=restricted.id, permission=permission)
            for permission in (
                Permission.SUBSCRIBER_READ,
                Permission.TICKET_CREATE,
                Permission.TICKET_READ_OWN,
            )
        )
        db.commit()
    login(billing_client, restricted.login)
    card = lookup(billing_client, "MOCK-ETH-001").json()
    response = billing_client.get(f"/api/v1/subscriber-lookups/{card['lookup_id']}/active-tickets")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["has_hidden_active"] is True
    assert "Private synthetic subject" not in response.text
    assert "number" not in response.json()
    assert (
        billing_client.post(
            "/api/v1/tickets", headers=csrf(billing_client), json=ticket_payload(card)
        ).json()["code"]
        == "active_ticket_exists"
    )
    assert billing_client.get(f"/api/v1/tickets/{first.json()['id']}").status_code == 404


def test_parallel_linked_creates_are_serialized_by_subscriber(
    billing_engine: Engine,
) -> None:
    actors = [seed_user(billing_engine, f"parallel_duplicate_{n}", Role.OPERATOR) for n in (1, 2)]
    snapshots = []
    for actor in actors:
        with Session(billing_engine) as db:
            snapshots.append(create_lookup(db, actor.id, "MOCK-ETH-001", MockBillingProvider()).id)
    gate = Barrier(2)

    def create_one(index: int) -> int:
        with Session(billing_engine) as db:
            payload = CreateTicket(
                lookup_id=snapshots[index],
                description="Concurrent synthetic issue",
                client_request_id=uuid4(),
            )
            gate.wait(timeout=10)
            try:
                create_ticket(db, actors[index].id, payload)
                return 201
            except ApiError as exc:
                assert exc.code == "active_ticket_exists"
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_one, (0, 1)))
    assert sorted(outcomes) == [201, 409]
    with Session(billing_engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
