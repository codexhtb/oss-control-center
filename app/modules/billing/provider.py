"""Read-only provider contract and normalized Subscriber 360 domain object."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.billing.quality import DataQuality, FieldOrigin
from app.modules.network.models import Connection


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(
        min_length=1, max_length=100, validation_alias=AliasChoices("contract_id", "id")
    )
    label: str | None = Field(default=None, max_length=200)
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(
        min_length=1, max_length=100, validation_alias=AliasChoices("service_id", "id")
    )
    name: str | None = Field(default=None, max_length=200)
    account_id: str | None = Field(default=None, max_length=100)
    tariff_name: str | None = Field(default=None, max_length=200)
    billing_active: bool | None = None
    billing_blocked: bool | None = None
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class FinancialAccount(BaseModel):
    """Raw billing observations, never a network health verdict."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=100)
    balance: Decimal | None = None
    credit: Decimal | None = None
    account_blocked: bool | None = None
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class BillingAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=100)
    is_primary: bool
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class Contact(BaseModel):
    """A source-specific phone observation; never a preferred-number decision."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mobile", "work", "home"]
    value: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    quality: DataQuality

    @field_validator("observed_at")
    @classmethod
    def require_contact_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Contact observation requires a timezone")
        return value.astimezone(UTC)


class Subscriber360(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriber_id: str | None = Field(max_length=100)
    billing_account: str = Field(min_length=1, max_length=100)
    full_name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    contacts: list[Contact] = Field(default_factory=list, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    actual_address: str | None = Field(default=None, max_length=300)
    house_number: str | None = Field(default=None, max_length=100)
    accounts: list[BillingAccount] = Field(default_factory=list)
    financial_accounts: list[FinancialAccount] = Field(default_factory=list)
    contracts: list[Contract]
    services: list[Service]
    connections: list[Connection]
    field_meta: dict[str, FieldOrigin]
    source: Literal["MOCK", "NETUP"]
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def require_utc_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Fetched time must include a timezone")
        return value.astimezone(UTC)


class BillingUnavailable(Exception):
    """The provider cannot be queried; distinct from a missing subscriber."""


class BillingAmbiguous(Exception):
    """One account has multiple live subscriber associations."""


class BillingIncomplete(Exception):
    """The account exists but required source relationships are incomplete."""


class BillingInvalidAccount(Exception):
    """The provider cannot safely map this account string to a source key."""


class BillingProvider(Protocol):
    def lookup_by_account(self, account_no: str) -> Subscriber360 | None: ...


class UnavailableBillingProvider:
    """Safe failure when an explicitly selected source lacks runtime configuration."""

    def lookup_by_account(self, account_no: str) -> Subscriber360 | None:
        raise BillingUnavailable from None
