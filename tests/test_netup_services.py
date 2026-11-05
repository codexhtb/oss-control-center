"""Synthetic NetUP service contract: prices are configuration, never ledger guesses."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.billing.netup.services import _current, load_services, load_tariff_history


class Cursor:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return next(self.answers)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def row(link_id, service_type=3, cost=1400, coefficient=1, **overrides):
    value = {
        "link_id": link_id,
        "service_id": link_id + 200,
        "tariff_link_id": 42 if service_type == 3 else None,
        "service_name": "Единый" if service_type == 3 else "Внешний IP",
        "service_type": service_type,
        "tariff_id": 209 if service_type == 3 else 0,
        "catalog_deleted": 0,
        "periodic_deleted": 0,
        "is_blocked": 0,
        "is_planned": 0,
        "need_del": 0,
        "start_date": 100,
        "expire_date": 2_147_483_647,
        "cost_coef": coefficient,
        "base_cost": cost,
        "linked_tariff_id": 209 if service_type == 3 else None,
        "tariff_name": "Единый" if service_type == 3 else None,
    }
    return {**value, **overrides}


def test_configured_cost_multiple_services_and_no_guess_for_missing_prices():
    cursor = Cursor([[row(1), row(2, 2, 150, "1.25"), row(3, 2, None)]])
    result = _current(cursor, 4951, 4941, 1_800_000_000, "RUR")
    assert [item["role"] for item in result["items"]] == ["main_tariff", "additional", "additional"]
    assert [item["configured_cost"] for item in result["items"]] == ["1400.00", "187.50", None]
    assert result["items"][0]["period"] is None
    assert result["items"][0]["ends_at"] is None  # NetUP sentinel
    assert cursor.queries[0][1] == (4951, 4941, 51)


def test_ambiguous_tariff_and_inactive_links_are_not_called_main_or_active():
    cursor = Cursor([[row(1), row(2), row(3, 2, expire_date=500), row(4, 2, is_blocked=1)]])
    items = _current(cursor, 76, 76, 1000, None)["items"]
    assert all(item["role"] == "additional" for item in items)
    assert [item["status"] for item in items] == ["active", "active", "ended", "blocked"]
    assert all(item["currency"] is None for item in items)
    assert (
        _current(Cursor([[row(8, tariff_id=0, linked_tariff_id=0)]]), 76, 76, 1000, None)["items"][
            0
        ]["role"]
        == "additional"
    )


def test_read_only_bounded_query_and_source_error_close_connection():
    cursor = Cursor([[]])
    connection = Connection(cursor)
    config = SimpleNamespace(
        connect=lambda: connection,
        max_execution_ms=3000,
        lab_as_of_utc=datetime(2026, 11, 5, tzinfo=UTC),
    )
    assert load_services(config, 76, 76, None)["items"] == []
    assert any("READ ONLY" in sql for sql, _ in cursor.queries)
    assert connection.rolled_back and connection.closed
    assert len([sql for sql, _ in cursor.queries if "FROM service_links" in sql]) == 1
    overflow = Cursor([[row(index) for index in range(51)]])
    connection = Connection(overflow)
    config.connect = lambda: connection
    with pytest.raises(ValueError, match="Too many"):
        load_services(config, 76, 76, None)
    assert connection.rolled_back and connection.closed


def test_history_uses_plan_guard_and_never_infers_historical_price():
    cursor = Cursor(
        [
            [{"rows": 15000}],
            [
                {
                    "id": 1,
                    "tariff_id": 192,
                    "tariff_name": "Единый",
                    "link_date": 1759266044,
                    "unlink_date": 1772312447,
                }
            ],
        ]
    )
    connection = Connection(cursor)
    config = SimpleNamespace(connect=lambda: connection, max_execution_ms=3000)
    history = load_tariff_history(config, 4951)
    assert history["available"] and len(history["items"]) == 1
    assert history["items"][0]["configured_cost"] is None
    assert cursor.queries[2][1] == (4951, 51)
    assert connection.rolled_back and connection.closed
    guarded = Cursor([[{"rows": 100000}]])
    config.connect = lambda: Connection(guarded)
    assert load_tariff_history(config, 4951)["reason"] == "history_scan_too_large"
    assert not any("SELECT id,tariff_id" in sql for sql, _ in guarded.queries)
    unknown_plan = Cursor([[{"rows": None}]])
    config.connect = lambda: Connection(unknown_plan)
    assert load_tariff_history(config, 4951)["available"] is False
