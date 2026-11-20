"""Add internal task replies and durable scheduled reminders.

Revision ID: 0053_task_replies_reminders
Revises: 0052_field_work_note
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_task_replies_reminders"
down_revision: str | None = "0052_field_work_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "internal_tasks", sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "internal_task_notifications",
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.drop_constraint("ck_internal_task_events_kind", "internal_task_events", type_="check")
    op.create_check_constraint(
        "ck_internal_task_events_kind", "internal_task_events",
        "kind IN ('CREATED','MESSAGE','COMPLETED','NO_ANSWER','REOPENED','REMINDER')",
    )
    op.create_index(
        "ix_internal_task_notifications_recipient_available",
        "internal_task_notifications",
        ["recipient_id", "available_at", "created_at", "id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_internal_task_notifications_recipient_available",
        table_name="internal_task_notifications",
    )
    op.drop_constraint("ck_internal_task_events_kind", "internal_task_events", type_="check")
    op.create_check_constraint(
        "ck_internal_task_events_kind", "internal_task_events",
        "kind IN ('CREATED','MESSAGE','COMPLETED','NO_ANSWER','REOPENED')",
    )
    op.drop_column("internal_task_notifications", "available_at")
    op.drop_column("internal_tasks", "reminder_at")
