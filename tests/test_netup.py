"""Synthetic contract and optional read-only lab checks; never prints source rows."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

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
from app.modules.network.models import AccessType


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


def test_repeated_identical_phone_is_not_a_conflict() -> None:
    raw = synthetic_subscriber()
    raw = NetupSubscriber(**{**raw.__dict__, "work_phone": raw.mobile_phone})
    card = normalize_netup(raw)
    assert card.phone == "000-000"
    assert card.field_meta["phone"].quality == DataQuality.KNOWN


def test_historical_lab_observation_is_stale_without_changing_read_time() -> None:
    raw = synthetic_subscriber()
    observed = raw.fetched_at - timedelta(days=3)
    raw = NetupSubscriber(**{**raw.__dict__, "source_as_of": observed})
    card = normalize_netup(raw)
    assert card.fetched_at == raw.fetched_at
    assert card.field_meta["billing_account"].observed_at == observed
    assert card.field_meta["billing_account"].quality == DataQuality.STALE
    assert card.connections[0].ip_groups[0].rows[1].field_meta["ip_raw"].quality == (
        DataQuality.UNKNOWN
    )


def test_existing_mock_provider_contract_remains_valid() -> None:
    card = MockBillingProvider().lookup_by_account("MOCK-ETH-001")
    assert card is not None and card.billing_account == "MOCK-ETH-001"
    assert card.accounts == [] and card.connections[0].ip_groups == []
    assert card.connections[0].service_id is None


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


def test_repository_loads_multiple_accounts_links_and_group_rows() -> None:
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
            elif sql.startswith("SELECT DISTINCT a.id FROM users_accounts"):
                self.rows = [{"id": 202}, {"id": 203}]
            elif sql.startswith("SELECT sl.id"):
                self.rows = (
                    [
                        {
                            "id": 301,
                            "account_id": 202,
                            "service_name": "Synthetic Internet",
                            "service_type": 3,
                        }
                    ]
                    if params[1] == 202
                    else [
                        {
                            "id": 302,
                            "account_id": 203,
                            "service_name": "Synthetic ancillary",
                            "service_type": 2,
                        }
                    ]
                )
            elif sql.startswith("SELECT ip_group_id"):
                self.rows = [{"ip_group_id": 9}]
            elif sql.startswith("SELECT g.id"):
                self.rows = [
                    {
                        "id": 11,
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
            elif sql.startswith("SELECT id,first_ip,last_ip"):
                self.rows = [{"id": 5, "first_ip": 1000, "last_ip": 2000}]
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
    assert all("%s" in sql for sql, _ in cursor.calls if sql.startswith("SELECT"))
    assert all("202" not in sql for sql, _ in cursor.calls)
    assert all(
        "is_deleted=0" in sql
        for sql, _ in cursor.calls
        if sql.startswith("SELECT") and not sql.startswith("SELECT id,first_ip")
    )


def test_provider_hides_db_failure_and_secrets(caplog: pytest.LogCaptureFixture) -> None:
    class FailedRepository:
        def lookup_by_account(self, _account_no: str):
            raise OSError("synthetic-secret-not-for-logs")

    provider = NetUPBillingProvider(FailedRepository())
    with pytest.raises(BillingUnavailable) as error:
        provider.lookup_by_account("202")
    assert str(error.value) == ""
    assert "synthetic-secret-not-for-logs" not in caplog.text


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


@pytest.mark.integration
def test_restored_utm5_via_separate_lab_channel() -> None:
    if os.environ.get("RUN_NETUP_LAB_TESTS") != "1":
        pytest.skip("separate NetUP lab read-only channel is not configured")
    account_no = os.environ.get("NETUP_LAB_ACCOUNT_NO")
    if not account_no:
        pytest.skip("NETUP_LAB_ACCOUNT_NO is required for this opt-in test")
    config = NetupConfig.model_validate(
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
    card = NetUPBillingProvider.from_config(config).lookup_by_account(account_no)
    assert card is not None and card.source == "NETUP"
    assert bool(card.billing_account == account_no)
    assert all(connection.access_type == AccessType.UNKNOWN for connection in card.connections)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("variable", "expectation"),
    [
        ("NETUP_LAB_MISSING_ACCOUNT", "missing"),
        ("NETUP_LAB_DELETED_ACCOUNT", "missing"),
        ("NETUP_LAB_CONFLICT_ACCOUNT", "conflict"),
        ("NETUP_LAB_MULTILINK_ACCOUNT", "multiple_links"),
    ],
)
def test_restored_utm5_optional_scenarios(variable: str, expectation: str) -> None:
    if os.environ.get("RUN_NETUP_LAB_TESTS") != "1":
        pytest.skip("separate NetUP lab read-only channel is not configured")
    account_no = os.environ.get(variable)
    if not account_no:
        pytest.skip("this anonymized lab scenario is not configured")
    config = NetupConfig.model_validate(
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
    card = NetUPBillingProvider.from_config(config).lookup_by_account(account_no)
    if expectation == "missing":
        assert card is None
    elif expectation == "conflict":
        assert card is not None
        assert any(
            connection.field_meta["port"].quality == DataQuality.CONFLICT
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
                    "VALUES (:id,'legacy-netup-test','Legacy','operator','synthetic-hash',true)"
                ),
                {"id": user_id},
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
        command.upgrade(config, "head")
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
    finally:
        engine.dispose()
