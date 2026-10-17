"""Public outbound field dispatch contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SendFieldWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID


class FieldWorkHandoffView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    reason: str
    delivery_status: Literal["DRAFT", "SENDING", "SENT", "FAILED", "UNCERTAIN"]
    work_status: Literal["AWAITING_REPORT", "REPORTED", "VERIFIED"]
    chat_id: str
    telegram_message_id: int | None
    message_preview: str | None = None
    ready_to_send: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    created_by: UUID
    sent_by: UUID | None
    sent_by_name: str | None = None
    last_error_code: str | None
    created_at: datetime
    send_started_at: datetime | None
    sent_at: datetime | None
    reported_at: datetime | None
    verified_by: UUID | None
    verified_by_name: str | None = None
    verified_at: datetime | None
    updated_at: datetime
    completion_prompt_status: (
        Literal["REQUESTING", "WAITING", "FAILED", "UNCERTAIN", "COMPLETED"] | None
    ) = None
    completion_prompt_sent_at: datetime | None = None


class LatestFieldWorkHandoff(BaseModel):
    handoff: FieldWorkHandoffView | None


class FieldExternalMessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    handoff_id: UUID
    kind: Literal["TEXT_REPLY", "COMPLETION_CALLBACK", "COMPLETION_REPORT"]
    sender_name: str
    body: str | None
    telegram_created_at: datetime
    received_at: datetime


class FieldExternalMessagePage(BaseModel):
    items: list[FieldExternalMessageView]
    page: int
    size: int
    total: int
