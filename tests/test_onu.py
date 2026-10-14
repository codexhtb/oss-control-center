"""Synthetic ONU adapter tests; no LibreNMS connection or subscriber identifiers."""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.diagnostics.config import DeviceMapping, MappingRegistry
from app.modules.diagnostics.http import SourceUnavailable
from app.modules.diagnostics.onu import BdcomOnuReader, OnuDbConfig, normalize_candidate
from app.modules.diagnostics.onu_correlation import lookup_onu_candidates


def connection(rows: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        access_type="UNKNOWN",
        field_meta={},
        ip_groups=[{"group_id": "synthetic", "rows": rows}],
    )


class FakeReader:
    def __init__(
        self, rows: list[dict] | None = None, truncated: bool = False, pon_type: str = "GPON"
    ):
        self.rows = rows or []
        self.truncated = truncated
        self.pon_type = pon_type
        self.calls: list[tuple[int, int, int | None]] = []

    def candidates(self, device_id: int, vlan: int, netup_port: int | None = None):
        self.calls.append((device_id, vlan, netup_port))
        rows = self.rows
        if netup_port is not None:
            offset = 13 if self.pon_type == "GPON" else 6
            pon_port = netup_port - offset
            rows = [
                row for row in rows if row["interface"].startswith(f"{self.pon_type}0/{pon_port}:")
            ]
        return (
            {
                "sys_name": "synthetic-olt",
                "pon_type": self.pon_type,
                "last_success": datetime(2026, 10, 10, 12),
            },
            rows,
            self.truncated,
        )


def registry() -> MappingRegistry:
    return MappingRegistry(devices=[DeviceMapping(netup_switch_id="7", librenms_device_id=42)])


def gpon_row() -> dict:
    return {
        "pon_type": "gpon",
        "interface": "GPON0/1:2",
        "mac": "serial-key-placeholder",
        "serial": "SYNTHETIC-SERIAL",
        "state": "online",
        "reason": None,
        "onu_rx_power": -21.5,
        "onu_tx_power": None,
        "olt_rx_power": -24,
        "signal_updated_at": datetime(2026, 10, 9, 4, 10),
        "last_seen": datetime(2026, 10, 10, 12),
    }


def test_gpon_surrogate_key_is_never_rendered_as_mac() -> None:
    item = normalize_candidate(gpon_row(), {"sys_name": "synthetic-olt"})
    assert item is not None
    assert item.mac is None
    assert item.serial == "SYNTHETIC-SERIAL"
    assert item.onu_rx_dbm == "-21.5"
    assert item.optical_measured_at == "2026-10-09 04:10:00"


def test_epon_mac_and_deregistered_are_preserved() -> None:
    row = {
        **gpon_row(),
        "pon_type": "epon",
        "mac": "02:00:00:00:00:01",
        "serial": None,
        "state": "deregistered",
    }
    item = normalize_candidate(row, {})
    assert item is not None
    assert item.mac == "02:00:00:00:00:01"
    assert item.serial is None
    assert item.state == "DEREGISTERED"


def test_vlan_only_produces_unconfirmed_candidate() -> None:
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(
        connection([{"switch_id": "7", "port_id": 3, "vlan_id": 101}]), registry(), reader
    )
    assert reader.calls == [(42, 101, None)]
    assert result.availability == "AVAILABLE"
    assert result.source == "BDCOM" and result.retrieved_at.tzinfo is not None
    assert result.association == "UNCONFIRMED"
    assert result.candidates[0].inventory_polled_at == "2026-10-10 12:00:00"
    assert "optical_values_may_have_different_ages" in result.limitations


def netup_row(port: int, vlan: int = 101) -> dict:
    return {
        "switch_id": "7",
        "port_id": port,
        "vlan_id": vlan,
        "field_meta": {
            key: {"source": "NETUP", "quality": "KNOWN", "observed_at": "2026-10-10T12:00:00+00:00"}
            for key in ("switch_id", "port_id", "vlan_id")
        },
    }


