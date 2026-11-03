"""Public chat contracts. Sender identity is always taken from the session."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SendChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    body: str = Field(min_length=1, max_length=2000)


class ChatMessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_id: UUID
    sender_name: str
    body: str
    created_at: datetime


class ChatHistory(BaseModel):
    items: list[ChatMessageView]
    next_before: int | None


class ChatSummary(BaseModel):
    latest_id: int
    latest_event_id: int
    last_read_id: int
    unread_count: int


class MarkChatRead(BaseModel):
    through_id: int = Field(ge=0)
