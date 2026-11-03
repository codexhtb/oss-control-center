"""Persist one report notification per handoff and individual read receipts.

Revision ID: 0027_field_report_notifications
Revises: 0026_telegram_report_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_field_report_notifications"
down_revision: str | None = "0026_telegram_report_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_report_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["handoff_id"], ["field_work_handoffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["message_id"], ["field_external_messages.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("handoff_id", name="uq_field_report_notifications_handoff"),
        sa.UniqueConstraint("message_id", name="uq_field_report_notifications_message"),
    )
    op.create_index(
        "ix_field_report_notifications_created", "field_report_notifications", ["created_at", "id"]
    )
    op.create_table(
        "field_report_notification_reads",
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["field_report_notifications.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("notification_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("field_report_notification_reads")
    op.drop_index("ix_field_report_notifications_created", table_name="field_report_notifications")
    op.drop_table("field_report_notifications")
