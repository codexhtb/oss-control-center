"""Bounded, parameterized UTM5 reads within one consistent read-only snapshot."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from app.modules.billing.netup.dto import (
    NetupAccount,
    NetupGroup,
    NetupGroupRow,
    NetupRange,
    NetupService,
    NetupSubscriber,
)
from app.modules.billing.provider import BillingAmbiguous, BillingIncomplete, BillingInvalidAccount

MAX_ACCOUNTS = 50
MAX_SERVICES = 50
MAX_GROUPS_PER_SERVICE = 20
MAX_ROWS_PER_GROUP = 100
MAX_TOTAL_GROUPS = 200
MAX_TOTAL_GROUP_ROWS = 2000
MAX_RANGES_PER_POOL = 50
LOOKUP_DEADLINE_SECONDS = 15
CANONICAL_ACCOUNT = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def parse_account_no(account_no: str) -> int:
    """Never coerce a formatted/zero-prefixed account to a different UTM5 key."""
    if not CANONICAL_ACCOUNT.fullmatch(account_no) or len(account_no) > 10:
        raise BillingInvalidAccount from None
    value = int(account_no)
    if value > 2_147_483_647:
        raise BillingInvalidAccount from None
    return value


class NetupRepository:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        max_statement_ms: int = 3000,
        lab_as_of_utc: datetime | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._max_statement_ms = max_statement_ms
        self._lab_as_of_utc = lab_as_of_utc

    def lookup_by_account(self, account_no: str) -> NetupSubscriber | None:
        account_id = parse_account_no(account_no)
        connection = self._connection_factory()
        deadline = monotonic() + LOOKUP_DEADLINE_SECONDS
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET SESSION max_statement_time=%s", (self._max_statement_ms / 1000,)
                )
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                result = self._load(cursor, account_id, deadline)
            return result
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()

    @staticmethod
    def _select(cursor: Any, sql: str, params: tuple[Any, ...], deadline: float) -> list[dict]:
        if monotonic() >= deadline:
            raise TimeoutError("NetUP lookup deadline exceeded")
        cursor.execute(sql, params)
        return list(cursor.fetchall())

    def _load(self, cursor: Any, account_id: int, deadline: float) -> NetupSubscriber | None:
        select = self._select
        account = select(
            cursor,
            "SELECT id FROM accounts WHERE id=%s AND is_deleted=0",
            (account_id,),
            deadline,
        )
        if not account:
            return None
        owners = select(
            cursor,
            "SELECT DISTINCT u.id FROM users_accounts ua "
            "JOIN users u ON u.id=ua.uid AND u.is_deleted=0 "
            "WHERE ua.account_id=%s AND ua.is_deleted=0 ORDER BY u.id LIMIT 2",
            (account_id,),
            deadline,
        )
        if not owners:
            raise BillingIncomplete from None
        if len(owners) > 1:
            raise BillingAmbiguous from None
        user_id = owners[0]["id"]
        users = select(
            cursor,
            "SELECT id,basic_account,full_name,is_juridical,mobile_telephone,"
            "work_telephone,home_telephone,email,actual_address "
            "FROM users WHERE id=%s AND is_deleted=0",
            (user_id,),
            deadline,
        )
        if len(users) != 1:
            raise BillingIncomplete from None
        user = users[0]
        account_rows = select(
            cursor,
            "SELECT DISTINCT a.id FROM users_accounts ua "
            "JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0 "
            "WHERE ua.uid=%s AND ua.is_deleted=0 ORDER BY a.id LIMIT 51",
            (user_id,),
            deadline,
        )
        if not account_rows or len(account_rows) > MAX_ACCOUNTS:
            raise BillingIncomplete from None
        accounts = tuple(
            NetupAccount(row["id"], row["id"] == user["basic_account"]) for row in account_rows
        )
        if account_id not in {item.account_id for item in accounts}:
            raise BillingIncomplete from None
        if sum(item.is_primary for item in accounts) != 1:
            raise BillingIncomplete from None
        as_of = int((self._lab_as_of_utc or datetime.now(UTC)).timestamp())
        services: list[NetupService] = []
        total_groups = total_group_rows = 0
        range_cache: dict[int, tuple[NetupRange, ...]] = {}
        for item in accounts:
            rows = select(
                cursor,
                "SELECT sl.id,sl.account_id,sd.service_name,sd.service_type "
                "FROM service_links sl "
                "JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0 "
                "JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
                "WHERE sl.user_id=%s AND sl.account_id=%s AND sl.is_deleted=0 "
                "AND sd.service_type IN (2,3) AND psl.is_planned=0 AND psl.need_del=0 "
                "AND psl.start_date<=%s AND psl.expire_date>%s "
                "ORDER BY sl.id LIMIT 51",
                (user_id, item.account_id, as_of, as_of),
                deadline,
            )
            if len(rows) > MAX_SERVICES or len(services) + len(rows) > MAX_SERVICES:
                raise BillingIncomplete from None
            for row in rows:
                groups = (
                    self._groups(cursor, row["id"], range_cache, deadline)
                    if row["service_type"] == 3
                    else ()
                )
                total_groups += len(groups)
                total_group_rows += sum(len(group.rows) for group in groups)
                if total_groups > MAX_TOTAL_GROUPS or total_group_rows > MAX_TOTAL_GROUP_ROWS:
                    raise BillingIncomplete from None
                services.append(
                    NetupService(
                        link_id=row["id"],
                        account_id=item.account_id,
                        name=row["service_name"],
                        service_type=row["service_type"],
                        groups=groups,
                    )
                )
        return NetupSubscriber(
            user_id=user_id,
            requested_account_id=account_id,
            full_name=user["full_name"],
            is_juridical=bool(user["is_juridical"]),
            mobile_phone=user["mobile_telephone"],
            work_phone=user["work_telephone"],
            home_phone=user["home_telephone"],
            email=user["email"],
            actual_address=user["actual_address"],
            accounts=accounts,
            services=tuple(services),
            fetched_at=datetime.now(UTC),
            source_as_of=self._lab_as_of_utc,
        )

    def _groups(
        self,
        cursor: Any,
        link_id: int,
        range_cache: dict[int, tuple[NetupRange, ...]],
        deadline: float,
    ) -> tuple[NetupGroup, ...]:
        select = self._select
        references = select(
            cursor,
            "SELECT ip_group_id FROM iptraffic_service_links WHERE id=%s AND is_deleted=0 LIMIT 21",
            (link_id,),
            deadline,
        )
        if not references or len(references) > MAX_GROUPS_PER_SERVICE:
            raise BillingIncomplete from None
        groups = []
        for reference in references:
            group_id = reference["ip_group_id"]
            rows = select(
                cursor,
                "SELECT g.id,g.ip_type,g.ip,g.mask,g.ip_ext,g.mask_ext,g.pool_id,"
                "g.switch_id,g.port_id,g.vlan_id,s.name AS switch_name "
                "FROM ip_groups g LEFT JOIN switches s ON s.id=g.switch_id AND s.is_deleted=0 "
                "WHERE g.ip_group_id=%s AND g.is_deleted=0 ORDER BY g.id LIMIT 101",
                (group_id,),
                deadline,
            )
            if not rows or len(rows) > MAX_ROWS_PER_GROUP:
                raise BillingIncomplete from None
            group_rows = []
            for row in rows:
                pool_id = row["pool_id"]
                ranges = ()
                if pool_id > 0:
                    if pool_id not in range_cache:
                        pool = select(
                            cursor,
                            "SELECT id FROM dhcp_pool_info WHERE id=%s AND is_deleted=0",
                            (pool_id,),
                            deadline,
                        )
                        if len(pool) != 1:
                            raise BillingIncomplete from None
                        raw_ranges = select(
                            cursor,
                            "SELECT id,first_ip,last_ip FROM dhcp_pool_ranges "
                            "WHERE pool_id=%s AND is_deleted=0 ORDER BY id LIMIT 51",
                            (pool_id,),
                            deadline,
                        )
                        if not raw_ranges or len(raw_ranges) > MAX_RANGES_PER_POOL:
                            raise BillingIncomplete from None
                        range_cache[pool_id] = tuple(
                            NetupRange(r["id"], r["first_ip"], r["last_ip"]) for r in raw_ranges
                        )
                    ranges = range_cache[pool_id]
                group_rows.append(
                    NetupGroupRow(
                        row_id=row["id"],
                        ip_type=row["ip_type"],
                        ip=row["ip"],
                        mask=row["mask"],
                        ip_ext=row["ip_ext"],
                        mask_ext=row["mask_ext"],
                        pool_id=pool_id,
                        switch_id=row["switch_id"],
                        switch_name=row["switch_name"],
                        port_id=row["port_id"],
                        vlan_id=row["vlan_id"],
                        ranges=ranges,
                    )
                )
            groups.append(NetupGroup(group_id, tuple(group_rows)))
        return tuple(groups)