@pytest.mark.parametrize(
    ("pon_type", "port", "row"),
    [
        ("GPON", 14, gpon_row()),
        (
            "EPON",
            7,
            {
                **gpon_row(),
                "pon_type": "EPON",
                "interface": "EPON0/1:2",
                "mac": "02:00:00:00:00:01",
                "serial": None,
            },
        ),
    ],
)
def test_unique_complete_key_matches_by_confirmed_rule(pon_type, port, row) -> None:
    reader = FakeReader([row], pon_type=pon_type)
    result = lookup_onu_candidates(connection([netup_row(port)]), registry(), reader)
    assert reader.calls == [(42, 101, port)]
    assert result.availability == "AVAILABLE"
    assert result.association == "MATCHED_BY_RULE"
    assert result.match is not None
    assert result.match.pon_port == 1 and result.match.netup_port == port
    assert result.match.vlan == 101
    assert result.match.billing_source == "NETUP"
    assert result.match.onu_source == "LIBRENMS_BDCOM"
    assert result.match.billing_observed_at is not None


def test_composite_miss_does_not_confirm_vlan_candidate() -> None:
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(connection([netup_row(15)]), registry(), reader)
    assert result.availability == "UNKNOWN"
    assert result.association == "UNCONFIRMED"
    assert len(result.candidates) == 1
    assert reader.calls == [(42, 101, 15), (42, 101, None)]
    assert "onu_composite_key_not_found" in result.limitations
    assert "nearby_vlan_candidates_unconfirmed" in result.limitations


def test_duplicate_composite_key_remains_conflict() -> None:
    rows = [gpon_row(), {**gpon_row(), "interface": "GPON0/1:3"}]
    result = lookup_onu_candidates(connection([netup_row(14)]), registry(), FakeReader(rows))
    assert result.availability == "CONFLICT"
    assert result.association == "UNCONFIRMED"
    assert len(result.candidates) == 2
    assert "onu_composite_key_ambiguous" in result.limitations


def test_mock_or_unproven_source_never_auto_matches() -> None:
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(
        connection([{"switch_id": "7", "port_id": 14, "vlan_id": 101}]), registry(), reader
    )
    assert reader.calls == [(42, 101, None)]
    assert result.association == "UNCONFIRMED"


def test_conflicting_netup_ports_never_query_onu() -> None:
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(connection([netup_row(14), netup_row(15)]), registry(), reader)
    assert result.availability == "CONFLICT" and reader.calls == []


def test_two_service_links_with_same_complete_key_never_auto_match() -> None:
    first = connection([netup_row(14)])
    second = connection([netup_row(14)])
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(first, registry(), reader, sibling_connections=[first, second])
    assert result.availability == "CONFLICT"
    assert result.association == "UNCONFIRMED"
    assert "multiple_service_links_same_pon_vlan" in result.limitations
    assert reader.calls == []


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([{"switch_id": "7", "vlan_id": 101}, {"switch_id": "8", "vlan_id": 101}], "CONFLICT"),
        ([{"switch_id": "7", "vlan_id": 101}, {"switch_id": "7", "vlan_id": 102}], "CONFLICT"),
        ([{"switch_id": "7", "vlan_id": None}], "UNKNOWN"),
    ],
)
def test_ambiguous_or_missing_billing_values_never_query_source(
    rows: list[dict], expected: str
) -> None:
    reader = FakeReader([gpon_row()])
    assert lookup_onu_candidates(connection(rows), registry(), reader).availability == expected
    assert reader.calls == []


def test_unconfigured_source_is_explicit() -> None:
    result = lookup_onu_candidates(
        connection([{"switch_id": "7", "vlan_id": 101}]), registry(), None
    )
    assert result.availability == "UNAVAILABLE"
    assert result.candidates == []


def test_confirmed_ethernet_connection_never_queries_onu_inventory() -> None:
    snapshot = connection([{"switch_id": "7", "vlan_id": 101}])
    snapshot.access_type = "ETHERNET"
    reader = FakeReader([gpon_row()])
    result = lookup_onu_candidates(snapshot, registry(), reader)
    assert result.availability == "UNKNOWN"
    assert result.candidates == []
    assert reader.calls == []


def test_multiple_or_truncated_candidates_are_never_confirmed() -> None:
    reader = FakeReader([gpon_row(), {**gpon_row(), "interface": "GPON0/1:3"}], True)
    result = lookup_onu_candidates(
        connection([{"switch_id": "7", "vlan_id": 101}]), registry(), reader
    )
    assert result.availability == "CONFLICT"
    assert result.association == "UNCONFIRMED"
    assert "onu_candidates_truncated" in result.limitations


def test_plaintext_credentials_dev_only_and_protected_file(tmp_path) -> None:
    path = tmp_path / "bdcom.json"
    path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 3306,
                "database": "librenms",
                "user": "reader",
                "password": "synthetic-only",
                "tls_ca": None,
                "allow_plaintext_dev": True,
            }
        )
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="dev-only"):
        OnuDbConfig.from_file(str(path), "production")
    assert OnuDbConfig.from_file(str(path), "dev").host == "127.0.0.1"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="credential"):
        OnuDbConfig.from_file(str(path), "dev")


