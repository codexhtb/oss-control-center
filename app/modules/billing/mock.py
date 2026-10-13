"""Deterministic synthetic scenarios; no external calls or production identifiers."""

from datetime import UTC, datetime, timedelta

from app.modules.billing.normalizer import normalize_source
from app.modules.billing.provider import BillingUnavailable, Subscriber360
from app.modules.billing.quality import DataQuality
from app.modules.billing.source_dto import (
    SourceConnection,
    SourceContract,
    SourceField,
    SourceService,
    SourceSubscriber,
)
from app.modules.network.models import AccessType


def _field[T](
    value: T | None, when: datetime, quality: DataQuality | None = None
) -> SourceField[T]:
    return SourceField(
        value=value,
        observed_at=when,
        quality=quality or (DataQuality.KNOWN if value is not None else DataQuality.UNKNOWN),
        source="MOCK",
    )


def _contract(key: str, now: datetime) -> SourceContract:
    return SourceContract(
        contract_id=_field(f"MOCK-CONTRACT-{key}", now),
        label=_field(f"Синтетический договор {key}", now),
    )


def _service(key: str, now: datetime) -> SourceService:
    return SourceService(
        service_id=_field(f"MOCK-SERVICE-{key}", now),
        name=_field(f"Синтетическая интернет-услуга {key}", now),
    )


def _connection(
    access_type: AccessType,
    key: str,
    now: datetime,
    *,
    missing_port: bool = False,
    missing_vlan: bool = False,
    missing_onu: bool = False,
    stale: bool = False,
    conflict: bool = False,
) -> SourceConnection:
    observed = now - timedelta(days=45) if stale else now
    return SourceConnection(
        access_type=_field(access_type, now),
        address=_field(f"MOCK-адрес подключения {key}", now),
        device=_field(
            f"mock-{access_type.lower()}-device-{key.lower()}",
            observed,
            DataQuality.STALE if stale else None,
        ),
        port=_field(None if missing_port else f"MOCK-PORT-{key}", now),
        vlan=_field(
            None if missing_vlan or conflict else f"MOCK-VLAN-{key}",
            now,
            DataQuality.CONFLICT if conflict else None,
        ),
        ip_group=_field(f"MOCK-IP-GROUP-{key}", now),
        onu_id=_field(
            None if access_type == AccessType.ETHERNET or missing_onu else f"MOCK-ONU-{key}",
            now,
        ),
    )


class MockBillingProvider:
    """Read-only synthetic provider with explicit absence and failure scenarios."""

    def lookup_by_account(self, account_no: str) -> Subscriber360 | None:
        if account_no == "MOCK-UNAVAILABLE-001":
            raise BillingUnavailable
        now = datetime.now(UTC)
        raw = self._scenario(account_no, now)
        return normalize_source(raw) if raw is not None else None

    def _scenario(self, account_no: str, now: datetime) -> SourceSubscriber | None:
        if account_no == "MOCK-ETH-001":
            key, connections, services = (
                "ETH",
                (_connection(AccessType.ETHERNET, "ETH", now),),
                ("ETH",),
            )
        elif account_no == "MOCK-EPON-001":
            key, connections, services = (
                "EPON",
                (_connection(AccessType.EPON, "EPON", now),),
                ("EPON",),
            )
        elif account_no == "MOCK-GPON-001":
            key, connections, services = (
                "GPON",
                (
                    _connection(AccessType.GPON, "GPON-A", now),
                    _connection(AccessType.GPON, "GPON-B", now, missing_onu=True),
                ),
                ("GPON-A", "GPON-B"),
            )
        elif account_no == "MOCK-PARTIAL-001":
            key, connections, services = (
                "PARTIAL",
                (
                    _connection(
                        AccessType.EPON,
                        "PARTIAL",
                        now,
                        missing_port=True,
                        missing_vlan=True,
                        missing_onu=True,
                    ),
                ),
                ("PARTIAL",),
            )
        elif account_no == "MOCK-STALE-001":
            key, connections, services = (
                "STALE",
                (_connection(AccessType.ETHERNET, "STALE", now, stale=True),),
                ("STALE",),
            )
        elif account_no == "MOCK-CONFLICT-001":
            key, connections, services = (
                "CONFLICT",
                (_connection(AccessType.GPON, "CONFLICT", now, conflict=True),),
                ("CONFLICT",),
            )
        elif account_no == "MOCK-NO-CONNECTION-001":
            key, connections, services = "NO-CONNECTION", (), ("NO-CONNECTION",)
        else:
            return None
        is_organization = key == "GPON"
        return SourceSubscriber(
            subscriber_id=_field(f"MOCK-SUBSCRIBER-{key}", now),
            billing_account=_field(account_no, now),
            full_name=_field(None if is_organization else f"Тестовый абонент {key}", now),
            organization_name=_field("MOCK Организация GPON" if is_organization else None, now),
            phone=_field("+0-000-000-0001" if key == "ETH" else None, now),
            email=_field(f"{key.lower()}@example.invalid", now),
            contracts=(_contract(key, now),),
            services=tuple(_service(item, now) for item in services),
            connections=connections,
            fetched_at=now,
        )
