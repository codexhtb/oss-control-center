"""Provider-neutral connection data; no network lookup is performed."""

from enum import StrEnum

from pydantic import BaseModel


class AccessType(StrEnum):
    ETHERNET = "ETHERNET"
    EPON = "EPON"
    GPON = "GPON"
    UNKNOWN = "UNKNOWN"


class Connection(BaseModel):
    access_type: AccessType
    device: str | None = None
    port: str | None = None
    vlan: str | None = None
    onu_id: str | None = None
