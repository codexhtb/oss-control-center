"""Persistent Telegram field work delivery, replies and completion state."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FieldWorkHandoff(Base):
    __tablename__ = "field_work_handoffs"
    __table_args__ = (
        UniqueConstraint("ticket_id", "ticket_version", name="uq_field_handoffs_ticket_version"),
        CheckConstraint(
            "delivery_status IN ('DRAFT','SENDING','SENT','FAILED','UNCERTAIN')",
            name="ck_field_handoffs_delivery",
        ),
        CheckConstraint(
            "work_status IN ('AWAITING_REPORT','REPORTED','VERIFIED')",
            name="ck_field_handoffs_work",
        ),
        CheckConstraint("chat_id = '-5319791765'", name="ck_field_handoffs_lab_chat"),
        CheckConstraint("length(btrim(reason)) > 0", name="ck_field_handoffs_reason"),
        CheckConstraint(
            "(delivery_status = 'SENT' AND telegram_message_id IS NOT NULL "
            "AND sent_at IS NOT NULL AND sent_by IS NOT NULL) OR "
            "(delivery_status <> 'SENT' AND telegram_message_id IS NULL "
            "AND sent_at IS NULL)",
            name="ck_field_handoffs_sent_receipt",
        ),
        CheckConstraint(
            "(delivery_status IN ('FAILED','UNCERTAIN') AND last_error_code IS NOT NULL) OR "
            "(delivery_status NOT IN ('FAILED','UNCERTAIN') AND last_error_code IS NULL)",
            name="ck_field_handoffs_error",
        ),
        CheckConstraint(
            "(work_status = 'AWAITING_REPORT' AND reported_at IS NULL "
            "AND verified_at IS NULL AND verified_by IS NULL) OR "
            "(work_status = 'REPORTED' AND reported_at IS NOT NULL "
            "AND verified_at IS NULL AND verified_by IS NULL) OR "
            "(work_status = 'VERIFIED' AND reported_at IS NOT NULL "
            "AND verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name="ck_field_handoffs_work_receipt",
        ),
        Index("ix_field_handoffs_ticket_created", "ticket_id", "created_at", "id"),
        Index(
            "uq_field_handoffs_telegram_message",
            "chat_id",
            "telegram_message_id",
            unique=True,
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False
    )
    ticket_version: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    work_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="AWAITING_REPORT"
    )
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    message_text: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    sent_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldWorkDeliveryAttempt(Base):
    __tablename__ = "field_work_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("handoff_id", "client_request_id", name="uq_field_attempt_request"),
        CheckConstraint(
            "status IN ('SENDING','SENT','FAILED','UNCERTAIN')", name="ck_field_attempt_status"
        ),
        CheckConstraint(
            "(status = 'SENT' AND telegram_message_id IS NOT NULL) OR "
            "(status <> 'SENT' AND telegram_message_id IS NULL)",
            name="ck_field_attempt_receipt",
        ),
        CheckConstraint(
            "(status = 'SENDING' AND completed_at IS NULL) OR "
            "(status <> 'SENDING' AND completed_at IS NOT NULL)",
            name="ck_field_attempt_completed",
        ),
        CheckConstraint(
            "(status IN ('FAILED','UNCERTAIN') AND error_code IS NOT NULL) OR "
            "(status NOT IN ('FAILED','UNCERTAIN') AND error_code IS NULL)",
            name="ck_field_attempt_error",
        ),
        Index("ix_field_attempts_handoff_started", "handoff_id", "started_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FieldDispatchUpdate(Base):
    """Minimal update receipt used for durable deduplication and security audit."""

    __tablename__ = "field_dispatch_updates"
    __table_args__ = (
        UniqueConstraint("bot_identity", "update_id", name="uq_field_updates_bot_update"),
        CheckConstraint(
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','UNSUPPORTED')",
            name="ck_field_updates_kind",
        ),
        CheckConstraint(
            "outcome IN ('PROCESSED','DENIED','IGNORED','DUPLICATE')",
            name="ck_field_updates_outcome",
        ),
        CheckConstraint(
            "sync_status IN ('NOT_REQUIRED','PENDING','SYNCED','FAILED')",
            name="ck_field_updates_sync",
        ),
        Index("ix_field_updates_received", "received_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    bot_identity: Mapped[str] = mapped_column(String(80), nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    handoff_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("field_work_handoffs.id", ondelete="RESTRICT")
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    sync_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_REQUIRED"
    )
    sync_error_code: Mapped[str | None] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldExternalMessage(Base):
    """Verified installer reply or completion callback attached to one handoff."""

    __tablename__ = "field_external_messages"
    __table_args__ = (
        UniqueConstraint("update_record_id", name="uq_field_messages_update"),
        CheckConstraint(
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK')", name="ck_field_messages_kind"
        ),
        CheckConstraint("length(btrim(sender_name)) > 0", name="ck_field_messages_sender"),
        CheckConstraint(
            "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
            "AND telegram_message_id IS NOT NULL) OR "
            "(kind = 'COMPLETION_CALLBACK' AND body IS NULL)",
            name="ck_field_messages_content",
        ),
        Index("ix_field_messages_handoff_created", "handoff_id", "telegram_created_at", "id"),
        Index(
            "uq_field_messages_chat_message",
            "chat_id",
            "telegram_message_id",
            unique=True,
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
        Index(
            "uq_field_messages_completion",
            "handoff_id",
            unique=True,
            postgresql_where=text("kind = 'COMPLETION_CALLBACK'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    update_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_dispatch_updates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    telegram_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
