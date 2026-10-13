"""UTM5 source values -> existing Subscriber 360 plus lossless group structure."""

from datetime import UTC, datetime

from app.modules.billing.netup.dto import NetupGroup, NetupSubscriber
from app.modules.billing.provider import BillingAccount, BillingIncomplete, Service, Subscriber360
from app.modules.billing.quality import DataQuality, FieldOrigin
from app.modules.network.models import AccessType, Connection, DhcpRange, IpGroup, IpGroupRow


def origin(when: datetime, quality: DataQuality) -> FieldOrigin:
    return FieldOrigin(source="NETUP", observed_at=when.astimezone(UTC), quality=quality)


def present(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def bounded(value: str | None, maximum: int) -> str | None:
    if not present(value):
        return None
    if len(value) > maximum:
        raise BillingIncomplete from None
    return value


def field(value: object, when: datetime, *, semantic_known: bool = True) -> FieldOrigin:
    return origin(
        when,
        DataQuality.KNOWN if semantic_known and present(value) else DataQuality.UNKNOWN,
    )


def _phone(raw: NetupSubscriber, when: datetime) -> tuple[str | None, FieldOrigin]:
    candidates = {
        item.strip() for item in (raw.mobile_phone, raw.work_phone, raw.home_phone) if present(item)
    }
    if len(candidates) > 1 or any(len(item) > 50 for item in candidates):
        return None, origin(when, DataQuality.CONFLICT)
    value = next(iter(candidates)) if candidates else None
    return value, field(value, when)


def _group(group: NetupGroup, when: datetime) -> IpGroup:
    rows = []
    for item in group.rows:
        ranges = [
            DhcpRange(range_id=str(r.range_id), first_ip_raw=r.first_ip, last_ip_raw=r.last_ip)
            for r in item.ranges
        ]
        values = {
            "ip_raw": item.ip,
            "mask_raw": item.mask,
            "ip_ext_raw": item.ip_ext,
            "mask_ext_raw": item.mask_ext,
            "dhcp_pool_id": str(item.pool_id) if item.pool_id > 0 else None,
            "pool_id_raw": item.pool_id,
            "switch_id": str(item.switch_id) if item.switch_id > 0 else None,
            "switch_id_raw": item.switch_id,
            "device": item.switch_name if item.switch_id > 0 else None,
            "port_id": item.port_id,
            "vlan_id": item.vlan_id,
        }
        uncertain_raw = {
            "ip_raw",
            "mask_raw",
            "ip_ext_raw",
            "mask_ext_raw",
            "pool_id_raw",
            "switch_id_raw",
        }
        metadata = {
            "row_id": origin(when, DataQuality.KNOWN),
            "ip_type_code": origin(when, DataQuality.UNKNOWN),
            "dhcp_ranges": origin(when, DataQuality.KNOWN if ranges else DataQuality.UNKNOWN),
            **{
                key: field(
                    value,
                    when,
                    semantic_known=(
                        key not in uncertain_raw
                        and (key not in {"port_id", "vlan_id"} or bool(value and value > 0))
                    ),
                )
                for key, value in values.items()
            },
        }
        rows.append(
            IpGroupRow(
                row_id=str(item.row_id),
                ip_type_code=item.ip_type,
                dhcp_ranges=ranges,
                field_meta=metadata,
                **values,
            )
        )
    return IpGroup(
        group_id=str(group.group_id),
        rows=rows,
        field_meta={"group_id": origin(when, DataQuality.KNOWN)},
    )


def _connection(service_id: str, groups: tuple[NetupGroup, ...], when: datetime) -> Connection:
    rows = [row for group in groups for row in group.rows]
    topologies = {
        (row.switch_id, row.port_id, row.vlan_id)
        for row in rows
        if row.switch_id > 0 and row.port_id > 0 and row.vlan_id > 0
    }
    explicit_values = (
        {row.switch_id for row in rows if row.switch_id > 0},
        {row.port_id for row in rows if row.port_id > 0},
        {row.vlan_id for row in rows if row.vlan_id > 0},
    )
    conflicting = any(len(values) > 1 for values in explicit_values)
    if len(topologies) == 1 and not conflicting:
        switch_id, port_id, vlan_id = next(iter(topologies))
        names = {
            row.switch_name
            for row in rows
            if row.switch_id == switch_id and present(row.switch_name)
        }
        device = next(iter(names)) if len(names) == 1 else None
        port, vlan = str(port_id), str(vlan_id)
        quality = DataQuality.CONFLICT if len(names) > 1 else DataQuality.KNOWN
        if quality == DataQuality.CONFLICT:
            device = port = vlan = None
    else:
        device = port = vlan = None
        quality = (
            DataQuality.CONFLICT if conflicting or len(topologies) > 1 else DataQuality.UNKNOWN
        )
    group_id = str(groups[0].group_id) if len(groups) == 1 else None
    group_quality = DataQuality.KNOWN if len(groups) == 1 else DataQuality.CONFLICT
    if not groups:
        group_quality = DataQuality.UNKNOWN
    return Connection(
        access_type=AccessType.UNKNOWN,
        address=None,
        device=device,
        port=port,
        vlan=vlan,
        ip_group=group_id,
        onu_id=None,
        service_id=service_id,
        ip_groups=[_group(group, when) for group in groups],
        field_meta={
            "access_type": origin(when, DataQuality.UNKNOWN),
            "address": origin(when, DataQuality.UNKNOWN),
            "device": origin(
                when,
                quality
                if device is not None or quality == DataQuality.CONFLICT
                else DataQuality.UNKNOWN,
            ),
            "port": origin(when, quality),
            "vlan": origin(when, quality),
            "ip_group": origin(when, group_quality),
            "onu_id": origin(when, DataQuality.UNKNOWN),
            "service_id": origin(when, DataQuality.KNOWN),
        },
    )


def normalize_netup(raw: NetupSubscriber) -> Subscriber360:
    when = raw.source_as_of or raw.fetched_at
    if when.tzinfo is None or when.utcoffset() is None:
        raise BillingIncomplete from None
    phone, phone_origin = _phone(raw, when)
    full_name = bounded(raw.full_name, 200)
    email = bounded(raw.email, 200)
    actual_address = bounded(raw.actual_address, 300)
    services = [
        Service(
            service_id=str(item.link_id),
            name=bounded(item.name, 200),
            account_id=str(item.account_id),
            field_meta={
                "service_id": origin(when, DataQuality.KNOWN),
                "name": field(item.name, when),
                "account_id": origin(when, DataQuality.KNOWN),
            },
        )
        for item in raw.services
    ]
    connections = [
        _connection(str(item.link_id), item.groups, when) for item in raw.services if item.groups
    ]
    card = Subscriber360(
        subscriber_id=str(raw.user_id),
        billing_account=str(raw.requested_account_id),
        full_name=full_name,
        organization_name=None,
        phone=phone,
        email=email,
        actual_address=actual_address,
        accounts=[
            BillingAccount(
                account_id=str(item.account_id),
                is_primary=item.is_primary,
                field_meta={
                    "account_id": origin(when, DataQuality.KNOWN),
                    "is_primary": origin(when, DataQuality.KNOWN),
                },
            )
            for item in raw.accounts
        ],
        contracts=[],
        services=services,
        connections=connections,
        field_meta={
            "subscriber_id": origin(when, DataQuality.KNOWN),
            "billing_account": origin(when, DataQuality.KNOWN),
            "full_name": field(full_name, when),
            "organization_name": origin(when, DataQuality.UNKNOWN),
            "phone": phone_origin,
            "email": field(email, when),
            "actual_address": field(actual_address, when),
            "contracts": origin(when, DataQuality.UNKNOWN),
        },
        source="NETUP",
        fetched_at=raw.fetched_at,
    )
    if raw.source_as_of is not None:
        metadata_sets = [card.field_meta]
        metadata_sets.extend(item.field_meta for item in card.accounts)
        metadata_sets.extend(item.field_meta for item in card.services)
        for connection in card.connections:
            metadata_sets.append(connection.field_meta)
            for group in connection.ip_groups:
                metadata_sets.append(group.field_meta)
                metadata_sets.extend(row.field_meta for row in group.rows)
        for metadata in metadata_sets:
            for item in metadata.values():
                if item.quality == DataQuality.KNOWN:
                    item.quality = DataQuality.STALE
    return card
