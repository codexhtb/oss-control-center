"""Read-only, provider-neutral Subscriber 360 contract."""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.modules.network.models import Connection


class Subscriber360(BaseModel):
    account_no: str
    full_name: str | None = None
    phone: str | None = None
    contracts: list[dict[str, Any]]
    services: list[dict[str, Any]]
    connections: list[Connection]
    source: Literal["MOCK", "NETUP"]
    fetched_at: datetime


class BillingUnavailable(Exception):
    """The provider cannot be queried; distinct from a missing subscriber."""


class BillingProvider(Protocol):
    def lookup_by_account(self, account_no: str) -> Subscriber360 | None: ...
