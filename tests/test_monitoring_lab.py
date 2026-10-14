"""Opt-in read-only LibreNMS Lab integration; never prints device or secret values."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import MappingRegistry, MonitoringConfig, protected_text
from app.modules.diagnostics.correlation import CorrelationEngine
from app.modules.diagnostics.http import JsonTransport
from app.modules.diagnostics.providers import LibreNMSProvider


@pytest.mark.integration
def test_librenms_read_only_device_and_ports() -> None:
    if os.environ.get("RUN_LIBRENMS_LAB_TESTS") != "1":
        pytest.skip("separate LibreNMS read-only lab channel is not configured")
    config = MonitoringConfig.from_env()
    url = config.validate_endpoint(config.librenms_url)
    assert url and config.librenms_token_file
    provider = LibreNMSProvider(
        JsonTransport(
            url,
            {"Authorization": "Bearer " + protected_text(config.librenms_token_file)},
            config.timeout_seconds,
            config.ca_file,
        )
    )
    provider.probe()
    listing = provider.transport.get("/api/v0/devices")
    rows = listing.get("devices")
    assert isinstance(rows, list) and 0 < len(rows) <= 4096
    # Select internally; no hostname, address, token or device identifier is logged.
    device_id = int(rows[0]["device_id"])
    device = provider.device(device_id)
    ports = provider.ports(device.device_id)
    assert device.device_id == device_id
    assert len(ports) <= 4096
    assert all(port.device_id == device_id for port in ports)


@pytest.mark.integration
def test_approved_exact_mapping_reads_live_device_without_inferred_port() -> None:
    if os.environ.get("RUN_LIBRENMS_LAB_TESTS") != "1":
        pytest.skip("separate LibreNMS read-only lab channel is not configured")
    config = MonitoringConfig.from_env()
    registry = MappingRegistry.from_file(config.mapping_file)
    if not registry.devices:
        pytest.skip("owner-approved exact-name mapping registry is not configured")
    url = config.validate_endpoint(config.librenms_url)
    assert url and config.librenms_token_file
    provider = LibreNMSProvider(
        JsonTransport(
            url,
            {"Authorization": "Bearer " + protected_text(config.librenms_token_file)},
            config.timeout_seconds,
            config.ca_file,
        )
    )
    mapped = registry.devices[0]
    connection = SimpleNamespace(
        id=uuid4(),
        ip_groups=[{"rows": [{"switch_id": mapped.netup_switch_id, "port_id": 1}]}],
        field_meta={"device": {"quality": "STALE"}, "port": {"quality": "STALE"}},
    )
    report = CorrelationEngine(registry, provider, None).check(connection)
    assert report.device_match == DataQuality.STALE
    assert report.interface_match == DataQuality.UNKNOWN
    assert report.observations[0].source == "LIBRENMS"
    assert report.observations[0].result in {"UP", "DOWN"}
    assert "device_mapping_unique_exact_sysname" in report.limitations
