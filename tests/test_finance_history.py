"""Finance Phase 2 classification, endpoint authorization and bounded reads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response

from app.api.v1 import billing
from app.core.errors import ApiError
from app.modules.billing.netup.finance import _classification, amount, load_finance


def test_money_and_payment_classification() -> None:
    assert amount(10100.07) == "10100.07"
    assert amount(0.1 + 0.2) == "0.30"
    with pytest.raises(ValueError):
        amount(None)
    base = {"is_canceled": 0, "cancel_id": 0, "method": 6553639, "payment_absolute": 100}
    assert _classification(base) == "cash"
    assert _classification({**base, "method": 7}) == "credit"
    assert _classification({**base, "is_canceled": 1}) == "canceled"
    assert _classification({**base, "cancel_id": 12}) == "canceled"
    assert _classification({**base, "method": 8}) == "canceled"
    assert _classification({**base, "payment_absolute": -100}) == "unclassified_negative"


def test_bounded_history_separates_cash_credit_charges_and_pages() -> None:
    sql = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            sql.append((statement, params))

        def fetchone(self):
            statement = sql[-1][0]
            if "cash," in statement:
                return {"cash": 20350.0, "credit": 10100.07, "unclassified_negative": -10.0}
            if " adjustments " in statement:
                return {"charges": 21350.0, "adjustments": -25.0}
            return {"total": 31}

        def fetchall(self):
            return [
                {
                    "id": 5,
                    "actual_date": 1793519899,
                    "payment_absolute": 1000.01,
                    "payment_incurrency": 1000.01,
                    "currency_id": 810,
                    "method": 7,
                    "is_canceled": 0,
                    "cancel_id": 0,
                    "burn_time": 0,
                    "method_name": "Credit",
                    "currency_name": "RUR",
                }
            ]

    class Connection:
        closed = False
        rolled_back = False

        def cursor(self):
            return Cursor()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = Connection()
    config = SimpleNamespace(connect=lambda: connection, max_execution_ms=3000)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(days=31)
    data = load_finance(config, 4951, start, end, "credit", 2, 10, "RUR")
    assert data["summary"] == {
        "cash": "20350.00",
        "credit": "10100.07",
        "unclassified_negative": "-10.00",
        "charges": "21350.00",
        "adjustments": "-25.00",
    }
    assert data["items"][0]["type"] == "credit"
    assert data["items"][0]["amount"] == "1000.01"
    assert data["total"] == 31 and data["page"] == 2
    assert sql[-1][1][-2:] == (10, 10)
    assert "method=7" in sql[-1][0]
    assert "charge_type IN (1,2,3)" in sql[4][0]
    assert connection.rolled_back and connection.closed


def test_charge_filter_uses_account_and_service_link_in_both_queries() -> None:
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params=None):
            statements.append((sql, params))

        def fetchone(self):
            sql = statements[-1][0]
            if " cash," in sql:
                return {"cash": 0, "credit": 0, "unclassified_negative": 0}
            if " adjustments " in sql:
                return {"charges": 100, "adjustments": 0}
            return {"total": 0}

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    start = datetime(2026, 10, 1, tzinfo=UTC)
    result = load_finance(
        SimpleNamespace(connect=Connection, max_execution_ms=3000),
        4951,
        start,
        start + timedelta(days=31),
        "charges",
        1,
        20,
        "RUR",
        20627,
    )
    assert result["total"] == 0
    assert result["summary"]["charges"] == "100.00"  # account-wide summary
    count, rows = statements[-2:]
    assert "account_id=%s" in count[0] and "slink_id=%s" in count[0]
    assert "d.account_id=%s" in rows[0] and "slink_id=%s" in rows[0]
    assert count[1][-2:] == (4951, 20627)
    assert rows[1][-4:-2] == (4951, 20627)


def test_finance_endpoint_requires_grant_and_owned_netup_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, lookup_id = uuid4(), uuid4()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(days=31)
    snapshot = SimpleNamespace(
        requested_by=actor_id,
        source="NETUP",
        account_no="4951",
        subscriber_id="4941",
        financial_accounts=[
            {
                "account_id": "4951",
                "currency": "RUR",
                "field_meta": {"currency": {"quality": "KNOWN"}},
            }
        ],
    )

    class Db:
        rolled_back = False

        def get(self, _model, _id):
            return snapshot

        def rollback(self):
            self.rolled_back = True

    db = Db()
    actor = SimpleNamespace(user=SimpleNamespace(id=actor_id))
    response = Response()
    monkeypatch.setattr(
        billing, "get_settings", lambda: SimpleNamespace(billing_provider="NETUP", app_env="dev")
    )
    monkeypatch.setattr(billing.NetupConfig, "from_env", lambda **_kw: "configured-replica")
    calls = []

    def fetch(*args):
        calls.append(args)
        return {"summary": {"cash": "20.00"}, "items": []}

    monkeypatch.setattr(billing, "load_finance", fetch)
    monkeypatch.setattr(billing, "has_permission", lambda *_args: False)
    with pytest.raises(ApiError) as denied:
        billing.finance_history(lookup_id, response, actor, db, start, end)
    assert denied.value.status_code == 403
    assert not calls

    monkeypatch.setattr(billing, "has_permission", lambda *_args: True)
    snapshot.requested_by = uuid4()
    with pytest.raises(ApiError) as foreign:
        billing.finance_history(lookup_id, response, actor, db, start, end)
    assert foreign.value.status_code == 404
    assert not calls

    snapshot.requested_by = actor_id
    with pytest.raises(ApiError) as invalid:
        billing.finance_history(lookup_id, response, actor, db, start, start + timedelta(days=367))
    assert invalid.value.status_code == 422
    assert not calls

    assert billing.finance_history(lookup_id, response, actor, db, start, end)["items"] == []
    assert len(calls) == 1
    assert calls[0][0:2] == ("configured-replica", 4951)
    assert calls[0][7] == "RUR"
    assert db.rolled_back
    assert response.headers["cache-control"] == "no-store"

    with pytest.raises(ApiError) as invalid_service_filter:
        billing.finance_history(
            lookup_id, response, actor, db, start, end, kind="payments", service_link_id=20627
        )
    assert invalid_service_filter.value.status_code == 422
    assert len(calls) == 1

    snapshot.source = "MOCK"
    with pytest.raises(ApiError) as unavailable:
        billing.finance_history(lookup_id, response, actor, db, start, end)
    assert unavailable.value.status_code == 503


def test_services_endpoint_rejects_missing_finance_grant_and_foreign_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, lookup_id = uuid4(), uuid4()
    snapshot = SimpleNamespace(
        requested_by=actor_id,
        source="NETUP",
        account_no="4951",
        subscriber_id="4941",
        financial_accounts=[],
    )

    class Db:
        def get(self, *_):
            return snapshot

        def rollback(self):
            pass

    actor = SimpleNamespace(user=SimpleNamespace(id=actor_id))
    monkeypatch.setattr(
        billing, "get_settings", lambda: SimpleNamespace(billing_provider="NETUP", app_env="dev")
    )
    monkeypatch.setattr(billing.NetupConfig, "from_env", lambda **_: "replica")
    calls = []
    monkeypatch.setattr(
        billing,
        "load_services",
        lambda *args: calls.append(args) or {"items": [], "source": "NETUP"},
    )
    monkeypatch.setattr(billing, "has_permission", lambda *_: False)
    with pytest.raises(ApiError) as denied:
        billing.configured_services(lookup_id, Response(), actor, Db())
    assert denied.value.status_code == 403 and not calls
    monkeypatch.setattr(billing, "has_permission", lambda *_: True)
    snapshot.requested_by = uuid4()
    with pytest.raises(ApiError) as foreign:
        billing.configured_services(lookup_id, Response(), actor, Db())
    assert foreign.value.status_code == 404 and not calls
    snapshot.requested_by = actor_id
    response = Response()
    assert billing.configured_services(lookup_id, response, actor, Db())["items"] == []
    assert calls[0] == ("replica", 4951, 4941, None)
    assert response.headers["cache-control"] == "no-store"
