"""Provider-neutral access connection, with nullable observed identifiers."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.modules.billing.quality import FieldOrigin


class AccessType(StrEnum):
    ETHERNET = "ETHERNET"
    EPON = "EPON"
    GPON = "GPON"
    UNKNOWN = "UNKNOWN"


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_type: AccessType
    address: str | None = Field(default=None, max_length=300)
    device: str | None = Field(default=None, max_length=200)
    port: str | None = Field(default=None, max_length=100)
    vlan: str | None = Field(default=None, max_length=100)
    ip_group: str | None = Field(default=None, max_length=100)
    onu_id: str | None = Field(default=None, max_length=100)
    field_meta: dict[str, FieldOrigin]
