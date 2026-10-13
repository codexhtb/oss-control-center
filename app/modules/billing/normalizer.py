"""Normalize source DTOs without inferring missing or conflicting values."""

from datetime import UTC

from app.modules.billing.provider import Contract, Service, Subscriber360
from app.modules.billing.quality import DataQuality, FieldOrigin
from app.modules.billing.source_dto import SourceField, SourceSubscriber
from app.modules.network.models import AccessType, Connection


def _value[T](field: SourceField[T]) -> T | None:
    if field.quality in {DataQuality.UNKNOWN, DataQuality.CONFLICT, DataQuality.UNAVAILABLE}:
        return None
    return field.value


def _origin(field: SourceField) -> FieldOrigin:
    if field.observed_at.tzinfo is None or field.observed_at.utcoffset() is None:
        raise ValueError("Source timestamps must have a timezone")
    quality = field.quality
    if field.value is None and quality in {DataQuality.KNOWN, DataQuality.STALE}:
        quality = DataQuality.UNKNOWN
    return FieldOrigin(
        source=field.source, observed_at=field.observed_at.astimezone(UTC), quality=quality
    )


def normalize_source(raw: SourceSubscriber) -> Subscriber360:
    account = _value(raw.billing_account)
    if account is None:
        raise ValueError("A found subscriber must have a billing account")
    if raw.fetched_at.tzinfo is None or raw.fetched_at.utcoffset() is None:
        raise ValueError("Fetched time must have a timezone")

    subscriber_fields = {
        key: getattr(raw, key)
        for key in (
            "subscriber_id",
            "billing_account",
            "full_name",
            "organization_name",
            "phone",
            "email",
        )
    }
    contracts = []
    for item in raw.contracts:
        contract_id = _value(item.contract_id)
        if contract_id is None:
            raise ValueError("Contract without source ID cannot be selected")
        contracts.append(
            Contract(
                contract_id=contract_id,
                label=_value(item.label),
                field_meta={"contract_id": _origin(item.contract_id), "label": _origin(item.label)},
            )
        )
    services = []
    for item in raw.services:
        service_id = _value(item.service_id)
        if service_id is None:
            raise ValueError("Service without source ID cannot be selected")
        services.append(
            Service(
                service_id=service_id,
                name=_value(item.name),
                field_meta={"service_id": _origin(item.service_id), "name": _origin(item.name)},
            )
        )
    if len({item.service_id for item in services}) != len(services):
        raise ValueError("Duplicate service IDs in provider response")
    connections = []
    for item in raw.connections:
        fields = {
            key: getattr(item, key)
            for key in ("access_type", "address", "device", "port", "vlan", "ip_group", "onu_id")
        }
        access_type = _value(item.access_type) or AccessType.UNKNOWN
        onu_id = _value(item.onu_id) if access_type in {AccessType.EPON, AccessType.GPON} else None
        origins = {key: _origin(field) for key, field in fields.items()}
        if access_type not in {AccessType.EPON, AccessType.GPON}:
            origins["onu_id"] = FieldOrigin(
                source=item.onu_id.source,
                observed_at=item.onu_id.observed_at.astimezone(UTC),
                quality=DataQuality.UNKNOWN,
            )
        connections.append(
            Connection(
                access_type=access_type,
                address=_value(item.address),
                device=_value(item.device),
                port=_value(item.port),
                vlan=_value(item.vlan),
                ip_group=_value(item.ip_group),
                onu_id=onu_id,
                field_meta=origins,
            )
        )
    return Subscriber360(
        subscriber_id=_value(raw.subscriber_id),
        billing_account=account,
        full_name=_value(raw.full_name),
        organization_name=_value(raw.organization_name),
        phone=_value(raw.phone),
        email=_value(raw.email),
        contracts=contracts,
        services=services,
        connections=connections,
        field_meta={key: _origin(field) for key, field in subscriber_fields.items()},
        source=raw.billing_account.source,
        fetched_at=raw.fetched_at.astimezone(UTC),
    )
