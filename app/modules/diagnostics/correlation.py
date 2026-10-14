"""Conservative NetUP-to-monitoring correlation; no inferred interface index."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import MappingRegistry
from app.modules.diagnostics.http import SourceUnavailable
from app.modules.diagnostics.providers import IcingaProvider, LibreNMSProvider
from app.modules.tickets.models import ConnectionSnapshot

Source = Literal["LIBRENMS", "ICINGA"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source
    measured_object: str = Field(max_length=240)
    result: str = Field(max_length=40)
    observed_at: datetime | None
    retrieved_at: datetime
    quality: DataQuality


class DiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_snapshot_id: UUID
    checked_at: datetime
    device_match: DataQuality
    interface_match: DataQuality
    observations: list[Observation] = Field(max_length=110)
    limitations: list[str] = Field(max_length=20)


def _quality(observed_at: datetime | None, checked_at: datetime) -> DataQuality:
    if observed_at is None:
        return DataQuality.UNKNOWN
    if observed_at > checked_at + timedelta(minutes=5) or checked_at - observed_at > timedelta(
        minutes=15
    ):
        return DataQuality.STALE
    return DataQuality.KNOWN


def _identifiers(
    connection: ConnectionSnapshot,
) -> tuple[str | None, int | None, DataQuality, DataQuality]:
    switches: set[str] = set()
    ports: set[int] = set()
    incomplete_port = False
    for group in connection.ip_groups:
        for row in group.get("rows", []):
            switch = row.get("switch_id")
            port = row.get("port_id")
            if switch is not None and str(switch).isdecimal() and int(switch) > 0:
                switches.add(str(switch))
                if port is None or not str(port).isdecimal() or int(port) < 1:
                    incomplete_port = True
                else:
                    ports.add(int(port))
    if len(switches) > 1:
        return None, None, DataQuality.CONFLICT, DataQuality.CONFLICT
    if not switches:
        return None, None, DataQuality.UNKNOWN, DataQuality.UNKNOWN
    switch_id = next(iter(switches))
    if len(ports) > 1:
        return switch_id, None, DataQuality.KNOWN, DataQuality.CONFLICT
    if incomplete_port or not ports:
        return switch_id, None, DataQuality.KNOWN, DataQuality.UNKNOWN
    return switch_id, next(iter(ports)), DataQuality.KNOWN, DataQuality.KNOWN


class CorrelationEngine:
    def __init__(
        self,
        mappings: MappingRegistry,
        librenms: LibreNMSProvider | None,
        icinga: IcingaProvider | None,
    ):
        self.mappings = mappings
        self.librenms = librenms
        self.icinga = icinga

    def check(self, connection: ConnectionSnapshot) -> DiagnosticReport:
        now = datetime.now(UTC)
        observations: list[Observation] = []
        limitations: list[str] = []
        metadata = getattr(connection, "field_meta", {}) or {}
        switch_id, port_id, device_quality, port_quality = _identifiers(connection)
        mapping = self.mappings.device(switch_id) if switch_id else None
        if metadata.get("device", {}).get("quality") == "CONFLICT":
            device_quality = DataQuality.CONFLICT
            port_quality = DataQuality.CONFLICT
            mapping = None
        if mapping and mapping.match_basis == "unique_exact_sysname":
            limitations.append("device_mapping_unique_exact_sysname")
        if switch_id and mapping is None:
            device_quality = DataQuality.UNKNOWN
            limitations.append("device_mapping_unconfirmed")
            port_quality = (
                DataQuality.UNKNOWN if port_quality != DataQuality.CONFLICT else port_quality
            )
        if (
            mapping
            and port_id is not None
            and not any(item.netup_port_id == port_id for item in mapping.ports)
        ):
            port_quality = DataQuality.UNKNOWN
            limitations.append("interface_mapping_unconfirmed")
        vlans = {
            str(row.get("vlan_id"))
            for group in connection.ip_groups
            for row in group.get("rows", [])
            if row.get("vlan_id") is not None
            and str(row["vlan_id"]).isdecimal()
            and int(row["vlan_id"]) > 0
        }
        if len(vlans) > 1:
            limitations.append("conflicting_netup_vlans")
        if device_quality == DataQuality.CONFLICT:
            limitations.append("conflicting_netup_switches")
        if port_quality == DataQuality.CONFLICT:
            limitations.append("conflicting_netup_ports")
        if not switch_id:
            limitations.append("netup_switch_unknown")

        if mapping and mapping.librenms_device_id and self.librenms:
            try:
                device = self.librenms.device(mapping.librenms_device_id)
                observations.append(
                    Observation(
                        source="LIBRENMS",
                        measured_object=f"device:{device.device_id}",
                        result=device.state,
                        observed_at=device.observed_at,
                        retrieved_at=now,
                        quality=_quality(device.observed_at, now),
                    )
                )
                if port_id is not None and port_quality == DataQuality.KNOWN:
                    port_mapping = next(
                        (p for p in mapping.ports if p.netup_port_id == port_id), None
                    )
                    if port_mapping is None:
                        port_quality = DataQuality.UNKNOWN
                        limitations.append("interface_mapping_unconfirmed")
                    else:
                        matches = [
                            port
                            for port in self.librenms.ports(device.device_id)
                            if port.port_id == port_mapping.librenms_port_id
                        ]
                        if len(matches) != 1:
                            port_quality = DataQuality.UNKNOWN
                            limitations.append("mapped_interface_missing")
                        else:
                            port = matches[0]
                            for name, state in (
                                ("ifOperStatus", port.oper_status),
                                ("ifAdminStatus", port.admin_status),
                            ):
                                observations.append(
                                    Observation(
                                        source="LIBRENMS",
                                        measured_object=f"interface:{port.port_id}:{name}",
                                        result=state.upper()
                                        if state in {"up", "down"}
                                        else "UNKNOWN",
                                        observed_at=port.observed_at,
                                        retrieved_at=now,
                                        quality=_quality(port.observed_at, now),
                                    )
                                )
                elif port_quality == DataQuality.UNKNOWN and port_id is None:
                    limitations.append("netup_port_unknown")
            except SourceUnavailable:
                limitations.append("librenms_unavailable")
                observations.append(
                    Observation(
                        source="LIBRENMS",
                        measured_object="mapped_device",
                        result="UNAVAILABLE",
                        observed_at=None,
                        retrieved_at=now,
                        quality=DataQuality.UNAVAILABLE,
                    )
                )
        elif mapping and mapping.librenms_device_id:
            limitations.append("librenms_not_configured")

        if mapping and mapping.icinga_host and self.icinga:
            try:
                host = self.icinga.host(mapping.icinga_host)
                observations.append(
                    Observation(
                        source="ICINGA",
                        measured_object="host",
                        result=host.state,
                        observed_at=host.observed_at,
                        retrieved_at=now,
                        quality=_quality(host.observed_at, now),
                    )
                )
                for service in self.icinga.services(mapping.icinga_host):
                    observations.append(
                        Observation(
                            source="ICINGA",
                            measured_object=f"service:{service.name}",
                            result=service.state,
                            observed_at=service.observed_at,
                            retrieved_at=now,
                            quality=_quality(service.observed_at, now),
                        )
                    )
            except SourceUnavailable:
                limitations.append("icinga_unavailable")
                observations.append(
                    Observation(
                        source="ICINGA",
                        measured_object="mapped_host",
                        result="UNAVAILABLE",
                        observed_at=None,
                        retrieved_at=now,
                        quality=DataQuality.UNAVAILABLE,
                    )
                )
        elif mapping and mapping.icinga_host:
            limitations.append("icinga_not_configured")
        if not mapping or (mapping.librenms_device_id is None and mapping.icinga_host is None):
            limitations.append("monitoring_mapping_missing")
        if (
            metadata.get("device", {}).get("quality") == "STALE"
            and device_quality == DataQuality.KNOWN
        ):
            device_quality = DataQuality.STALE
        if metadata.get("port", {}).get("quality") == "STALE" and port_quality == DataQuality.KNOWN:
            port_quality = DataQuality.STALE
        limitations.append("optical_measurement_not_verified")
        return DiagnosticReport(
            connection_snapshot_id=connection.id,
            checked_at=now,
            device_match=device_quality,
            interface_match=port_quality,
            observations=observations,
            limitations=list(dict.fromkeys(limitations)),
        )