def test_dev_tunnel_requires_protected_password_and_fixed_socket(tmp_path) -> None:
    path = tmp_path / "bdcom-password"
    path.write_text("synthetic-only\n")
    path.chmod(0o600)
    socket_path = "/run/bdcom/bdcom.sock"
    with pytest.raises(ValueError, match="dev-only"):
        OnuDbConfig.from_tunnel_password_file(str(path), socket_path, "production")
    with pytest.raises(ValueError, match="socket"):
        OnuDbConfig.from_tunnel_password_file(str(path), "/tmp/other.sock", "dev")
    config = OnuDbConfig.from_tunnel_password_file(str(path), socket_path, "dev")
    assert config.unix_socket == socket_path
    assert config.user == "oss_diagnostics"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="secret file"):
        OnuDbConfig.from_tunnel_password_file(str(path), socket_path, "dev")


def test_tunnel_reader_uses_unix_socket_and_disables_tls(monkeypatch, tmp_path) -> None:
    path = tmp_path / "bdcom-password"
    path.write_text("synthetic-only")
    path.chmod(0o600)
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            return None

        def fetchall(self):
            return [{"device_id": 42}] if not hasattr(self, "fetched") else []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return FakeCursor()

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr("app.modules.diagnostics.onu.pymysql.connect", fake_connect)
    config = OnuDbConfig.from_tunnel_password_file(str(path), "/run/bdcom/bdcom.sock", "dev")
    BdcomOnuReader(config).candidates(42, 101)
    assert captured["unix_socket"] == "/run/bdcom/bdcom.sock"
    assert captured["ssl_disabled"] is True
    assert "host" not in captured and "port" not in captured


def test_tunnel_outage_is_reported_without_exposing_driver_details(monkeypatch, tmp_path) -> None:
    path = tmp_path / "bdcom-password"
    path.write_text("synthetic-only")
    path.chmod(0o600)

    def unreachable(**_):
        raise OSError("synthetic socket path and credential detail")

    monkeypatch.setattr("app.modules.diagnostics.onu.pymysql.connect", unreachable)
    config = OnuDbConfig.from_tunnel_password_file(str(path), "/run/bdcom/bdcom.sock", "dev")
    with pytest.raises(SourceUnavailable, match="bdcom_source_unavailable") as caught:
        BdcomOnuReader(config).candidates(42, 101)
    assert "synthetic socket" not in str(caught.value)


def test_reader_queries_only_two_tables_with_parameters(monkeypatch) -> None:
    calls = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params):
            calls.append((query, params))

        def fetchall(self):
            return [{"device_id": 42}] if len(calls) == 1 else []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("app.modules.diagnostics.onu.pymysql.connect", lambda **_: FakeConnection())
    config = OnuDbConfig(
        "127.0.0.1", 3306, "librenms", "reader", "synthetic-only", None, True, "dev"
    )
    BdcomOnuReader(config).candidates(42, 101)
    assert len(calls) == 2
    assert all(query.startswith("SELECT ") for query, _ in calls)
    assert calls[0][1] == (42,)
    assert calls[1][1] == (42, 101)
    assert "LIMIT 21" in calls[1][0]


@pytest.mark.parametrize(
    ("pon_type", "netup_port", "expected_pattern"),
    [
        ("GPON", 16, "^GPON[0-9]+/3:[0-9]+$"),
        ("EPON", 9, "^EPON[0-9]+/3:[0-9]+$"),
    ],
)
def test_reader_uses_parameterized_full_pon_vlan_key(
    monkeypatch, pon_type, netup_port, expected_pattern
) -> None:
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params):
            calls.append((query, params))

        def fetchall(self):
            return [{"device_id": 42, "pon_type": pon_type}] if len(calls) == 1 else []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr("app.modules.diagnostics.onu.pymysql.connect", lambda **_: Connection())
    config = OnuDbConfig(
        "127.0.0.1", 3306, "librenms", "reader", "synthetic-only", None, True, "dev"
    )
    BdcomOnuReader(config).candidates(42, 101, netup_port)
    assert len(calls) == 2
    assert "interface REGEXP %s" in calls[1][0]
    assert calls[1][1] == (42, 101, pon_type, expected_pattern)
