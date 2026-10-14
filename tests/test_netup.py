"""Synthetic contract and optional read-only lab checks; never prints source rows."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.modules.billing.mock import MockBillingProvider
from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.dto import (
    NetupAccount,
    NetupGroup,
    NetupGroupRow,
    NetupRange,
    NetupService,
    NetupSubscriber,
)
from app.modules.billing.netup.normalizer import normalize_netup
from app.modules.billing.netup.provider import NetUPBillingProvider
from app.modules.billing.netup.repository import NetupRepository, parse_account_no
from app.modules.billing.provider import (
    BillingAmbiguous,
    BillingIncomplete,
    BillingInvalidAccount,
    BillingUnavailable,
)
from app.modules.billing.quality import DataQuality
from app.modules.billing.service import validate_snapshot_structure
from app.modules.identity.crypto import hash_password
from app.modules.network.models import AccessType
from app.modules.tickets.models import ConnectionSnapshot, SubscriberSnapshot


def synthetic_group(*, conflict: bool = False) -> NetupGroup:
    pool = NetupGroupRow(
        row_id=11,
        ip_type=0,
        ip=0,
        mask=0,
        ip_ext=0,
        mask_ext=0,
        pool_id=3,
        switch_id=7,
        switch_name="synthetic-switch",
        port_id=2,
        vlan_id=100,
        ranges=(NetupRange(5, 1000, 2000),),
    )
    address = NetupGroupRow(
        row_id=12,
        ip_type=4,
        ip=123456,
        mask=654321,
        ip_ext=0,
        mask_ext=0,
        pool_id=0,
        switch_id=0,
        switch_name=None,
        port_id=0,
        vlan_id=0,
        ranges=(),
    )
    rows = [pool, address]
    if conflict:
        rows.append(
            NetupGroupRow(
                row_id=13,
                ip_type=0,
                ip=0,
                mask=0,
                ip_ext=0,
                mask_ext=0,
                pool_id=3,
                switch_id=8,
                switch_name="synthetic-switch-2",
                port_id=3,
                vlan_id=101,
                ranges=(NetupRange(5, 1000, 2000),),
            )
        )
    return NetupGroup(9, tuple(rows))


def synthetic_subscriber(*, conflict: bool = False) -> NetupSubscriber:
    return NetupSubscriber(
        user_id=101,
        requested_account_id=202,
        full_name="Synthetic Person",
        is_juridical=False,
        mobile_phone="000-000",
        work_phone="",
        home_phone="",
        email="",
        actual_address="Synthetic address",
        accounts=(NetupAccount(202, True), NetupAccount(203, False)),
        services=(
            NetupService(301, 202, "Synthetic Internet", 3, (synthetic_group(conflict=conflict),)),
            NetupService(302, 203, "Synthetic ancillary", 2, ()),
        ),
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("bad", ["0002", " 2", "2 ", "0", "-2", "2x", "2147483648", ""])
def test_account_format_preserves_identity(bad: str) -> None:
    with pytest.raises(BillingInvalidAccount):
        parse_account_no(bad)
    assert parse_account_no("202") == 202


def test_normalizer_preserves_accounts_services_groups_and_origins() -> None:
    card = normalize_netup(synthetic_subscriber())
    assert card.billing_account == "202" and card.source == "NETUP"
    assert len(card.accounts) == 2 and card.accounts[1].is_primary is False
    assert [service.account_id for service in card.services] == ["202", "203"]
    assert len(card.connections) == 1
    connection = card.connections[0]
    assert connection.access_type == AccessType.UNKNOWN and connection.onu_id is None
    assert connection.service_id == "301" and connection.ip_group == "9"
    assert len(connection.ip_groups[0].rows) == 2
    assert connection.ip_groups[0].rows[0].dhcp_pool_id == "3"
    assert connection.ip_groups[0].rows[0].ip_raw == 0
    assert connection.ip_groups[0].rows[0].pool_id_raw == 3
    assert connection.ip_groups[0].rows[1].pool_id_raw == 0
    assert connection.ip_groups[0].rows[1].switch_id_raw == 0
    assert connection.ip_groups[0].rows[1].port_id == 0
    assert connection.ip_groups[0].rows[1].field_meta["port_id"].quality == DataQuality.UNKNOWN
    assert connection.ip_groups[0].rows[1].ip_raw == 123456
    assert connection.ip_groups[0].rows[1].field_meta["ip_raw"].quality == DataQuality.UNKNOWN
    assert connection.device == "synthetic-switch" and connection.vlan == "100"
    assert connection.field_meta["access_type"].quality == DataQuality.UNKNOWN
    assert card.field_meta["actual_address"].source == "NETUP"
    assert card.services[0].tariff_name is None
    assert card.services[0].billing_active is True
    assert card.financial_accounts[0].balance is None


def test_tariff_and_finance_remain_separate_billing_observations() -> None:
    raw = synthetic_subscriber()
    raw = NetupSubscriber(
        **{
            **raw.__dict__,
            "accounts": (NetupAccount(202, True, 12.5, 1.0, 1),),
            "services": (
                NetupService(
                    301,
                    202,
                    "Synthetic Internet",
                    3,
                    (synthetic_group(),),
                    "Synthetic tariff",
                    0,
                ),
            ),
        }
    )
    card = normalize_netup(raw)
    assert card.services[0].tariff_name == "Synthetic tariff"
    assert card.services[0].billing_active is True
    assert card.services[0].billing_blocked is False
    assert card.financial_accounts[0].balance == 12.5
    assert card.financial_accounts[0].account_blocked is True
    assert card.connections[0].access_type == AccessType.UNKNOWN
    assert card.financial_accounts[0].field_meta["balance"].source == "NETUP"


def test_multiple_topologies_remain_conflict_without_losing_rows() -> None:
    card = normalize_netup(synthetic_subscriber(conflict=True))
    connection = card.connections[0]
    assert len(connection.ip_groups[0].rows) == 3
    assert connection.device is None and connection.port is None and connection.vlan is None
    assert connection.field_meta["device"].quality == DataQuality.CONFLICT
    assert connection.field_meta["port"].quality == DataQuality.CONFLICT
    assert connection.field_meta["vlan"].quality == DataQuality.CONFLICT


def test_partial_topology_row_cannot_hide_conflicting_switch() -> None:
    raw = synthetic_subscriber()
    first, second = raw.services
    partial = NetupGroupRow(
        row_id=14,
        ip_type=0,
        ip=0,
        mask=0,
        ip_ext=0,
        mask_ext=0,
        pool_id=0,
        switch_id=8,
        switch_name="other-synthetic-switch",
        port_id=0,
        vlan_id=0,
        ranges=(),
    )
    group = NetupGroup(9, (*first.groups[0].rows, partial))
    raw = NetupSubscriber(
        **{**raw.__dict__, "services": (NetupService(301, 202, first.name, 3, (group,)), second)}
    )
    connection = normalize_netup(raw).connections[0]
    assert len(connection.ip_groups[0].rows) == 3
    assert connection.device is None and connection.port is None and connection.vlan is None
    assert connection.field_meta["device"].quality == DataQuality.CONFLICT


def test_one_service_can_preserve_multiple_group_keys() -> None:
    raw = synthetic_subscriber()
    first, second = raw.services
    second_group = NetupGroup(10, synthetic_group().rows)
    multi = NetupService(
        first.link_id,
        first.account_id,
        first.name,
        first.service_type,
        (first.groups[0], second_group),
    )
    raw = NetupSubscriber(**{**raw.__dict__, "services": (multi, second)})
    connection = normalize_netup(raw).connections[0]
    assert len(connection.ip_groups) == 2
    assert connection.ip_group is None
    assert connection.field_meta["ip_group"].quality == DataQuality.CONFLICT


def test_multiple_phones_are_explicit_conflict() -> None:
    raw = synthetic_subscriber()
    raw = NetupSubscriber(**{**raw.__dict__, "work_phone": "111-111"})
    card = normalize_netup(raw)
    assert card.phone is None and card.field_meta["phone"].quality == DataQuality.CONFLICT
    assert [item.type for item in card.contacts] == ["mobile", "work"]
    assert [item.value for item in card.contacts] == ["000-000", "111-111"]
    assert all(item.quality == DataQuality.CONFLICT for item in card.contacts)
    assert all(item.source.startswith("NETUP.users.") for item in card.contacts)
    assert all(item.observed_at == raw.fetched_at for item in card.contacts)


def test_repeated_identical_phone_is_not_a_conflict() -> None:
    raw = synthetic_subscriber()
    raw = NetupSubscriber(**{**raw.__dict__, "work_phone": raw.mobile_phone})
    card = normalize_netup(raw)
    assert card.phone == "000-000"
    assert card.field_meta["phone"].quality == DataQuality.KNOWN
    assert len(card.contacts) == 2
    assert all(item.quality == DataQuality.KNOWN for item in card.contacts)


def test_long_phone_is_preserved_in_contacts_without_flat_truncation() -> None:
    raw = synthetic_subscriber()
    raw = NetupSubscriber(**{**raw.__dict__, "mobile_phone": "S" * 55})
    card = normalize_netup(raw)
    assert card.phone is None and card.field_meta["phone"].quality == DataQuality.CONFLICT
    assert card.contacts[0].value == "S" * 55
    with pytest.raises(BillingIncomplete):
        normalize_netup(NetupSubscriber(**{**raw.__dict__, "mobile_phone": "S" * 256}))


def test_historical_lab_observation_is_stale_without_changing_read_time() -> None:
    raw = synthetic_subscriber()
    observed = raw.fetched_at - timedelta(days=3)
    raw = NetupSubscriber(**{**raw.__dict__, "source_as_of": observed})
    card = normalize_netup(raw)
    assert card.fetched_at == raw.fetched_at
    assert card.field_meta["billing_account"].observed_at == observed
    assert card.field_meta["billing_account"].quality == DataQuality.STALE
    assert card.contacts[0].quality == DataQuality.STALE
    assert card.connections[0].ip_groups[0].rows[1].field_meta["ip_raw"].quality == (
        DataQuality.UNKNOWN
    )


def test_existing_mock_provider_contract_remains_valid() -> None:
    card = MockBillingProvider().lookup_by_account("MOCK-ETH-001")
    assert card is not None and card.billing_account == "MOCK-ETH-001"
    assert card.accounts == [] and card.connections[0].ip_groups == []
    assert card.contacts == []
    assert card.connections[0].service_id is None
    validate_snapshot_structure(card)


def test_snapshot_writer_rejects_cross_service_and_duplicate_groups() -> None:
    card = normalize_netup(synthetic_subscriber())
    validate_snapshot_structure(card)
    card.connections[0].service_id = "absent-service"
    with pytest.raises(ValueError, match="absent service"):
        validate_snapshot_structure(card)
    card.connections[0].service_id = card.services[0].service_id
    card.connections[0].ip_groups.append(card.connections[0].ip_groups[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="duplicate group"):
        validate_snapshot_structure(card)


class FakeCursor:
    def __init__(self, owners: list[dict], *, account_exists: bool = True) -> None:
        self.owners = owners
        self.account_exists = account_exists
        self.calls: list[tuple[str, tuple | None]] = []
        self.rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.calls.append((sql, params))
        if sql.startswith(("SET SESSION", "SET TRANSACTION", "START TRANSACTION")):
            self.rows = []
        elif sql.startswith("SELECT id FROM accounts"):
            self.rows = [{"id": 202}] if self.account_exists else []
        elif sql.startswith("SELECT DISTINCT u.id FROM users_accounts"):
            self.rows = self.owners
        else:
            raise AssertionError("Unexpected query")

    def fetchall(self) -> list[dict]:
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.fake_cursor

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("account_exists", "owners", "expected"),
    [
        (False, [], "missing"),
        (True, [], "incomplete"),
        (True, [{"id": 101}, {"id": 102}], "ambiguous"),
    ],
)
def test_repository_distinguishes_missing_deleted_and_ambiguous(
    account_exists: bool, owners: list[dict], expected: str
) -> None:
    cursor = FakeCursor(owners, account_exists=account_exists)
    connection = FakeConnection(cursor)
    repository = NetupRepository(lambda: connection)
    if expected == "missing":
        assert repository.lookup_by_account("202") is None
    else:
        with pytest.raises(BillingIncomplete if expected == "incomplete" else BillingAmbiguous):
            repository.lookup_by_account("202")
    assert connection.rolled_back and connection.closed
    assert cursor.calls[0][0] == "SET SESSION max_statement_time=%s"
    assert cursor.calls[1][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert cursor.calls[2][0] == "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
    assert cursor.calls[3][1] == (202,)
    assert "%s" in cursor.calls[3][0]
    assert "is_deleted=0" in cursor.calls[3][0]


def test_repository_loads_multiple_accounts_links_and_group_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompleteCursor(FakeCursor):
        def execute(self, sql: str, params: tuple | None = None) -> None:
            self.calls.append((sql, params))
            if sql.startswith(("SET SESSION", "SET TRANSACTION", "START TRANSACTION")):
                self.rows = []
            elif sql.startswith("SELECT id FROM accounts"):
                self.rows = [{"id": 202}]
            elif sql.startswith("SELECT DISTINCT u.id FROM users_accounts"):
                self.rows = [{"id": 101}]
            elif sql.startswith("SELECT id,basic_account"):
                self.rows = [
                    {
                        "id": 101,
                        "basic_account": 202,
                        "full_name": "Synthetic Person",
                        "is_juridical": 0,
                        "mobile_telephone": "000-000",
                        "work_telephone": "",
                        "home_telephone": "",
                        "email": "",
                        "actual_address": "Synthetic address",
                    }
                ]
            elif sql.startswith("SELECT DISTINCT a.id,a.balance"):
                self.rows = [
                    {"id": 202, "balance": 12.5, "credit": 0.0, "is_blocked": 1},
                    {"id": 203, "balance": 0.0, "credit": 0.0, "is_blocked": 0},
                ]
            elif sql.startswith("SELECT sl.id"):
                assert params[1:3] == (202, 203)
                self.rows = [
                    {
                        "id": 301,
                        "account_id": 202,
                        "service_name": "Synthetic Internet",
                        "service_type": 3,
                        "tariff_name": "Synthetic tariff",
                        "billing_blocked": 0,
                    },
                    {
                        "id": 302,
                        "account_id": 203,
                        "service_name": "Synthetic ancillary",
                        "service_type": 2,
                        "tariff_name": None,
                        "billing_blocked": 0,
                    },
                ]
            elif sql.startswith("SELECT id,ip_group_id"):
                self.rows = [{"id": 301, "ip_group_id": 9}]
            elif sql.startswith("SELECT g.id"):
                self.rows = [
                    {
                        "id": 11,
                        "ip_group_id": 9,
                        "ip_type": 0,
                        "ip": 0,
                        "mask": 0,
                        "ip_ext": 0,
                        "mask_ext": 0,
                        "pool_id": 3,
                        "switch_id": 7,
                        "switch_name": "synthetic-switch",
                        "port_id": 2,
                        "vlan_id": 100,
                    },
                    {
                        "id": 12,
                        "ip_group_id": 9,
                        "ip_type": 4,
                        "ip": 123456,
                        "mask": 654321,
                        "ip_ext": 0,
                        "mask_ext": 0,
                        "pool_id": 0,
                        "switch_id": 0,
                        "switch_name": None,
                        "port_id": 0,
                        "vlan_id": 0,
                    },
                ]
            elif sql.startswith("SELECT id FROM dhcp_pool_info"):
                self.rows = [{"id": 3}]
            elif sql.startswith("SELECT id,pool_id,first_ip,last_ip"):
                self.rows = [{"id": 5, "pool_id": 3, "first_ip": 1000, "last_ip": 2000}]
            else:
                raise AssertionError("Unexpected query")

    cursor = CompleteCursor([])
    connection = FakeConnection(cursor)
    raw = NetupRepository(lambda: connection).lookup_by_account("202")
    assert raw is not None
    assert len(raw.accounts) == 2 and len(raw.services) == 2
    assert len(raw.services[0].groups) == 1
    assert len(raw.services[0].groups[0].rows) == 2
    assert raw.services[0].groups[0].rows[0].ranges[0].range_id == 5
    assert connection.rolled_back and connection.closed
    assert len(cursor.calls) == 12  # 3 transaction commands and 9 bounded data queries
    assert all("%s" in sql for sql, _ in cursor.calls if sql.startswith("SELECT"))
    assert all("202" not in sql for sql, _ in cursor.calls)
    assert all(
        "is_deleted=0" in sql
        for sql, _ in cursor.calls
        if sql.startswith("SELECT") and not sql.startswith("SELECT id,first_ip")
    )
    from app.modules.billing.netup import repository as netup_repository

    monkeypatch.setattr(netup_repository, "MAX_EXPANDED_RANGES", 0)
    with pytest.raises(BillingIncomplete):
        NetupRepository(lambda: FakeConnection(CompleteCursor([]))).lookup_by_account("202")


def test_provider_hides_db_failure_and_secrets(caplog: pytest.LogCaptureFixture) -> None:
    class FailedRepository:
        def lookup_by_account(self, _account_no: str):
            raise OSError("synthetic-secret-not-for-logs")

    provider = NetUPBillingProvider(FailedRepository())
    with pytest.raises(BillingUnavailable) as error:
        provider.lookup_by_account("202")
    assert str(error.value) == ""
    assert "synthetic-secret-not-for-logs" not in caplog.text


def test_repository_overload_and_deadline_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.billing.netup import repository as netup_repository

    class NoSlots:
        def acquire(self, timeout: int) -> bool:
            assert timeout == 1
            return False

        def release(self) -> None:
            raise AssertionError("No slot was acquired")

    monkeypatch.setattr(netup_repository, "LOOKUP_SLOTS", NoSlots())
    with pytest.raises(BillingUnavailable):
        NetupRepository(
            lambda: pytest.fail("overloaded lookup opened a connection")
        ).lookup_by_account("202")
    cursor = FakeCursor([])
    with pytest.raises(TimeoutError):
        NetupRepository._select(cursor, "SELECT 1", (), 0)
    assert cursor.calls == []


def test_config_masks_secret_and_requires_tls(tmp_path: Path) -> None:
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("synthetic test marker")
    config = NetupConfig.model_validate(
        {
            "host": "lab.example.test",
            "user": "read_only",
            "password": "synthetic-secret",
            "tls_ca": ca,
        }
    )
    assert "synthetic-secret" not in repr(config)
    with pytest.raises(ValueError):
        NetupConfig.model_validate(
            {
                "host": "lab.example.test",
                "user": "read_only",
                "password": "synthetic-secret",
                "tls_ca": tmp_path / "missing",
            }
        )
    lab = NetupConfig.model_validate(
        {
            "purpose": "lab",
            "host": "172.17.7.1",
            "user": "lab_ro",
            "password": "synthetic-secret",
            "allow_plaintext_lab": True,
        }
    )
    assert lab.tls_ca is None
    with pytest.raises(ValueError):
        NetupConfig.model_validate(
            {
                "purpose": "production",
                "host": "172.17.7.1",
                "user": "lab_ro",
                "password": "synthetic-secret",
                "allow_plaintext_lab": True,
            }
        )


def test_driver_disables_tls_only_for_explicit_lab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pymysql = pytest.importorskip("pymysql")
    calls = []
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: calls.append(kwargs))
    lab = NetupConfig.model_validate(
        {
            "purpose": "lab",
            "host": "172.17.7.1",
            "user": "synthetic_lab",
            "password": "synthetic-secret",
            "allow_plaintext_lab": True,
        }
    )
    lab.connect()
    assert "ssl_ca" not in calls[-1]
    assert "ssl_verify_cert" not in calls[-1]
    ca = tmp_path / "synthetic-ca.pem"
    ca.write_text("synthetic CA placeholder")
    production = NetupConfig.model_validate(
        {
            "purpose": "production",
            "host": "netup.example.invalid",
            "user": "synthetic_ro",
            "password": "synthetic-secret",
            "tls_ca": ca,
        }
    )
    production.connect()
    assert calls[-1]["ssl_ca"] == str(ca)
    assert calls[-1]["ssl_verify_cert"] is True
    assert calls[-1]["ssl_verify_identity"] is True


def _lab_config() -> NetupConfig:
    if os.environ.get("RUN_NETUP_LAB_TESTS") != "1":
        pytest.skip("separate NetUP lab read-only channel is not configured")
    return NetupConfig.model_validate(
        {
            "purpose": "lab",
            "host": os.environ.get("NETUP_LAB_HOST"),
            "port": os.environ.get("NETUP_LAB_PORT", "3306"),
            "database": os.environ.get("NETUP_LAB_DATABASE", "UTM5"),
            "user": os.environ.get("NETUP_LAB_USER"),
            "password": os.environ.get("NETUP_LAB_PASSWORD"),
            "tls_ca": os.environ.get("NETUP_LAB_TLS_CA"),
            "allow_plaintext_lab": os.environ.get("NETUP_LAB_ALLOW_PLAINTEXT", "false"),
            "lab_as_of_utc": os.environ.get("NETUP_LAB_AS_OF_UTC") or None,
        }
    )


def _lab_scenario_account(config: NetupConfig, scenario: str) -> str:
    """Find an account inside the lab without printing or storing its identifier."""
    as_of = int((config.lab_as_of_utc or datetime.now(UTC)).timestamp())
    if scenario == "missing":
        query, params = "SELECT MAX(id)+1 AS account_id FROM accounts", ()
    elif scenario == "deleted":
        query, params = (
            "SELECT id AS account_id FROM accounts WHERE is_deleted<>0 ORDER BY id LIMIT 1",
            (),
        )
    elif scenario == "conflict":
        query = (
            "SELECT sl.account_id FROM (SELECT ip_group_id FROM ip_groups "
            "WHERE is_deleted=0 GROUP BY ip_group_id HAVING COUNT(DISTINCT "
            "CASE WHEN switch_id>0 AND port_id>0 AND vlan_id>0 "
            "THEN CONCAT(switch_id,'/',port_id,'/',vlan_id) END)>1) ck "
            "JOIN iptraffic_service_links isl ON isl.ip_group_id=ck.ip_group_id "
            "AND isl.is_deleted=0 JOIN service_links sl ON sl.id=isl.id AND sl.is_deleted=0 "
            "JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
            "AND psl.is_planned=0 AND psl.need_del=0 AND psl.start_date<=%s "
            "AND psl.expire_date>%s JOIN accounts a ON a.id=sl.account_id "
            "AND a.is_deleted=0 ORDER BY sl.account_id LIMIT 10"
        )
        params = (as_of, as_of)
    elif scenario == "multiple_links":
        query = (
            "SELECT sl.account_id FROM service_links sl "
            "JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
            "AND psl.is_planned=0 AND psl.need_del=0 AND psl.start_date<=%s "
            "AND psl.expire_date>%s JOIN accounts a ON a.id=sl.account_id "
            "AND a.is_deleted=0 JOIN services_data sd ON sd.id=sl.service_id "
            "AND sd.is_deleted=0 AND sd.service_type IN (2,3) WHERE sl.is_deleted=0 "
            "GROUP BY sl.account_id HAVING COUNT(DISTINCT sl.id)>1 "
            "ORDER BY sl.account_id LIMIT 10"
        )
        params = (as_of, as_of)
    else:
        query = (
            "SELECT sl.account_id FROM service_links sl "
            "JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
            "AND psl.is_planned=0 AND psl.need_del=0 AND psl.start_date<=%s "
            "AND psl.expire_date>%s JOIN iptraffic_service_links isl "
            "ON isl.id=sl.id AND isl.is_deleted=0 JOIN ip_groups g "
            "ON g.ip_group_id=isl.ip_group_id AND g.is_deleted=0 "
            "WHERE sl.is_deleted=0 GROUP BY sl.account_id,isl.ip_group_id "
            "HAVING COUNT(g.id)>1 ORDER BY sl.account_id LIMIT 10"
        )
        params = (as_of, as_of)
    try:
        connection = config.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time=%s", (3.0,))
                cursor.execute(query, params)
                candidates = [str(row["account_id"]) for row in cursor.fetchall()]
        finally:
            connection.close()
    except Exception as exc:
        pytest.fail(f"lab scenario query failed: {type(exc).__name__}", pytrace=False)
    provider = NetUPBillingProvider.from_config(config)
    for account_no in candidates:
        card = provider.lookup_by_account(account_no)
        if scenario in {"missing", "deleted"} and card is None:
            return account_no
        if card is None:
            continue
        if scenario == "conflict" and any(
            item.field_meta["port"].quality == DataQuality.CONFLICT for item in card.connections
        ):
            return account_no
        if scenario == "multiple_links" and len(card.services) > 1:
            return account_no
        if scenario == "multirow" and any(
            len(group.rows) > 1 for item in card.connections for group in item.ip_groups
        ):
            return account_no
    pytest.fail(f"restored lab has no usable {scenario} scenario", pytrace=False)


@pytest.mark.integration
def test_restored_utm5_via_separate_lab_channel() -> None:
    config = _lab_config()
    account_no = os.environ.get("NETUP_LAB_ACCOUNT_NO") or _lab_scenario_account(config, "multirow")
    card = NetUPBillingProvider.from_config(config).lookup_by_account(account_no)
    assert card is not None
    assert card.source == "NETUP"
    assert bool(card.billing_account == account_no)
    assert all(connection.access_type == AccessType.UNKNOWN for connection in card.connections)
    assert any(len(group.rows) > 1 for item in card.connections for group in item.ip_groups)
    assert len(card.financial_accounts) == len(card.accounts)
    assert all(item.field_meta["balance"].source == "NETUP" for item in card.financial_accounts)
    assert all(item.billing_active is True for item in card.services)
    assert all("tariff_name" in item.field_meta for item in card.services)
    if config.lab_as_of_utc is not None:
        assert card.field_meta["billing_account"].quality == DataQuality.STALE
        assert card.field_meta["billing_account"].observed_at == config.lab_as_of_utc


@pytest.mark.integration
def test_restored_utm5_service_links_and_group_rows_match_source() -> None:
    config = _lab_config()
    account_no = _lab_scenario_account(config, "multirow")
    card = NetUPBillingProvider.from_config(config).lookup_by_account(account_no)
    assert card is not None
    as_of = int((config.lab_as_of_utc or datetime.now(UTC)).timestamp())
    try:
        connection = config.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                cursor.execute(
                    "SELECT sl.id,sd.service_type FROM service_links sl "
                    "JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0 "
                    "JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0 "
                    "WHERE sl.account_id=%s AND sl.user_id=%s AND sl.is_deleted=0 "
                    "AND sd.service_type IN (2,3) AND psl.is_planned=0 AND psl.need_del=0 "
                    "AND psl.start_date<=%s AND psl.expire_date>%s",
                    (int(account_no), int(card.subscriber_id), as_of, as_of),
                )
                source_services = cursor.fetchall()
                assert bool(
                    {str(row["id"]) for row in source_services}
                    == {item.service_id for item in card.services if item.account_id == account_no}
                )
                source_ip_ids = {
                    str(row["id"]) for row in source_services if row["service_type"] == 3
                }
                card_connections = [
                    item for item in card.connections if item.service_id in source_ip_ids
                ]
                assert len(card_connections) == len(source_ip_ids)
                for item in card_connections:
                    cursor.execute(
                        "SELECT ip_group_id FROM iptraffic_service_links "
                        "WHERE id=%s AND is_deleted=0",
                        (int(item.service_id),),
                    )
                    source_groups = cursor.fetchall()
                    assert bool(
                        {str(row["ip_group_id"]) for row in source_groups}
                        == {group.group_id for group in item.ip_groups}
                    )
                    for group in item.ip_groups:
                        cursor.execute(
                            "SELECT id FROM ip_groups WHERE ip_group_id=%s AND is_deleted=0",
                            (int(group.group_id),),
                        )
                        assert bool(
                            {str(row["id"]) for row in cursor.fetchall()}
                            == {row.row_id for row in group.rows}
                        )
        finally:
            connection.rollback()
            connection.close()
    except Exception as exc:
        pytest.fail(f"lab mapping check failed: {type(exc).__name__}", pytrace=False)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("variable", "scenario", "expectation"),
    [
        ("NETUP_LAB_MISSING_ACCOUNT", "missing", "missing"),
        ("NETUP_LAB_DELETED_ACCOUNT", "deleted", "missing"),
        ("NETUP_LAB_CONFLICT_ACCOUNT", "conflict", "conflict"),
        ("NETUP_LAB_MULTILINK_ACCOUNT", "multiple_links", "multiple_links"),
    ],
)
def test_restored_utm5_optional_scenarios(variable: str, scenario: str, expectation: str) -> None:
    config = _lab_config()
    account_no = os.environ.get(variable) or _lab_scenario_account(config, scenario)
    card = NetUPBillingProvider.from_config(config).lookup_by_account(account_no)
    if expectation == "missing":
        assert card is None
    elif expectation == "conflict":
        assert card is not None
        assert any(
            connection.field_meta["port"].quality == DataQuality.CONFLICT
            for connection in card.connections
        )
        assert any(
            len(
                {
                    (row.switch_id, row.port_id, row.vlan_id)
                    for group in connection.ip_groups
                    for row in group.rows
                    if row.switch_id not in {None, "0"}
                    and row.port_id not in {None, 0}
                    and row.vlan_id not in {None, 0}
                }
            )
            > 1
            for connection in card.connections
        )
    else:
        assert card is not None and len(card.services) > 1


def test_0005_upgrade_preserves_existing_immutable_snapshots(
    isolated_postgres_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL", isolated_postgres_url.render_as_string(hide_password=False)
    )
    command.upgrade(config, "0004_subscriber_360")
    engine = create_engine(isolated_postgres_url)
    user_id, snapshot_id, connection_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app_users (id,login,display_name,role,password_hash,is_active) "
                    "VALUES (:id,'legacy-netup-test','Legacy','operator',:hash,true)"
                ),
                {"id": user_id, "hash": hash_password(uuid4().hex)},
            )
            connection.execute(
                text(
                    "INSERT INTO subscriber_snapshots "
                    "(id,account_no,source,fetched_at,requested_by,expires_at,contracts,services) "
                    "VALUES (:id,'MOCK-LEGACY','MOCK',now(),:user_id,"
                    "now()+interval '1 hour','[]'::jsonb,'[]'::jsonb)"
                ),
                {"id": snapshot_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO connection_snapshots "
                    "(id,subscriber_snapshot_id,access_type) VALUES (:id,:parent,'UNKNOWN')"
                ),
                {"id": connection_id, "parent": snapshot_id},
            )
        with engine.connect() as connection:
            assert "actual_address" not in {
                column["name"] for column in inspect(connection).get_columns("subscriber_snapshots")
            }
            assert "ip_groups" not in {
                column["name"] for column in inspect(connection).get_columns("connection_snapshots")
            }
        # Only raw SQL is valid at 0004; the current ORM requires 0007.
        command.upgrade(config, "0005_netup_groups")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT s.accounts,c.ip_groups,c.service_id "
                    "FROM subscriber_snapshots s JOIN connection_snapshots c "
                    "ON c.subscriber_snapshot_id=s.id WHERE s.id=:id"
                ),
                {"id": snapshot_id},
            ).one()
            assert row.accounts == [] and row.ip_groups == [] and row.service_id is None
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0005_netup_groups")
        command.upgrade(config, "head")
        with Session(engine) as db:
            snapshot = db.get(SubscriberSnapshot, snapshot_id)
            stored_connection = db.get(ConnectionSnapshot, connection_id)
            assert snapshot is not None and stored_connection is not None
            assert snapshot.actual_address is None and snapshot.accounts == []
            assert snapshot.contacts == []
            assert snapshot.financial_accounts == []
            assert stored_connection.service_id is None and stored_connection.ip_groups == []

            raw = synthetic_subscriber(conflict=True)
            first_service, second_service = raw.services
            extra_group = NetupGroup(10, synthetic_group().rows)
            first_service = NetupService(
                first_service.link_id,
                first_service.account_id,
                first_service.name,
                first_service.service_type,
                (*first_service.groups, extra_group),
            )
            card = normalize_netup(
                NetupSubscriber(**{**raw.__dict__, "services": (first_service, second_service)})
            )
            new_snapshot = SubscriberSnapshot(
                id=uuid4(),
                account_no=card.billing_account,
                source=card.source,
                fetched_at=card.fetched_at,
                expires_at=card.fetched_at + timedelta(hours=1),
                requested_by=user_id,
                actual_address=card.actual_address,
                contacts=[item.model_dump(mode="json") for item in card.contacts],
                accounts=[account.model_dump(mode="json") for account in card.accounts],
                contracts=[],
                services=[service.model_dump(mode="json") for service in card.services],
            )
            expected_groups = [
                group.model_dump(mode="json") for group in card.connections[0].ip_groups
            ]
            expected_meta = {
                key: origin.model_dump(mode="json")
                for key, origin in card.connections[0].field_meta.items()
            }
            db.add(new_snapshot)
            db.flush()
            new_connection = ConnectionSnapshot(
                id=uuid4(),
                subscriber_snapshot_id=new_snapshot.id,
                access_type=card.connections[0].access_type,
                service_id=card.connections[0].service_id,
                ip_groups=expected_groups,
                field_meta=expected_meta,
            )
            db.add(new_connection)
            db.commit()
            db.expire_all()
            reloaded_snapshot = db.get(SubscriberSnapshot, new_snapshot.id)
            reloaded_connection = db.get(ConnectionSnapshot, new_connection.id)
            assert reloaded_snapshot is not None and reloaded_connection is not None
            assert len(reloaded_snapshot.accounts) == 2
            assert len(reloaded_snapshot.contacts) == 1
            assert reloaded_connection.service_id == "301"
            assert reloaded_connection.ip_groups == expected_groups
            assert [group["group_id"] for group in reloaded_connection.ip_groups] == ["9", "10"]
            assert len(reloaded_connection.ip_groups[0]["rows"]) == 3
            assert reloaded_connection.field_meta["device"]["quality"] == "CONFLICT"
    finally:
        engine.dispose()
