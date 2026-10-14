"""Correlate BDCOM ONU only by an approved OLT and the full PON/VLAN key."""

import re
from datetime import datetime

from app.modules.billing.quality import DataQuality
from app.modules.diagnostics.config import MappingRegistry
from app.modules.diagnostics.correlation import _identifiers
from app.modules.diagnostics.http import SourceUnavailable
from app.modules.diagnostics.onu import BdcomOnuReader, OnuMatch, OnuResult, normalize_candidate
from app.modules.tickets.models import ConnectionSnapshot


def lookup_onu_candidates(
    connection: ConnectionSnapshot,
    mappings: MappingRegistry,
    reader: BdcomOnuReader | None,
    sibling_connections: list[ConnectionSnapshot] | None = None,
) -> OnuResult:
    if connection.access_type == "ETHERNET":
        return OnuResult(availability="UNKNOWN", limitations=["ethernet_has_no_confirmed_onu"])
    if reader is None:
        return OnuResult(availability="UNAVAILABLE", limitations=["onu_source_not_configured"])
    switch_id, port_id, device_quality, port_quality = _identifiers(connection)
    if device_quality == DataQuality.CONFLICT or (
        (connection.field_meta or {}).get("device", {}).get("quality") == "CONFLICT"
    ):
        return OnuResult(availability="CONFLICT", limitations=["netup_device_conflict"])
    if not switch_id:
        return OnuResult(availability="UNKNOWN", limitations=["netup_device_unknown"])
    mapping = mappings.device(switch_id)
    if not mapping or not mapping.librenms_device_id:
        return OnuResult(availability="UNKNOWN", limitations=["olt_mapping_unconfirmed"])
    if port_quality == DataQuality.CONFLICT or (
        (connection.field_meta or {}).get("port", {}).get("quality") == "CONFLICT"
    ):
        return OnuResult(availability="CONFLICT", limitations=["netup_port_conflict"])
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
    vlan = next(iter(vlans))
    for sibling in sibling_connections or []:
        if sibling.id == connection.id:
            continue
        if any(
            str(row.get("switch_id")) == switch_id
            and str(row.get("port_id")) == str(port_id)
            and str(row.get("vlan_id")) == str(vlan)
            for group in sibling.ip_groups
            for row in group.get("rows", [])
        ):
            return OnuResult(
                availability="CONFLICT", limitations=["multiple_service_links_same_pon_vlan"]
            )
    # A matching number in separate IP-group rows is not a complete topology.
    complete_rows = [
        row
        for group in connection.ip_groups
        for row in group.get("rows", [])
        if str(row.get("switch_id")) == switch_id
        and row.get("port_id") is not None
        and str(row["port_id"]).isdecimal()
        and int(row["port_id"]) == port_id
        and str(row.get("vlan_id")) == str(vlan)
    ]
    trusted = [
        row
        for row in complete_rows
        if all(
            row.get("field_meta", {}).get(key, {}).get("source") == "NETUP"
            and row["field_meta"][key].get("quality") in {"KNOWN", "STALE"}
            for key in ("switch_id", "port_id", "vlan_id")
        )
    ]
    exact = bool(port_id and trusted)
    try:
        olt, rows, truncated = reader.candidates(
            mapping.librenms_device_id, vlan, port_id if exact else None
        )
        exact_rows = rows
        if exact and not rows and olt:
            _, rows, truncated = reader.candidates(mapping.librenms_device_id, vlan)
    except SourceUnavailable:
        return OnuResult(availability="UNAVAILABLE", limitations=["onu_source_unavailable"])
    if not olt:
        return OnuResult(availability="UNKNOWN", limitations=["olt_inventory_missing"])
    pon_type = str(olt.get("pon_type") or "").upper()
    offset = {"EPON": 6, "GPON": 13}.get(pon_type)
    if offset is None:
        return OnuResult(availability="UNKNOWN", limitations=["olt_pon_type_unknown"])
    if exact and not 1 <= port_id - offset <= 256:
        return OnuResult(availability="UNKNOWN", limitations=["netup_pon_port_invalid"])
    candidates = [candidate for row in rows if (candidate := normalize_candidate(row, olt))]
    expected_interface = rf"{pon_type}[0-9]+/{port_id - offset}:[0-9]+" if exact else None
    matched = (
        exact
        and not truncated
        and len(exact_rows) == 1
        and len(candidates) == 1
        and candidates[0].pon_type == pon_type
        and re.fullmatch(expected_interface, candidates[0].interface) is not None
    )
    limitations = ["source_local_timezone_unverified"]
    if not exact:
        limitations.extend(["onu_association_unconfirmed", "netup_pon_key_unverified"])
    elif not exact_rows:
        limitations.append("onu_composite_key_not_found")
        if rows:
            limitations.append("nearby_vlan_candidates_unconfirmed")
    elif len(exact_rows) > 1 or truncated:
        limitations.append("onu_composite_key_ambiguous")
    elif not matched:
        limitations.append("onu_identifier_missing_or_invalid")
    if truncated:
        limitations.append("onu_candidates_truncated")
    if any(candidate.optical_measured_at for candidate in candidates):
        limitations.append("optical_values_may_have_different_ages")
    origin = trusted[0]["field_meta"]["port_id"] if trusted else {}
    observed = origin.get("observed_at")
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed)
        except ValueError:
            observed = None
    if not isinstance(observed, datetime) or observed.tzinfo is None:
        observed = None
    if trusted and any(
        row["field_meta"][key].get("quality") == "STALE"
        for row in trusted
        for key in ("switch_id", "port_id", "vlan_id")
    ):
        limitations.append("netup_topology_stale")
    return OnuResult(
        availability="CONFLICT"
        if exact_rows and (truncated or len(exact_rows) > 1)
        else "AVAILABLE"
        if candidates and (matched or not exact)
        else "UNKNOWN",
        association="MATCHED_BY_RULE" if matched else "UNCONFIRMED",
        match=OnuMatch(
            netup_port=port_id,
            pon_port=port_id - offset,
            vlan=vlan,
            olt_mapping_basis=mapping.match_basis,
            billing_observed_at=observed,
        )
        if matched
        else None,
        candidates=candidates,
        limitations=limitations,
    )
