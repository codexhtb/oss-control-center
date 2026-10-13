"""Provider-neutral access connection, with nullable observed identifiers."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.modules.billing.quality import FieldOrigin


class AccessType(StrEnum):
    ETHERNET = "ETHERNET"
    EPON = "EPON"
    GPON = "GPON"
    UNKNOWN = "UNKNOWN"


class DhcpRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    range_id: str
    first_ip_raw: int
    last_ip_raw: int


class IpGroupRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    ip_type_code: int
    ip_raw: int | None = None
    mask_raw: int | None = None
    ip_ext_raw: int | None = None
    mask_ext_raw: int | None = None
    dhcp_pool_id: str | None = None
    pool_id_raw: int | None = None
    dhcp_ranges: list[DhcpRange] = Field(default_factory=list)
    switch_id: str | None = None
    switch_id_raw: int | None = None
    device: str | None = None
    port_id: int | None = None
    vlan_id: int | None = None
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class IpGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    rows: list[IpGroupRow]
    field_meta: dict[str, FieldOrigin] = Field(default_factory=dict)


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_type: AccessType
    address: str | None = Field(default=None, max_length=300)
    device: str | None = Field(default=None, max_length=200)
    port: str | None = Field(default=None, max_length=100)
    vlan: str | None = Field(default=None, max_length=100)
    ip_group: str | None = Field(default=None, max_length=100)
    service_id: str | None = Field(default=None, max_length=100)
    ip_groups: list[IpGroup] = Field(default_factory=list)
    onu_id: str | None = Field(default=None, max_length=100)
    field_meta: dict[str, FieldOrigin]
