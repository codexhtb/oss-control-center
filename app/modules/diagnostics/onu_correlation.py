"""Do not equate a billing VLAN or port with a verified subscriber ONU."""

from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import MappingRegistry
from app.modules.diagnostics.correlation import _identifiers
from app.modules.diagnostics.http import SourceUnavailable
from app.modules.diagnostics.onu import BdcomOnuReader, OnuResult, normalize_candidate
from app.modules.tickets.models import ConnectionSnapshot


def lookup_onu_candidates(
    connection: ConnectionSnapshot,
    mappings: MappingRegistry,
    reader: BdcomOnuReader | None,
) -> OnuResult:
    if connection.access_type == "ETHERNET":
        return OnuResult(availability="UNKNOWN", limitations=["ethernet_has_no_confirmed_onu"])
    if reader is None:
        return OnuResult(availability="UNAVAILABLE", limitations=["onu_source_not_configured"])
    switch_id, _, device_quality, _ = _identifiers(connection)
    if device_quality == DataQuality.CONFLICT or (
        (connection.field_meta or {}).get("device", {}).get("quality") == "CONFLICT"
    ):
        return OnuResult(availability="CONFLICT", limitations=["netup_device_conflict"])
    if not switch_id:
        return OnuResult(availability="UNKNOWN", limitations=["netup_device_unknown"])
    mapping = mappings.device(switch_id)
    if not mapping or not mapping.librenms_device_id:
        return OnuResult(availability="UNKNOWN", limitations=["olt_mapping_unconfirmed"])
    vlans = {
        int(row["vlan_id"])
        for group in connection.ip_groups
        for row in group.get("rows", [])
        if row.get("switch_id") is not None
        and str(row["switch_id"]) == switch_id
        and row.get("vlan_id") is not None
        and str(row["vlan_id"]).isdecimal()
        and int(row["vlan_id"]) > 0
    }
    if len(vlans) > 1:
        return OnuResult(availability="CONFLICT", limitations=["netup_vlan_conflict"])
    if not vlans:
        return OnuResult(availability="UNKNOWN", limitations=["netup_vlan_unknown"])
    try:
        olt, rows, truncated = reader.candidates(mapping.librenms_device_id, next(iter(vlans)))
    except SourceUnavailable:
        return OnuResult(availability="UNAVAILABLE", limitations=["onu_source_unavailable"])
    if not olt:
        return OnuResult(availability="UNKNOWN", limitations=["olt_inventory_missing"])
    candidates = [candidate for row in rows if (candidate := normalize_candidate(row, olt))]
    limitations = ["onu_association_unconfirmed", "source_local_timezone_unverified"]
    if truncated:
        limitations.append("onu_candidates_truncated")
    if any(candidate.optical_measured_at for candidate in candidates):
        limitations.append("optical_values_may_have_different_ages")
    return OnuResult(
        availability="CONFLICT"
        if truncated or len(candidates) > 1
        else "AVAILABLE"
        if candidates
        else "UNKNOWN",
        candidates=candidates,
        limitations=limitations,
    )
