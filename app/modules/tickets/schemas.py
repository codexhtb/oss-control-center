"""Strict public Service Desk payloads; snapshots are read from PostgreSQL."""

from datetime import UTC, datetime
from unicodedata import category
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.billing.provider import Contact
from app.modules.tickets.status import TicketPriority, TicketStatus, TicketType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    sort_order: int
    is_active: bool
    is_other: bool
    version: int


class CreateCategory(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    sort_order: int = Field(ge=0, le=100000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value):
            raise ValueError("category name must be trimmed and contain no control characters")
        return value


class UpdateCategory(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        return CreateCategory.valid_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_update(self) -> "UpdateCategory":
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("at least one category field is required")
        return self


class CreateTicket(StrictModel):
    ticket_type: TicketType = TicketType.SUBSCRIBER_LINKED
    lookup_id: UUID | None = None
    connection_snapshot_id: UUID | None = None
    selected_service_id: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    subject: str | None = Field(default=None, max_length=160)
    category_id: UUID | None = None
    caller_phone: str | None = Field(default=None, max_length=50)
    client_request_id: UUID
    priority: TicketPriority = TicketPriority.NORMAL
    duplicate_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value):
            raise ValueError("subject must be nonblank, trimmed and contain no control characters")
        return value

    @field_validator("duplicate_reason")
    @classmethod
    def validate_duplicate_reason(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value)
        ):
            raise ValueError("duplicate_reason must be trimmed and contain no control characters")
        return value

    @field_validator("caller_phone")
    @classmethod
    def validate_caller_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value):
            raise ValueError(
                "caller_phone must be nonblank, trimmed and contain no control characters"
            )
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "CreateTicket":
        if self.category_id is None and self.description is None:
            raise ValueError("description is required without a category")
        if self.ticket_type == TicketType.SUBSCRIBER_LINKED:
            if self.lookup_id is None:
                raise ValueError("lookup_id is required for SUBSCRIBER_LINKED")
        else:
            if "ticket_type" not in self.model_fields_set:
                raise ValueError("ticket_type is required without lookup_id")
            forbidden = {"lookup_id", "connection_snapshot_id", "selected_service_id"}
            if forbidden & self.model_fields_set:
                raise ValueError("billing fields are forbidden for MANUAL and INTERNAL")
            if "duplicate_reason" in self.model_fields_set:
                raise ValueError("duplicate_reason is only valid for SUBSCRIBER_LINKED")
        return self


class AssignEngineer(StrictModel):
    engineer_id: UUID


class EditTicket(StrictModel):
    subject: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=10000)

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value: str | None) -> str | None:
        return CreateTicket.validate_subject(value)

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(category(ch) == "Cc" and ch not in "\n\r\t" for ch in value)
        ):
            raise ValueError("description must contain text and no control characters")
        return value

    @model_validator(mode="after")
    def nonempty(self) -> "EditTicket":
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("subject or description is required")
        return self


class DeleteTicket(StrictModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value)
        ):
            raise ValueError("reason must be nonblank, trimmed and contain no control characters")
        return value


class PurgeTicket(StrictModel):
    confirm_number: int = Field(ge=1)


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
    subject: str | None
    category_id: UUID | None = None
    category_name: str | None = None
    caller_phone: str | None = None
    status: TicketStatus
    priority: TicketPriority
    created_by: UUID
    created_by_name: str | None = None
    assigned_engineer_id: UUID | None
    assigned_engineer_name: str | None = None
    closed_by: UUID | None
    closed_decision_id: UUID | None
    closed_at: datetime | None
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
    delete_reason: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class TicketListView(TicketView):
    subscriber_name: str | None = None
    work_started_at: datetime | None = None


class TicketDetail(TicketView):
    subscriber: SubscriberView | None
    selected_connection: ConnectionView | None
    technical_state: str = "UNKNOWN"


class ActiveTicketSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: int
    subject: str | None
    status: TicketStatus
    created_at: datetime
    assigned_engineer_name: str | None = None
    description: str


class ActiveTickets(BaseModel):
    items: list[ActiveTicketSummary]
    page: int
    size: int
    total_visible: int
    has_hidden_active: bool


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
    author_name: str | None = None
    body: str
    created_at: datetime


class DecisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    engineer_id: UUID
    engineer_name: str | None = None
    decision_text: str
    performed_actions: str
    created_at: datetime


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    event_type: str
    actor_id: UUID | None
    actor_name: str | None = None
    event_data: dict
    ticket_version: int
    occurred_at: datetime


class RecentEventView(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_number: int
    event_type: str
    actor_id: UUID | None
    actor_name: str | None = None
    ticket_version: int
    occurred_at: datetime


class AssignableEngineerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str


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
    check_id: UUID | None = None
    ticket_id: UUID
    connection_snapshot_id: UUID | None
    source_kind: str
    availability: str
    observation: dict
    observed_at: datetime
    recorded_at: datetime
    recorded_by: UUID
