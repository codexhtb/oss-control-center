"""Bounded, parameterized UTM5 reads within one consistent read-only snapshot."""

import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from threading import BoundedSemaphore
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
from app.modules.billing.provider import (
    BillingAmbiguous,
    BillingIncomplete,
    BillingInvalidAccount,
    BillingUnavailable,
    SubscriberSearchItem,
    SubscriberSearchResult,
)

MAX_ACCOUNTS = 50
MAX_SEARCH_RESULTS = 20
MAX_SERVICES = 50
MAX_GROUPS_PER_SERVICE = 20
MAX_ROWS_PER_GROUP = 100
MAX_TOTAL_GROUPS = 200
MAX_TOTAL_GROUP_ROWS = 2000
MAX_EXPANDED_RANGES = 5000
MAX_RANGES_PER_POOL = 50
MAX_POOLS = 100
LOOKUP_DEADLINE_SECONDS = 15
LOOKUP_SLOTS = BoundedSemaphore(8)
CANONICAL_ACCOUNT = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def _like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


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
        deadline = monotonic() + LOOKUP_DEADLINE_SECONDS
        if not LOOKUP_SLOTS.acquire(timeout=1):
            raise BillingUnavailable from None
        try:
            connection = self._connection_factory()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET SESSION max_statement_time=%s", (self._max_statement_ms / 1000,)
                    )
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    return self._load(cursor, account_id, deadline)
            finally:
                try:
                    connection.rollback()
                finally:
                    connection.close()
        finally:
            LOOKUP_SLOTS.release()

    def search_subscribers(self, query: str) -> SubscriberSearchResult:
        term = " ".join(query.split())
        if not term or len(term) > 100 or any(ord(char) < 32 for char in term):
            raise ValueError("invalid_subscriber_search")
        deadline = monotonic() + LOOKUP_DEADLINE_SECONDS
        if not LOOKUP_SLOTS.acquire(timeout=1):
            raise BillingUnavailable from None
        try:
            connection = self._connection_factory()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET SESSION max_statement_time=%s", (self._max_statement_ms / 1000,)
                    )
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    return self._search(cursor, term, deadline)
            finally:
                try:
                    connection.rollback()
                finally:
                    connection.close()
        finally:
            LOOKUP_SLOTS.release()

    def _search(self, cursor: Any, term: str, deadline: float) -> SubscriberSearchResult:
        select = self._select
        found: dict[int, dict] = {}
        if CANONICAL_ACCOUNT.fullmatch(term) and len(term) <= 10 and int(term) <= 2_147_483_647:
            rows = select(
                cursor,
                "SELECT u.id,u.full_name,u.login FROM accounts a "
                "JOIN users_accounts ua ON ua.account_id=a.id AND ua.is_deleted=0 "
                "JOIN users u ON u.id=ua.uid AND u.is_deleted=0 "
                "WHERE a.id=%s AND a.is_deleted=0 ORDER BY u.id LIMIT 21",
                (int(term),),
                deadline,
            )
            found.update((int(row["id"]), row) for row in rows)

        predicates = ["u.login=%s"]
        params: list[Any] = [term]
        if len(term) >= 3:
            predicates.append("u.login LIKE %s ESCAPE '!' ")
            params.append(_like(term) + "%")
            tokens = term.split()
            if len(tokens) <= 5:
                predicates.extend(["u.full_name LIKE %s ESCAPE '!' "] * len(tokens))
                params.extend("%" + _like(token) + "%" for token in tokens)
                name_clause = " AND ".join(predicates[2:])
                predicates = predicates[:2] + [f"({name_clause})"]
        rows = select(
            cursor,
            "SELECT u.id,u.full_name,u.login FROM users u "
            "WHERE u.is_deleted=0 AND EXISTS ("
            "SELECT 1 FROM users_accounts ua JOIN accounts a ON a.id=ua.account_id "
            "AND a.is_deleted=0 WHERE ua.uid=u.id AND ua.is_deleted=0) "
            f"AND ({' OR '.join(predicates)}) "
            "ORDER BY CASE WHEN u.login=%s THEN 0 "
            "WHEN u.login LIKE %s ESCAPE '!' THEN 1 ELSE 2 END,u.id LIMIT 21",
            (*params, term, _like(term) + "%"),
            deadline,
        )
        found.update((int(row["id"]), row) for row in rows)
        has_more = len(rows) > MAX_SEARCH_RESULTS or len(found) > MAX_SEARCH_RESULTS
        users = list(found.values())[:MAX_SEARCH_RESULTS]
        if not users:
            return SubscriberSearchResult(items=[])
        user_ids = [int(row["id"]) for row in users]
        account_rows = select(
            cursor,
            "SELECT DISTINCT ua.uid,a.id AS account_id FROM users_accounts ua "
            "JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0 "
            f"WHERE ua.uid IN ({self._placeholders(user_ids)}) AND ua.is_deleted=0 "
            "ORDER BY ua.uid,a.id LIMIT 1001",
            tuple(user_ids),
            deadline,
        )
        if len(account_rows) > 1000:
            raise BillingIncomplete from None
        account_map: dict[int, list[str]] = defaultdict(list)
        for row in account_rows:
            account_map[int(row["uid"])].append(str(row["account_id"]))
        return SubscriberSearchResult(
            items=[
                SubscriberSearchItem(
                    subscriber_id=str(row["id"]),
                    full_name=row["full_name"],
                    login=row["login"],
                    account_nos=account_map[int(row["id"])],
                )
                for row in users
                if account_map[int(row["id"])]
            ],
            has_more=has_more,
        )

    @staticmethod
    def _select(cursor: Any, sql: str, params: tuple[Any, ...], deadline: float) -> list[dict]:
        if monotonic() >= deadline:
            raise TimeoutError("NetUP lookup deadline exceeded")
        cursor.execute(sql, params)
        return list(cursor.fetchall())

    @staticmethod
    def _placeholders(values: list[int]) -> str:
        if not values:
            raise BillingIncomplete from None
        return ",".join(["%s"] * len(values))

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
            "work_telephone,home_telephone,email,actual_address,building,binded_currency_code,"
            "(SELECT currency_brief_name FROM currency_list "
            "WHERE id=users.binded_currency_code AND is_deleted=0 LIMIT 1) "
            "AS currency_brief_name "
            "FROM users WHERE id=%s AND is_deleted=0",
            (user_id,),
            deadline,
        )
        if len(users) != 1:
            raise BillingIncomplete from None
        user = users[0]
        account_rows = select(
            cursor,
            "SELECT DISTINCT a.id,a.balance,a.credit,a.is_blocked,a.int_status "
            "FROM users_accounts ua "
            "JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0 "
            "WHERE ua.uid=%s AND ua.is_deleted=0 ORDER BY a.id LIMIT 51",
            (user_id,),
            deadline,
        )
        if not account_rows or len(account_rows) > MAX_ACCOUNTS:
            raise BillingIncomplete from None
        accounts = tuple(
            NetupAccount(
                row["id"],
                row["id"] == user["basic_account"],
                row["balance"],
                row["credit"],
                row["is_blocked"],
                row["int_status"],
            )
            for row in account_rows
        )
        if account_id not in {item.account_id for item in accounts}:
            raise BillingIncomplete from None
        if sum(item.is_primary for item in accounts) != 1:
            raise BillingIncomplete from None
        as_of = int((self._lab_as_of_utc or datetime.now(UTC)).timestamp())
        account_ids = [item.account_id for item in accounts]
        service_rows = select(
            cursor,
            "SELECT sl.id,sl.account_id,sd.service_name,sd.service_type,"
            "psl.is_blocked AS billing_blocked,t.name AS tariff_name "
            "FROM service_links sl "
            "JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0 "
            "LEFT JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
            "LEFT JOIN account_tariff_link atl ON atl.id=sl.tariff_link_id "
            "AND atl.account_id=sl.account_id AND atl.tariff_id=sd.tariff_id "
            "AND atl.is_deleted=0 "
            "LEFT JOIN tariffs t ON t.id=atl.tariff_id AND t.is_deleted=0 "
            f"WHERE sl.user_id=%s AND sl.account_id IN ({self._placeholders(account_ids)}) "
            "AND sl.is_deleted=0 "
            "AND (sd.service_type NOT IN (2,3) OR "
            "(psl.is_planned=0 AND psl.need_del=0 "
            "AND psl.start_date<=%s AND psl.expire_date>%s)) "
            "ORDER BY sl.id LIMIT 51",
            (user_id, *account_ids, as_of, as_of),
            deadline,
        )
        if len(service_rows) > MAX_SERVICES:
            raise BillingIncomplete from None
        ip_services = [row["id"] for row in service_rows if row["service_type"] == 3]
        references: dict[int, list[int]] = defaultdict(list)
        if ip_services:
            reference_rows = select(
                cursor,
                "SELECT id,ip_group_id FROM iptraffic_service_links "
                f"WHERE id IN ({self._placeholders(ip_services)}) AND is_deleted=0 "
                "ORDER BY id,ip_group_id LIMIT 1001",
                tuple(ip_services),
                deadline,
            )
            if len(reference_rows) > MAX_SERVICES * MAX_GROUPS_PER_SERVICE:
                raise BillingIncomplete from None
            for row in reference_rows:
                references[row["id"]].append(row["ip_group_id"])
            if any(
                not references[item] or len(references[item]) > MAX_GROUPS_PER_SERVICE
                for item in ip_services
            ):
                raise BillingIncomplete from None
        group_ids = sorted({group for values in references.values() for group in values})
        if len(group_ids) > MAX_TOTAL_GROUPS:
            raise BillingIncomplete from None
        group_rows: dict[int, list[dict]] = defaultdict(list)
        if group_ids:
            raw_rows = select(
                cursor,
                "SELECT g.id,g.ip_group_id,g.ip_type,g.ip,g.mask,g.ip_ext,g.mask_ext,g.pool_id,"
                "g.switch_id,g.port_id,g.vlan_id,s.name AS switch_name "
                "FROM ip_groups g LEFT JOIN switches s ON s.id=g.switch_id AND s.is_deleted=0 "
                f"WHERE g.ip_group_id IN ({self._placeholders(group_ids)}) "
                "AND g.is_deleted=0 ORDER BY g.ip_group_id,g.id LIMIT 2001",
                tuple(group_ids),
                deadline,
            )
            if len(raw_rows) > MAX_TOTAL_GROUP_ROWS:
                raise BillingIncomplete from None
            for row in raw_rows:
                group_rows[row["ip_group_id"]].append(row)
            if any(
                not group_rows[item] or len(group_rows[item]) > MAX_ROWS_PER_GROUP
                for item in group_ids
            ):
                raise BillingIncomplete from None
        pool_ids = (
            sorted({row["pool_id"] for row in raw_rows if row["pool_id"] > 0}) if group_ids else []
        )
        if len(pool_ids) > MAX_POOLS:
            raise BillingIncomplete from None
        range_cache: dict[int, tuple[NetupRange, ...]] = {}
        if pool_ids:
            pools = select(
                cursor,
                "SELECT id FROM dhcp_pool_info "
                f"WHERE id IN ({self._placeholders(pool_ids)}) AND is_deleted=0 LIMIT 101",
                tuple(pool_ids),
                deadline,
            )
            if len(pools) != len(pool_ids):
                raise BillingIncomplete from None
            range_rows = select(
                cursor,
                "SELECT id,pool_id,first_ip,last_ip FROM dhcp_pool_ranges "
                f"WHERE pool_id IN ({self._placeholders(pool_ids)}) AND is_deleted=0 "
                "ORDER BY pool_id,id LIMIT 5001",
                tuple(pool_ids),
                deadline,
            )
            if len(range_rows) > MAX_POOLS * MAX_RANGES_PER_POOL:
                raise BillingIncomplete from None
            ranges: dict[int, list[NetupRange]] = defaultdict(list)
            for row in range_rows:
                ranges[row["pool_id"]].append(
                    NetupRange(row["id"], row["first_ip"], row["last_ip"])
                )
            if any(
                not ranges[item] or len(ranges[item]) > MAX_RANGES_PER_POOL for item in pool_ids
            ):
                raise BillingIncomplete from None
            range_cache = {item: tuple(ranges[item]) for item in pool_ids}
        services: list[NetupService] = []
        total_groups = total_group_rows = total_ranges = 0
        for row in service_rows:
            groups = tuple(
                NetupGroup(
                    group_id,
                    tuple(
                        NetupGroupRow(
                            row_id=item["id"],
                            ip_type=item["ip_type"],
                            ip=item["ip"],
                            mask=item["mask"],
                            ip_ext=item["ip_ext"],
                            mask_ext=item["mask_ext"],
                            pool_id=item["pool_id"],
                            switch_id=item["switch_id"],
                            switch_name=item["switch_name"],
                            port_id=item["port_id"],
                            vlan_id=item["vlan_id"],
                            ranges=range_cache.get(item["pool_id"], ()),
                        )
                        for item in group_rows[group_id]
                    ),
                )
                for group_id in references[row["id"]]
            )
            total_groups += len(groups)
            total_group_rows += sum(len(group.rows) for group in groups)
            total_ranges += sum(len(item.ranges) for group in groups for item in group.rows)
            if (
                total_groups > MAX_TOTAL_GROUPS
                or total_group_rows > MAX_TOTAL_GROUP_ROWS
                or total_ranges > MAX_EXPANDED_RANGES
            ):
                raise BillingIncomplete from None
            services.append(
                NetupService(
                    link_id=row["id"],
                    account_id=row["account_id"],
                    name=row["service_name"],
                    service_type=row["service_type"],
                    groups=groups,
                    tariff_name=row["tariff_name"],
                    billing_blocked=row["billing_blocked"],
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
            building=user["building"],
            currency_code=user.get("binded_currency_code"),
            currency=user.get("currency_brief_name"),
        )
