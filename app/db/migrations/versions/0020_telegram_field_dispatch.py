"""Add durable outbound Telegram field work delivery records.

Revision ID: 0020_telegram_field_dispatch
Revises: 0019_subscriber_address_ip
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_telegram_field_dispatch"
down_revision: str | None = "0019_subscriber_address_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_work_handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("delivery_status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("work_status", sa.String(24), nullable=False, server_default="AWAITING_REPORT"),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("message_text", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("send_started_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sent_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ticket_id", "ticket_version", name="uq_field_handoffs_ticket_version"),
        sa.CheckConstraint(
            "delivery_status IN ('DRAFT','SENDING','SENT','FAILED','UNCERTAIN')",
            name="ck_field_handoffs_delivery",
        ),
        sa.CheckConstraint("work_status = 'AWAITING_REPORT'", name="ck_field_handoffs_work"),
        sa.CheckConstraint("chat_id = '-5319791765'", name="ck_field_handoffs_lab_chat"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_field_handoffs_reason"),
        sa.CheckConstraint(
            "(delivery_status = 'SENT' AND telegram_message_id IS NOT NULL "
            "AND sent_at IS NOT NULL AND sent_by IS NOT NULL) OR "
            "(delivery_status <> 'SENT' AND telegram_message_id IS NULL "
            "AND sent_at IS NULL)",
            name="ck_field_handoffs_sent_receipt",
        ),
        sa.CheckConstraint(
            "(delivery_status IN ('FAILED','UNCERTAIN') AND last_error_code IS NOT NULL) OR "
            "(delivery_status NOT IN ('FAILED','UNCERTAIN') AND last_error_code IS NULL)",
            name="ck_field_handoffs_error",
        ),
    )
    op.create_index(
        "ix_field_handoffs_ticket_created", "field_work_handoffs", ["ticket_id", "created_at", "id"]
    )
    op.create_index(
        "uq_field_handoffs_telegram_message",
        "field_work_handoffs",
        ["chat_id", "telegram_message_id"],
        unique=True,
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )
    op.create_table(
        "field_work_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["handoff_id"], ["field_work_handoffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("handoff_id", "client_request_id", name="uq_field_attempt_request"),
        sa.CheckConstraint(
            "status IN ('SENDING','SENT','FAILED','UNCERTAIN')", name="ck_field_attempt_status"
        ),
        sa.CheckConstraint(
            "(status = 'SENT' AND telegram_message_id IS NOT NULL) OR "
            "(status <> 'SENT' AND telegram_message_id IS NULL)",
            name="ck_field_attempt_receipt",
        ),
        sa.CheckConstraint(
            "(status = 'SENDING' AND completed_at IS NULL) OR "
            "(status <> 'SENDING' AND completed_at IS NOT NULL)",
            name="ck_field_attempt_completed",
        ),
        sa.CheckConstraint(
            "(status IN ('FAILED','UNCERTAIN') AND error_code IS NOT NULL) OR "
            "(status NOT IN ('FAILED','UNCERTAIN') AND error_code IS NULL)",
            name="ck_field_attempt_error",
        ),
    )
    op.create_index(
        "ix_field_attempts_handoff_started",
        "field_work_delivery_attempts",
        ["handoff_id", "started_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("field_work_delivery_attempts")
    op.drop_table("field_work_handoffs")
