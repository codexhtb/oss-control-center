"""Bounded, read-only view of configured NetUP services and tariff history."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import BoundedSemaphore
from typing import Any

from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.finance import amount

_SLOTS = BoundedSemaphore(4)
_MAX_SERVICES = 50
_MAX_HISTORY = 50
_MAX_HISTORY_EXPLAIN_ROWS = 50_000


def _date(value: Any) -> str | None:
    if value is None or int(value) <= 0 or int(value) >= 2_000_000_000:
        return None
    return datetime.fromtimestamp(int(value), UTC).isoformat()


def _cost(row: dict[str, Any]) -> str | None:
    if row["service_type"] not in (2, 3) or row["catalog_deleted"] or row["periodic_deleted"]:
        return None
    try:
        cost = Decimal(str(row["base_cost"]))
        coefficient = Decimal(str(row["cost_coef"]))
        if not cost.is_finite() or not coefficient.is_finite() or cost < 0 or coefficient < 0:
            return None
        return amount(cost * coefficient)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _status(row: dict[str, Any], now: int) -> str:
    if row["catalog_deleted"] or row["periodic_deleted"]:
        return "unknown"
    if row["service_type"] not in (2, 3):
        return "unknown"
    if row["is_planned"]:
        return "planned"
    if row["need_del"] or row["expire_date"] is not None and row["expire_date"] <= now:
        return "ended"
    if row["start_date"] is None or row["expire_date"] is None:
        return "unknown"
    if row["start_date"] > now:
        return "planned"
    if row["is_blocked"]:
        return "blocked"
    return "active"


def _current(cursor: Any, account_id: int, user_id: int, now: int, currency: str | None) -> dict:
    cursor.execute(
        "SELECT sl.id AS link_id,sl.service_id,sl.tariff_link_id,"
        "sd.service_name,sd.service_type,sd.tariff_id,sd.is_deleted AS catalog_deleted,"
        "psl.is_deleted AS periodic_deleted,psl.is_blocked,psl.is_planned,psl.need_del,"
        "psl.start_date,psl.expire_date,psl.cost_coef,psd.cost AS base_cost,"
        "atl.tariff_id AS linked_tariff_id,t.name AS tariff_name "
        "FROM service_links sl "
        "LEFT JOIN services_data sd ON sd.id=sl.service_id "
        "LEFT JOIN periodic_service_links psl ON psl.id=sl.id "
        "LEFT JOIN periodic_services_data psd ON psd.id=sl.service_id AND psd.is_deleted=0 "
        "LEFT JOIN account_tariff_link atl ON atl.id=sl.tariff_link_id "
        "AND atl.account_id=sl.account_id AND atl.is_deleted=0 "
        "LEFT JOIN tariffs t ON t.id=atl.tariff_id AND t.is_deleted=0 "
        "WHERE sl.account_id=%s AND sl.user_id=%s AND sl.is_deleted=0 "
        "ORDER BY sl.id LIMIT %s",
        (account_id, user_id, _MAX_SERVICES + 1),
    )
    rows = list(cursor.fetchall())
    if len(rows) > _MAX_SERVICES:
        raise ValueError("Too many NetUP service links")
    primary = [
        row["link_id"]
        for row in rows
        if row["service_type"] == 3
        and row["tariff_link_id"]
        and row["tariff_id"]
        and row["tariff_id"] > 0
        and row["linked_tariff_id"] == row["tariff_id"]
        and _status(row, now) in ("active", "blocked")
    ]
    primary_id = primary[0] if len(primary) == 1 else None
    items = []
    for row in rows:
        status = _status(row, now)
        items.append(
            {
                "link_id": row["link_id"],
                "service_id": row["service_id"],
                "name": row["tariff_name"]
                if row["link_id"] == primary_id and row["tariff_name"]
                else row["service_name"],
                "type": {2: "periodic", 3: "ip_traffic"}.get(row["service_type"], "unknown"),
                "role": "main_tariff" if row["link_id"] == primary_id else "additional",
                "status": status,
                "configured_cost": _cost(row),
                "currency": currency,
                "period": None,  # discount_periods is not readable; never assume a month.
                "starts_at": _date(row["start_date"]),
                "ends_at": _date(row["expire_date"]),
                "can_filter_charges": row["service_type"] in (2, 3),
            }
        )
    return {"items": items, "source": "NETUP", "as_of": _date(now)}


def load_services(config: NetupConfig, account_id: int, user_id: int, currency: str | None) -> dict:
    if not _SLOTS.acquire(timeout=1):
        raise TimeoutError("NetUP service capacity reached")
    connection = None
    try:
        connection = config.connect()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time=%s", (config.max_execution_ms / 1000,))
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            now = int((config.lab_as_of_utc or datetime.now(UTC)).timestamp())
            return _current(cursor, account_id, user_id, now, currency)
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
        _SLOTS.release()


def load_tariff_history(config: NetupConfig, account_id: int) -> dict:
    """Read only a bounded history; refuse unexpectedly large unindexed scans."""
    if not _SLOTS.acquire(timeout=1):
        raise TimeoutError("NetUP service capacity reached")
    connection = None
    try:
        connection = config.connect()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time=%s", (config.max_execution_ms / 1000,))
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(
                "EXPLAIN SELECT id FROM tariffs_history WHERE account_id=%s "
                "ORDER BY link_date DESC,id DESC LIMIT %s",
                (account_id, _MAX_HISTORY + 1),
            )
            plan = list(cursor.fetchall())
            if not plan or any(
                row.get("rows") is None or int(row["rows"]) > _MAX_HISTORY_EXPLAIN_ROWS
                for row in plan
            ):
                return {"items": [], "available": False, "reason": "history_scan_too_large"}
            cursor.execute(
                "SELECT id,tariff_id,tariff_name,link_date,unlink_date "
                "FROM tariffs_history WHERE account_id=%s "
                "ORDER BY link_date DESC,id DESC LIMIT %s",
                (account_id, _MAX_HISTORY + 1),
            )
            rows = list(cursor.fetchall())
            return {
                "items": [
                    {
                        "name": row["tariff_name"],
                        "starts_at": _date(row["link_date"]),
                        "ends_at": _date(row["unlink_date"]),
                        "configured_cost": None,
                        "source": "NETUP.tariffs_history",
                    }
                    for row in rows[:_MAX_HISTORY]
                ],
                "available": True,
                "truncated": len(rows) > _MAX_HISTORY,
                "reason": None,
            }
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
        _SLOTS.release()
