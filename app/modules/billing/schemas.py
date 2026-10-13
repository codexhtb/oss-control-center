"""Strict HTTP contract for lookup and stored Subscriber 360 cards."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.billing.provider import Contract, Service
from app.modules.billing.quality import FieldOrigin
from app.modules.network.models import Connection


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_no: str = Field(min_length=1, max_length=100)

    @field_validator("account_no")
    @classmethod
    def validate_account(cls, value: str) -> str:
        canonical = value.strip()
        if not canonical or len(canonical) > 100 or any(ord(char) < 32 for char in canonical):
            raise ValueError("Invalid billing account")
        return canonical


class SubscriberInfo(BaseModel):
    subscriber_id: str | None
    billing_account: str
    full_name: str | None
    organization_name: str | None
    phone: str | None
    email: str | None
    field_meta: dict[str, FieldOrigin]


class ConnectionCard(Connection):
    connection_snapshot_id: UUID


class SubscriberCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookup_id: UUID
    source: str
    fetched_at: datetime
    expires_at: datetime
    is_expired: bool
    subscriber: SubscriberInfo
    contracts: list[Contract]
    services: list[Service]
    connections: list[ConnectionCard]
    technical_state: str = "UNKNOWN"
