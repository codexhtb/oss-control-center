"""Synthetic monitoring contract tests; no real OSS identifiers or credentials."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import (
    DeviceMapping,
    MappingRegistry,
    MonitoringConfig,
    PortMapping,
)
from app.modules.diagnostics.correlation import CorrelationEngine
from app.modules.diagnostics.http import SourceUnavailable
from app.modules.diagnostics.mapping import unique_exact_mappings
from app.modules.diagnostics.providers import IcingaProvider, LibreNMSProvider


class FakeTransport:
    def __init__(self, replies: dict[str, dict]):
        self.replies = replies
        self.paths: list[str] = []

    def get(self, path: str, params=None):
        self.paths.append(path)
        if path not in self.replies:
            raise SourceUnavailable("synthetic_unavailable")
        return self.replies[path]


def connection(rows: list[dict]):
    return SimpleNamespace(id=uuid4(), ip_groups=[{"group_id": "synthetic", "rows": rows}])


def mapped_engine(now: datetime, *, port_id: int = 8) -> tuple[CorrelationEngine, FakeTransport]:
    transport = FakeTransport(
        {
            "/api/v0/devices/42": {
                "status": "ok",
                "devices": [
                    {
                        "device_id": "42",
                        "hostname": "synthetic-switch",
                        "status": 1,
                        "last_polled": now.isoformat(),
                    }
                ],
            },
            "/api/v0/devices/42/ports": {
                "status": "ok",
                "ports": [
                    {
                        "port_id": "99",
                        "device_id": "42",
                        "ifIndex": "8",
                        "ifName": "synthetic-if",
                        "ifOperStatus": "down",
                        "ifAdminStatus": "up",
                        "poll_prev": now.timestamp(),
                    }
                ],
            },
        }
    )
    registry = MappingRegistry(
        devices=[
            DeviceMapping(
                netup_switch_id="7",
                librenms_device_id=42,
                ports=[PortMapping(netup_port_id=port_id, librenms_port_id=99)],
            )
        ]
    )
    return CorrelationEngine(registry, LibreNMSProvider(transport), None), transport


def test_exact_device_and_port_mapping_never_uses_ifindex() -> None:
    now = datetime.now(UTC)
    engine, transport = mapped_engine(now, port_id=8)
    report = engine.check(connection([{"switch_id": "7", "port_id": 8}]))
    assert report.device_match == DataQuality.KNOWN
    assert report.interface_match == DataQuality.KNOWN
    assert [item.result for item in report.observations] == ["UP", "DOWN", "UP"]
    assert transport.paths == ["/api/v0/devices/42", "/api/v0/devices/42/ports"]
    assert all(item.observed_at is not None and item.retrieved_at for item in report.observations)


def test_port_without_explicit_mapping_stays_unknown_even_if_ifindex_matches() -> None:
    engine, transport = mapped_engine(datetime.now(UTC), port_id=9)
    report = engine.check(connection([{"switch_id": "7", "port_id": 8}]))
    assert report.interface_match == DataQuality.UNKNOWN
    assert len(report.observations) == 1
    assert "interface_mapping_unconfirmed" in report.limitations
    assert transport.paths == ["/api/v0/devices/42"]


def test_conflicting_switches_do_not_query_monitoring() -> None:
    engine, transport = mapped_engine(datetime.now(UTC))
    report = engine.check(
        connection([{"switch_id": "7", "port_id": 8}, {"switch_id": "9", "port_id": 8}])
    )
    assert report.device_match == DataQuality.CONFLICT
    assert report.interface_match == DataQuality.CONFLICT
    assert report.observations == []
    assert transport.paths == []


def test_missing_mapping_and_unavailable_source_are_explicit() -> None:
    unmapped = CorrelationEngine(MappingRegistry(), None, None).check(
        connection([{"switch_id": "7", "port_id": 8}])
    )
    assert unmapped.device_match == DataQuality.UNKNOWN
    assert unmapped.observations == []
    engine, _ = mapped_engine(datetime.now(UTC))
    engine.librenms = LibreNMSProvider(FakeTransport({}))
    unavailable = engine.check(connection([{"switch_id": "7", "port_id": 8}]))
    assert unavailable.observations[0].quality == DataQuality.UNAVAILABLE
    assert unavailable.observations[0].result == "UNAVAILABLE"


def test_stale_measurement_and_missing_timezone_are_not_current() -> None:
    now = datetime.now(UTC)
    engine, _ = mapped_engine(now - timedelta(hours=2))
    report = engine.check(connection([{"switch_id": "7", "port_id": 8}]))
    assert all(item.quality == DataQuality.STALE for item in report.observations)
    transport = FakeTransport(
        {
            "/api/v0/devices/42": {
                "status": "ok",
                "devices": [{"device_id": 42, "status": 0, "last_polled": "2026-10-10 12:00:00"}],
            },
            "/api/v0/devices/42/ports": {"status": "ok", "ports": []},
        }
    )
    engine.librenms = LibreNMSProvider(transport)
    unknown_time = engine.check(connection([{"switch_id": "7", "port_id": 8}]))
    assert unknown_time.observations[0].result == "DOWN"
    assert unknown_time.observations[0].quality == DataQuality.UNKNOWN


def test_icinga_unreachable_is_distinct_from_down() -> None:
    transport = FakeTransport(
        {
            "/v1/objects/hosts": {
                "results": [
                    {
                        "name": "synthetic-host",
                        "attrs": {
                            "state": 1,
                            "last_reachable": False,
                            "last_check": datetime.now(UTC).timestamp(),
                        },
                    }
                ]
            },
            "/v1/objects/services": {
                "results": [
                    {
                        "name": "synthetic-host!ping",
                        "attrs": {"state": 2, "last_check": datetime.now(UTC).timestamp()},
                    }
                ]
            },
        }
    )
    provider = IcingaProvider(transport)
    assert provider.host("synthetic-host").state == "UNREACHABLE"
    assert provider.services("synthetic-host")[0].state == "CRITICAL"


def test_plaintext_requires_explicit_dev_opt_in() -> None:
    with pytest.raises(ValueError, match="plaintext"):
        MonitoringConfig(app_env="production", allow_http_dev=True).validate_endpoint(
            "http://synthetic-monitor.invalid:8080"
        )
    with pytest.raises(ValueError, match="plaintext"):
        MonitoringConfig(app_env="dev").validate_endpoint("http://synthetic-monitor.invalid")
    assert MonitoringConfig(app_env="dev", allow_http_dev=True).validate_endpoint(
        "http://synthetic-monitor.invalid"
    )


def test_duplicate_or_ambiguous_mappings_rejected() -> None:
    with pytest.raises(ValueError):
        MappingRegistry(
            devices=[DeviceMapping(netup_switch_id="7"), DeviceMapping(netup_switch_id="7")]
        )


def test_unique_exact_name_mapping_rejects_case_changes_and_duplicates() -> None:
    registry = unique_exact_mappings(
        [
            {"id": 7, "name": "synthetic-a"},
            {"id": 8, "name": "synthetic-duplicate"},
            {"id": 9, "name": "synthetic-duplicate"},
            {"id": 10, "name": "Synthetic-B"},
        ],
        [
            {"device_id": 42, "sysName": "synthetic-a"},
            {"device_id": 43, "sysName": "synthetic-duplicate"},
            {"device_id": 44, "sysName": "synthetic-b"},
        ],
    )
    assert len(registry.devices) == 1
    assert registry.devices[0].netup_switch_id == "7"
    assert registry.devices[0].librenms_device_id == 42
    assert registry.devices[0].match_basis == "unique_exact_sysname"
    assert registry.devices[0].ports == []


def test_historical_netup_mapping_is_stale_without_downgrading_live_observation() -> None:
    now = datetime.now(UTC)
    engine, transport = mapped_engine(now)
    engine.mappings.devices[0].match_basis = "unique_exact_sysname"
    snapshot = connection([{"switch_id": "7", "port_id": 8}])
    snapshot.field_meta = {
        "device": {"quality": "STALE"},
        "port": {"quality": "STALE"},
    }
    report = engine.check(snapshot)
    assert report.device_match == DataQuality.STALE
    assert report.interface_match == DataQuality.STALE
    assert report.observations[0].quality == DataQuality.KNOWN
    assert "device_mapping_unique_exact_sysname" in report.limitations
    assert len(transport.paths) == 2
    with pytest.raises(ValueError):
        DeviceMapping(
            netup_switch_id="7",
            ports=[
                PortMapping(netup_port_id=8, librenms_port_id=99),
                PortMapping(netup_port_id=8, librenms_port_id=100),
            ],
        )
