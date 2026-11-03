"""Persistent chat room, message, and per-user read cursor."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    __table_args__ = (CheckConstraint("kind IN ('STAFF', 'DIRECT')", name="ck_chat_rooms_kind"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("room_id", "sender_id", "client_id", name="uq_chat_messages_client"),
        CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL AND length(body) BETWEEN 1 AND 2000) "
            "OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND body = '')",
            name="ck_chat_messages_body",
        ),
        Index("ix_chat_messages_room_id", "room_id", "id"),
        Index("ix_chat_messages_rate", "sender_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    room_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_rooms.id"), nullable=False
    )
    sender_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id"), nullable=False
    )
    sender_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id")
    )


class ChatEvent(Base):
    __tablename__ = "chat_events"
    __table_args__ = (
        UniqueConstraint("message_id", "event_type", name="uq_chat_events_message_type"),
        CheckConstraint("event_type IN ('MESSAGE', 'DELETE')", name="ck_chat_events_type"),
        Index("ix_chat_events_room_id", "room_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    room_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_rooms.id"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatRead(Base):
    __tablename__ = "chat_reads"
    __table_args__ = (CheckConstraint("last_message_id >= 0", name="ck_chat_reads_cursor"),)

    room_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_rooms.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id"), primary_key=True
    )
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
