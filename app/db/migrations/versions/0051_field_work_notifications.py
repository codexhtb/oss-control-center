"""Add personal Field Work notification preferences and transactional outbox.

Revision ID: 0051_field_notifications
Revises: 0050_field_work_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_field_notifications"
down_revision: str | None = "0050_field_work_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_notification_preferences",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("notify_new_work", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_reschedules", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_verification", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("subscription_status", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_field_notify_pref_telegram"),
        sa.CheckConstraint(
            "subscription_status IN ('UNKNOWN','ACTIVE','UNAVAILABLE')",
            name="ck_field_notify_pref_status",
        ),
    )
    op.create_table(
        "field_notification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "handoff_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("dedupe_key", sa.String(160), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('NEW_WORK','RESCHEDULED','REPORT_PENDING')",
            name="ck_field_notify_event_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('SUKHUM','OCHAMCHIRA')", name="ck_field_notify_event_direction"
        ),
    )
    op.create_index(
        "ix_field_notify_event_created", "field_notification_events", ["created_at", "id"]
    )
    op.create_table(
        "field_notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_notification_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("event_id", "telegram_user_id", name="uq_field_notify_delivery"),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_field_notify_delivery_telegram"
        ),
        sa.CheckConstraint(
            "status IN "
            "('PENDING','SENDING','RETRY','SENT','BLOCKED','UNCERTAIN','FAILED','SKIPPED')",
            name="ck_field_notify_delivery_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_field_notify_delivery_attempts"),
        sa.CheckConstraint(
            "(status = 'SENT' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR "
            "(status <> 'SENT' AND telegram_message_id IS NULL AND sent_at IS NULL)",
            name="ck_field_notify_delivery_receipt",
        ),
    )
    op.create_index(
        "ix_field_notify_delivery_due",
        "field_notification_deliveries",
        ["status", "available_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_field_notify_delivery_due", table_name="field_notification_deliveries")
    op.drop_table("field_notification_deliveries")
    op.drop_index("ix_field_notify_event_created", table_name="field_notification_events")
    op.drop_table("field_notification_events")
    op.drop_table("field_notification_preferences")
