"""Strict public Service Desk payloads; snapshots are read from PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.billing.provider import Contact
from app.modules.tickets.status import TicketPriority, TicketStatus, TicketType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTicket(StrictModel):
    lookup_id: UUID
    connection_snapshot_id: UUID | None = None
    selected_service_id: str | None = Field(default=None, min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10000)
    client_request_id: UUID
    priority: TicketPriority = TicketPriority.NORMAL


class AssignEngineer(StrictModel):
    engineer_id: UUID


class AddComment(StrictModel):
    body: str = Field(min_length=1, max_length=10000)


class FieldWorkRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)


class ManualDiagnostic(StrictModel):
    finding: str = Field(min_length=1, max_length=10000)
    observed_at: datetime
    connection_snapshot_id: UUID | None = None

    @field_validator("observed_at")
    @classmethod
    def require_utc_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)


class AddDecision(StrictModel):
    decision_text: str = Field(min_length=1, max_length=10000)
    performed_actions: str = Field(min_length=1, max_length=10000)


class CloseTicket(StrictModel):
    decision_id: UUID


class ConnectionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    access_type: str
    device: str | None
    port: str | None
    vlan: str | None
    onu_id: str | None
    address: str | None = None
    ip_group: str | None = None
    service_id: str | None = None
    ip_groups: list = Field(default_factory=list)
    field_meta: dict = Field(default_factory=dict)


class SubscriberView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    account_no: str
    subscriber_id: str | None = None
    organization_name: str | None = None
    email: str | None = None
    actual_address: str | None = None
    accounts: list = Field(default_factory=list)
    field_meta: dict = Field(default_factory=dict)
    source: str
    fetched_at: datetime
    full_name: str | None
    phone: str | None
    contacts: list[Contact] = Field(default_factory=list)
    contracts: list
    services: list
    connections: list[ConnectionView]


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: int
    ticket_type: TicketType
    account_no: str | None
    subscriber_snapshot_id: UUID | None
    connection_snapshot_id: UUID | None
    selected_service_id: str | None
    description: str
    status: TicketStatus
    priority: TicketPriority
    created_by: UUID
    assigned_engineer_id: UUID | None
    closed_by: UUID | None
    closed_decision_id: UUID | None
    closed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class TicketDetail(TicketView):
    subscriber: SubscriberView | None
    selected_connection: ConnectionView | None
    technical_state: str = "UNKNOWN"


class Page[T](BaseModel):
    items: list[T]
    page: int
    size: int
    total: int


class CommentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    author_id: UUID
    body: str
    created_at: datetime


class DecisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    engineer_id: UUID
    decision_text: str
    performed_actions: str
    created_at: datetime


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    event_type: str
    actor_id: UUID | None
    event_data: dict
    ticket_version: int
    occurred_at: datetime


class AuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID | None
    actor_id: UUID | None
    action: str
    outcome: str
    origin: str
    occurred_at: datetime


class DiagnosticView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    connection_snapshot_id: UUID | None
    source_kind: str
    availability: str
    observation: dict
    observed_at: datetime
    recorded_at: datetime
    recorded_by: UUID
