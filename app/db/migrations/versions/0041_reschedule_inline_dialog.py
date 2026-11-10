"""Inline Telegram rescheduling, date-only visits and durable card sync.

Revision ID: 0041_reschedule_inline_dialog
Revises: 0040_field_work_rescheduling
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0041_reschedule_inline_dialog"
down_revision: str | None = "0040_field_work_rescheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("field_work_handoffs", sa.Column("scheduled_date", sa.Date()))
    op.add_column("field_work_reschedules", sa.Column("scheduled_date", sa.Date()))
    op.drop_constraint("ck_field_handoffs_schedule_date", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_schedule_date",
        "field_work_handoffs",
        "(schedule_status = 'SCHEDULED' AND "
        "(scheduled_for IS NOT NULL OR scheduled_date IS NOT NULL)) "
        "OR (schedule_status <> 'SCHEDULED' AND scheduled_for IS NULL AND scheduled_date IS NULL)",
    )
    op.add_column(
        "field_work_handoffs",
        sa.Column("schedule_card_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "field_work_handoffs",
        sa.Column(
            "schedule_card_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"
        ),
    )
    op.add_column(
        "field_work_handoffs",
        sa.Column("schedule_card_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "field_work_handoffs", sa.Column("schedule_card_attempted_at", sa.DateTime(timezone=True))
    )
    op.add_column("field_work_handoffs", sa.Column("schedule_card_error_code", sa.String(80)))
    # Existing cards retain their historical state; only new changes enter the retry queue.
    op.execute("UPDATE field_work_handoffs SET schedule_card_version = schedule_version")
    op.create_check_constraint(
        "ck_field_handoffs_schedule_card_status",
        "field_work_handoffs",
        "schedule_card_status IN ('NOT_REQUIRED','PENDING','SENDING','DONE','FAILED')",
    )
    op.create_table(
        "field_reschedule_dialogs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "handoff_id",
            UUID(as_uuid=True),
            sa.ForeignKey("field_work_handoffs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("sender_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_name", sa.String(160), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(2000)),
        sa.Column("local_date", sa.Date()),
        sa.Column("local_time", sa.Time()),
        sa.Column("calendar_month", sa.Date()),
        sa.Column("input_error", sa.String(80)),
        sa.Column("render_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("synced_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("sync_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_attempted_at", sa.DateTime(timezone=True)),
        sa.Column("sync_error_code", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "stage IN ('REASON','REASON_TEXT','DATE','CALENDAR','DATE_TEXT',"
            "'TIME','TIME_TEXT','CONFIRM','FINISHED','CANCELED')",
            name="ck_field_reschedule_dialog_stage",
        ),
        sa.CheckConstraint(
            "sync_status IN ('PENDING','SENDING','SYNCED','FAILED')",
            name="ck_field_reschedule_dialog_sync",
        ),
    )
    op.create_index(
        "uq_field_reschedule_dialog_active",
        "field_reschedule_dialogs",
        ["handoff_id"],
        unique=True,
        postgresql_where=sa.text("stage NOT IN ('FINISHED','CANCELED')"),
    )


def downgrade() -> None:
    raise RuntimeError("0041 contains visit dates and dialog history; downgrade requires review")
