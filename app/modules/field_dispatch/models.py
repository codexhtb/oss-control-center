"""Persistent Telegram field work delivery, replies and completion state."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT',"
            "'GROUP_BIND','GROUP_STATUS','GROUP_EVENT','UNSUPPORTED')",
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
        CheckConstraint(
            "confirmation_status IN ('NOT_REQUIRED','PENDING','SENDING','SENT',"
            "'UNCERTAIN','FAILED')",
            name="ck_field_updates_confirmation",
        ),
        CheckConstraint(
            "report_delete_status IN ('NOT_REQUIRED','PENDING','SENDING','DONE','FAILED')",
            name="ck_field_updates_report_delete_status",
        ),
        CheckConstraint(
            "card_edit_status IN ('NOT_REQUIRED','PENDING','SENDING','DONE','FAILED')",
            name="ck_field_updates_card_edit_status",
        ),
        CheckConstraint(
            "report_delete_attempts >= 0 AND card_edit_attempts >= 0",
            name="ck_field_updates_cleanup_attempts",
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
    confirmation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_REQUIRED"
    )
    confirmation_error_code: Mapped[str | None] = mapped_column(String(80))
    report_delete_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_REQUIRED"
    )
    report_delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    report_delete_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_delete_error_code: Mapped[str | None] = mapped_column(String(80))
    card_edit_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_REQUIRED"
    )
    card_edit_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    card_edit_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_edit_error_code: Mapped[str | None] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldExternalMessage(Base):
    """Verified installer reply or completion callback attached to one handoff."""

    __tablename__ = "field_external_messages"
    __table_args__ = (
        UniqueConstraint("update_record_id", name="uq_field_messages_update"),
        CheckConstraint(
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT')",
            name="ck_field_messages_kind",
        ),
        CheckConstraint("length(btrim(sender_name)) > 0", name="ck_field_messages_sender"),
        CheckConstraint(
            "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
            "AND telegram_message_id IS NOT NULL) OR "
            "(kind = 'COMPLETION_CALLBACK' AND body IS NULL) OR "
            "(kind = 'COMPLETION_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
            "AND telegram_message_id IS NOT NULL)",
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


class FieldWorkCompletionPrompt(Base):
    """Durable ForceReply question binding one installer to one handoff."""

    __tablename__ = "field_work_completion_prompts"
    __table_args__ = (
        UniqueConstraint("callback_update_id", name="uq_field_prompts_callback_update"),
        CheckConstraint(
            "status IN ('REQUESTING','WAITING','FAILED','UNCERTAIN','COMPLETED')",
            name="ck_field_prompts_status",
        ),
        CheckConstraint(
            "(status = 'REQUESTING' AND telegram_message_id IS NULL AND sent_at IS NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'WAITING' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status IN ('FAILED','UNCERTAIN') AND telegram_message_id IS NULL "
            "AND sent_at IS NULL AND completed_at IS NULL AND error_code IS NOT NULL) OR "
            "(status = 'COMPLETED' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND error_code IS NULL)",
            name="ck_field_prompts_receipt",
        ),
        CheckConstraint(
            "deletion_status IN ('NOT_REQUIRED','PENDING','SENDING','DELETED','FAILED')",
            name="ck_field_prompts_deletion_status",
        ),
        CheckConstraint("deletion_attempts >= 0", name="ck_field_prompts_deletion_attempts"),
        Index("ix_field_prompts_handoff_created", "handoff_id", "created_at", "id"),
        Index(
            "uq_field_prompts_active_handoff",
            "handoff_id",
            unique=True,
            postgresql_where=text("status IN ('REQUESTING','WAITING','UNCERTAIN')"),
        ),
        Index(
            "uq_field_prompts_chat_message",
            "chat_id",
            "telegram_message_id",
            unique=True,
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    callback_update_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_dispatch_updates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(80))
    recipient_username: Mapped[str | None] = mapped_column(String(32))
    deletion_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_REQUIRED"
    )
    deletion_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deletion_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FieldDispatchGroupBinding(Base):
    """The single active Telegram destination, managed by a verified group admin."""

    __tablename__ = "field_dispatch_group_bindings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_field_group_binding_singleton"),
        CheckConstraint(
            "status IN ('UNBOUND','ACTIVE','BOT_REMOVED')", name="ck_field_group_binding_status"
        ),
        CheckConstraint(
            "(status = 'UNBOUND' AND chat_id IS NULL AND chat_title IS NULL AND chat_type IS NULL) "
            "OR (status <> 'UNBOUND' AND chat_id IS NOT NULL AND chat_title IS NOT NULL "
            "AND chat_type IN ('group','supergroup'))",
            name="ck_field_group_binding_chat",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    chat_id: Mapped[str | None] = mapped_column(String(32))
    chat_title: Mapped[str | None] = mapped_column(String(200))
    chat_type: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="UNBOUND")
    bound_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldDispatchGroupBindingAudit(Base):
    """Append-only audit of group binding and bot membership changes."""

    __tablename__ = "field_dispatch_group_binding_audit"
    __table_args__ = (
        UniqueConstraint("update_record_id", name="uq_field_group_audit_update"),
        CheckConstraint(
            "action IN ('BOUND','MOVED','BOT_REMOVED','BOT_RESTORED','MIGRATED','UNAVAILABLE')",
            name="ck_field_group_audit_action",
        ),
        Index("ix_field_group_audit_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    update_record_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("field_dispatch_updates.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    old_chat_id: Mapped[str | None] = mapped_column(String(32))
    new_chat_id: Mapped[str | None] = mapped_column(String(32))
    chat_title: Mapped[str | None] = mapped_column(String(200))
    chat_type: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldDispatchChatMigration(Base):
    """Alias old group ids to their Telegram supergroup ids without rewriting history."""

    __tablename__ = "field_dispatch_chat_migrations"
    __table_args__ = (
        UniqueConstraint("update_record_id", name="uq_field_chat_migrations_update"),
        CheckConstraint("old_chat_id <> new_chat_id", name="ck_field_chat_migration_distinct"),
        Index("ix_field_chat_migrations_new", "new_chat_id"),
    )

    old_chat_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    new_chat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    update_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_dispatch_updates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    migrated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldReportNotification(Base):
    """One durable notification for a completed handoff, shared by eligible engineers."""

    __tablename__ = "field_report_notifications"
    __table_args__ = (
        UniqueConstraint("handoff_id", name="uq_field_report_notifications_handoff"),
        UniqueConstraint("message_id", name="uq_field_report_notifications_message"),
        Index("ix_field_report_notifications_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_external_messages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldReportNotificationRead(Base):
    """Per-user read receipt; absence of a row means unread."""

    __tablename__ = "field_report_notification_reads"

    notification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("field_report_notifications.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
