"""Conservative, approved exact-name matching for laboratory inventory exports."""

from collections import Counter
from ipaddress import IPv4Address

from app.modules.diagnostics.config import DeviceMapping, MappingRegistry


def unique_exact_mappings(netup_rows: list[dict], libre_rows: list[dict]) -> MappingRegistry:
    netup_names = Counter(
        row.get("name")
        for row in netup_rows
        if isinstance(row.get("name"), str) and row["name"].strip()
    )
    libre_names = Counter(
        row.get("sysName")
        for row in libre_rows
        if isinstance(row.get("sysName"), str) and row["sysName"].strip()
    )
    unique_libre = {
        row["sysName"]: row["device_id"]
        for row in libre_rows
        if isinstance(row.get("sysName"), str)
        and libre_names[row["sysName"]] == 1
        and str(row.get("device_id", "")).isdecimal()
        and int(row["device_id"]) > 0
    }
    mappings = [
        DeviceMapping(
            netup_switch_id=str(row["id"]),
            librenms_device_id=int(unique_libre[row["name"]]),
            match_basis="unique_exact_sysname",
            ports=[],
        )
        for row in netup_rows
        if isinstance(row.get("name"), str)
        and netup_names[row["name"]] == 1
        and row["name"] in unique_libre
    ]
    return MappingRegistry(devices=sorted(mappings, key=lambda item: int(item.netup_switch_id)))


def preserve_verified_mappings(
    exact: MappingRegistry,
    approved: MappingRegistry,
    netup_rows: list[dict],
    libre_rows: list[dict],
) -> MappingRegistry:
    """Carry forward explicit mappings only while both inventories still identify one IP."""

    def netup_ip(row: dict) -> str | None:
        value = row.get("ip")
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value == 0
            or not -(1 << 31) <= value < (1 << 32)
        ):
            return None
        try:
            return str(IPv4Address(value & 0xFFFFFFFF))
        except ValueError:
            return None

    netup_id_counts = Counter(str(row.get("id")) for row in netup_rows)
    libre_id_counts = Counter(str(row.get("device_id")) for row in libre_rows)
    netup_by_id = {str(row.get("id")): row for row in netup_rows}
    libre_by_id = {str(row.get("device_id")): row for row in libre_rows}
    netup_ips = Counter(ip for row in netup_rows if (ip := netup_ip(row)) is not None)
    libre_ips = Counter(row.get("hostname") for row in libre_rows if row.get("hostname"))
    merged = list(exact.devices)
    netup_ids = {item.netup_switch_id for item in merged}
    libre_ids = {item.librenms_device_id for item in merged if item.librenms_device_id}
    for mapping in approved.devices:
        if mapping.match_basis != "manual_verified":
            continue
        netup = netup_by_id.get(mapping.netup_switch_id)
        libre = libre_by_id.get(str(mapping.librenms_device_id))
        address = netup_ip(netup) if netup is not None else None
        if (
            netup is None
            or libre is None
            or netup_id_counts[mapping.netup_switch_id] != 1
            or libre_id_counts[str(mapping.librenms_device_id)] != 1
            or address is None
            or libre.get("hostname") != address
            or netup_ips[address] != 1
            or libre_ips[address] != 1
            or mapping.netup_switch_id in netup_ids
            or mapping.librenms_device_id in libre_ids
        ):
            raise ValueError("approved monitoring mapping requires manual inventory review")
        merged.append(mapping)
        netup_ids.add(mapping.netup_switch_id)
        libre_ids.add(mapping.librenms_device_id)
    return MappingRegistry(devices=sorted(merged, key=lambda item: int(item.netup_switch_id)))
