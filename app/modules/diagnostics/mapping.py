"""Conservative, approved exact-name matching for laboratory inventory exports."""

from collections import Counter

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
