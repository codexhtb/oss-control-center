"""Raw UTM5 source values, separate from public Subscriber 360 models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, repr=False)
class NetupAccount:
    account_id: int
    is_primary: bool
    balance: float | None = None
    credit: float | None = None
    is_blocked: int | None = None


@dataclass(frozen=True, repr=False)
class NetupRange:
    range_id: int
    first_ip: int
    last_ip: int


@dataclass(frozen=True, repr=False)
class NetupGroupRow:
    row_id: int
    ip_type: int
    ip: int
    mask: int
    ip_ext: int
    mask_ext: int
    pool_id: int
    switch_id: int
    switch_name: str | None
    port_id: int
    vlan_id: int
    ranges: tuple[NetupRange, ...]


@dataclass(frozen=True, repr=False)
class NetupGroup:
    group_id: int
    rows: tuple[NetupGroupRow, ...]


@dataclass(frozen=True, repr=False)
class NetupService:
    link_id: int
    account_id: int
    name: str
    service_type: int
    groups: tuple[NetupGroup, ...]
    tariff_name: str | None = None
    billing_blocked: int | None = None


@dataclass(frozen=True, repr=False)
class NetupSubscriber:
    user_id: int
    requested_account_id: int
    full_name: str | None
    is_juridical: bool
    mobile_phone: str
    work_phone: str
    home_phone: str
    email: str
    actual_address: str | None
    accounts: tuple[NetupAccount, ...]
    services: tuple[NetupService, ...]
    fetched_at: datetime
    source_as_of: datetime | None = None
    building: str | None = None
