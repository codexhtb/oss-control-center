"""Provider input DTOs, independent of normalized Subscriber 360 and database rows."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.modules.billing.quality import DataQuality
from app.modules.network.models import AccessType


@dataclass(frozen=True)
class SourceField[T]:
    value: T | None
    observed_at: datetime
    quality: DataQuality
    source: Literal["MOCK", "NETUP"]


@dataclass(frozen=True)
class SourceContract:
    contract_id: SourceField[str]
    label: SourceField[str]


@dataclass(frozen=True)
class SourceService:
    service_id: SourceField[str]
    name: SourceField[str]


@dataclass(frozen=True)
class SourceConnection:
    access_type: SourceField[AccessType]
    address: SourceField[str]
    device: SourceField[str]
    port: SourceField[str]
    vlan: SourceField[str]
    ip_group: SourceField[str]
    onu_id: SourceField[str]


@dataclass(frozen=True)
class SourceSubscriber:
    subscriber_id: SourceField[str]
    billing_account: SourceField[str]
    full_name: SourceField[str]
    organization_name: SourceField[str]
    phone: SourceField[str]
    email: SourceField[str]
    contracts: tuple[SourceContract, ...]
    services: tuple[SourceService, ...]
    connections: tuple[SourceConnection, ...]
    fetched_at: datetime
