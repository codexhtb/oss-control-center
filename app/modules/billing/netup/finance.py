"""Bounded read-only financial history from the existing NetUP replica."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from threading import BoundedSemaphore
from typing import Any, Literal

from app.modules.billing.netup.config import NetupConfig

FinanceKind = Literal["payments", "charges", "credit"]
_SLOTS = BoundedSemaphore(4)
_CENT = Decimal("0.01")


def amount(value: Any) -> str:
    # UTM5 stores money as DOUBLE. Round only at the API boundary, never as float in Python.
    if value is None:
        raise ValueError("Missing financial amount")
    return str(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _when(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _classification(row: dict[str, Any]) -> str:
    if row["is_canceled"] or row["cancel_id"] or row["method"] == 8:
        return "canceled"
    if row["method"] == 7:
        return "credit"
    if row["payment_absolute"] < 0:
        return "unclassified_negative"
    if row["payment_absolute"] == 0:
        return "unclassified_zero"
    return "cash"


def load_finance(
    config: NetupConfig,
    account_id: int,
    start: datetime,
    end: datetime,
    kind: FinanceKind,
    page: int,
    size: int,
    account_currency: str | None,
    service_link_id: int | None = None,
) -> dict[str, Any]:
    """Read one bounded interval; one snapshot, indexed date scans and no N+1 reads."""
    if not _SLOTS.acquire(timeout=1):
        raise TimeoutError("NetUP finance capacity reached")
    connection = None
    try:
        connection = config.connect()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time=%s", (config.max_execution_ms / 1000,))
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            lo, hi = int(start.timestamp()), int(end.timestamp())
            payment_args = (lo, hi, account_id)
            charge_args = (lo, hi, account_id)
            cursor.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN method NOT IN (7,8) AND is_canceled=0 "
                "AND cancel_id=0 AND payment_absolute>0 THEN payment_absolute ELSE 0 END),0) cash, "
                "COALESCE(SUM(CASE WHEN method=7 AND is_canceled=0 AND cancel_id=0 "
                "THEN payment_absolute ELSE 0 END),0) credit, "
                "COALESCE(SUM(CASE WHEN method NOT IN (7,8) AND is_canceled=0 "
                "AND cancel_id=0 AND payment_absolute<0 THEN payment_absolute ELSE 0 END),0) "
                "unclassified_negative "
                "FROM payment_transactions WHERE actual_date >= %s AND actual_date < %s "
                "AND account_id=%s",
                payment_args,
            )
            payments = cursor.fetchone()
            cursor.execute(
                "SELECT COALESCE(SUM(CASE WHEN charge_type IN (1,2,3) "
                "THEN discount ELSE 0 END),0) charges, "
                "COALESCE(SUM(CASE WHEN charge_type IN (22,23) "
                "THEN discount ELSE 0 END),0) adjustments "
                "FROM discount_transactions_all WHERE discount_date >= %s "
                "AND discount_date < %s AND account_id=%s",
                charge_args,
            )
            charges = cursor.fetchone()
            if kind == "charges":
                filter_link = " AND slink_id=%s" if service_link_id is not None else ""
                table, date, clause, args = (
                    "discount_transactions_all",
                    "discount_date",
                    " AND charge_type IN (1,2,3,22,23)" + filter_link,
                    (*charge_args, service_link_id) if service_link_id is not None else charge_args,
                )
            else:
                table, date = "payment_transactions", "actual_date"
                clause = " AND method=7" if kind == "credit" else " AND method<>7"
                args = payment_args
            where = f"{date} >= %s AND {date} < %s AND account_id=%s{clause}"
            cursor.execute(f"SELECT COUNT(*) total FROM {table} WHERE {where}", args)
            total = int(cursor.fetchone()["total"])
            if kind == "charges":
                cursor.execute(
                    "SELECT d.id,d.discount_date,d.discount,d.charge_type,d.service_type,"
                    "d.service_id,d.slink_id,sd.service_name "
                    "FROM discount_transactions_all d "
                    "LEFT JOIN services_data sd ON sd.id=d.service_id "
                    "WHERE d.discount_date >= %s AND d.discount_date < %s "
                    f"AND d.account_id=%s{clause} "
                    "ORDER BY d.discount_date DESC,d.id DESC LIMIT %s OFFSET %s",
                    (*args, size, (page - 1) * size),
                )
            else:
                cursor.execute(
                    "SELECT p.id,p.actual_date,p.payment_absolute,p.payment_incurrency,"
                    "p.currency_id,p.method,p.is_canceled,p.cancel_id,p.burn_time,"
                    "pm.name method_name,cl.currency_brief_name currency_name "
                    "FROM payment_transactions p "
                    "LEFT JOIN payment_methods pm ON pm.id=p.method "
                    "LEFT JOIN currency_list cl ON cl.id=p.currency_id "
                    "WHERE p.actual_date >= %s AND p.actual_date < %s "
                    f"AND p.account_id=%s{clause} "
                    "ORDER BY p.actual_date DESC,p.id DESC LIMIT %s OFFSET %s",
                    (*args, size, (page - 1) * size),
                )
            rows = list(cursor.fetchall())
            items = []
            for row in rows:
                if kind == "charges":
                    items.append(
                        {
                            "id": row["id"],
                            "at": _when(row["discount_date"]),
                            "amount": amount(row["discount"]),
                            "currency": account_currency,
                            "type": {
                                1: "one_off",
                                2: "periodic",
                                3: "ip_traffic",
                                22: "charge_refund",
                                23: "adjustment",
                            }.get(row["charge_type"], "other"),
                            "service": row["service_name"] or None,
                            "details": {
                                "charge_type": row["charge_type"],
                                "service_id": row["service_id"],
                                "service_type": row["service_type"],
                                "service_link_id": row["slink_id"],
                            },
                        }
                    )
                else:
                    items.append(
                        {
                            "id": row["id"],
                            "at": _when(row["actual_date"]),
                            "amount": amount(row["payment_absolute"]),
                            "currency": account_currency,
                            "type": _classification(row),
                            "method": row["method_name"],
                            "transaction_amount": amount(row["payment_incurrency"]),
                            "transaction_currency": row["currency_name"],
                            "canceled": bool(
                                row["is_canceled"] or row["cancel_id"] or row["method"] == 8
                            ),
                            "expires_at": _when(row["burn_time"])
                            if row["burn_time"] and row["method"] == 7
                            else None,
                            "details": {
                                "method_id": row["method"],
                                "currency_id": row["currency_id"],
                                "cancel_id": row["cancel_id"],
                            },
                        }
                    )
            return {
                "summary": {key: amount(value) for key, value in {**payments, **charges}.items()},
                "currency": account_currency,
                "items": items,
                "total": total,
                "page": page,
                "size": size,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
        _SLOTS.release()
