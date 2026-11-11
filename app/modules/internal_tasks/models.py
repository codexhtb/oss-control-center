"""Ticket-bound internal assignments, immutable conversation and durable inbox."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InternalTask(Base):
    __tablename__ = "internal_tasks"
    __table_args__ = (
        UniqueConstraint(
            "creator_id", "client_request_id", name="uq_internal_tasks_creator_request"
        ),
        CheckConstraint(
            "status IN ('OPEN','COMPLETED','NO_ANSWER')", name="ck_internal_tasks_status"
        ),
        CheckConstraint("version > 0", name="ck_internal_tasks_version"),
        CheckConstraint("length(btrim(title)) BETWEEN 1 AND 160", name="ck_internal_tasks_title"),
        CheckConstraint(
            "length(btrim(description)) BETWEEN 1 AND 4000", name="ck_internal_tasks_description"
        ),
        Index("ix_internal_tasks_ticket_created", "ticket_id", "created_at", "id"),
        Index("ix_internal_tasks_assignee_status", "assignee_id", "status", "due_at"),
        Index("ix_internal_tasks_creator_status", "creator_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False
    )
    creator_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    assignee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    client_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InternalTaskEvent(Base):
    __tablename__ = "internal_task_events"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "actor_id", "client_request_id", name="uq_internal_task_events_request"
        ),
        CheckConstraint(
            "kind IN ('CREATED','MESSAGE','COMPLETED','NO_ANSWER','REOPENED')",
            name="ck_internal_task_events_kind",
        ),
        CheckConstraint(
            "body IS NULL OR length(btrim(body)) BETWEEN 1 AND 4000",
            name="ck_internal_task_events_body",
        ),
        Index("ix_internal_task_events_task_created", "task_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("internal_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    client_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InternalTaskNotification(Base):
    __tablename__ = "internal_task_notifications"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "recipient_id", name="uq_internal_task_notification_event_recipient"
        ),
        Index(
            "ix_internal_task_notifications_recipient_created", "recipient_id", "created_at", "id"
        ),
        Index(
            "ix_internal_task_notifications_unread",
            "recipient_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("internal_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("internal_task_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recipient_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
