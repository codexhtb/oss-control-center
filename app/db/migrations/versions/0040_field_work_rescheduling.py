"""Add durable visit rescheduling to existing field dispatch.

Revision ID: 0040_field_work_rescheduling
Revises: 0039_field_dispatch_direction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0040_field_work_rescheduling"
down_revision: str | None = "0039_field_dispatch_direction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("field_work_handoffs", sa.Column(
        "schedule_status", sa.String(16), nullable=False, server_default="ORIGINAL"
    ))
    op.add_column("field_work_handoffs", sa.Column(
        "scheduled_for", sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column("field_work_handoffs", sa.Column(
        "schedule_version", sa.Integer(), nullable=False, server_default="0"
    ))
    op.create_check_constraint(
        "ck_field_handoffs_schedule_status", "field_work_handoffs",
        "schedule_status IN ('ORIGINAL','DATE_PENDING','SCHEDULED')",
    )
    op.create_check_constraint(
        "ck_field_handoffs_schedule_date", "field_work_handoffs",
        "(schedule_status = 'SCHEDULED' AND scheduled_for IS NOT NULL) OR "
        "(schedule_status <> 'SCHEDULED' AND scheduled_for IS NULL)",
    )
    op.create_check_constraint(
        "ck_field_handoffs_schedule_version", "field_work_handoffs",
        "schedule_version >= 0",
    )
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind", "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT',"
        "'RESCHEDULE_CALLBACK','RESCHEDULE_REPORT','RESCHEDULE_CANCEL','RESCHEDULE_INVALID',"
        "'GROUP_BIND','GROUP_STATUS','GROUP_EVENT','UNSUPPORTED')",
    )
    op.add_column("field_work_completion_prompts", sa.Column(
        "prompt_type", sa.String(16), nullable=False, server_default="COMPLETION"
    ))
    op.create_check_constraint(
        "ck_field_prompts_type", "field_work_completion_prompts",
        "prompt_type IN ('COMPLETION','RESCHEDULE')",
    )
    op.drop_constraint("ck_field_prompts_status", "field_work_completion_prompts", type_="check")
    op.create_check_constraint(
        "ck_field_prompts_status", "field_work_completion_prompts",
        "status IN ('REQUESTING','WAITING','FAILED','UNCERTAIN','COMPLETED','CANCELED')",
    )
    op.drop_constraint("ck_field_prompts_receipt", "field_work_completion_prompts", type_="check")
    op.create_check_constraint(
        "ck_field_prompts_receipt", "field_work_completion_prompts",
        "(status = 'REQUESTING' AND telegram_message_id IS NULL AND sent_at IS NULL "
        "AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status = 'WAITING' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL "
        "AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status IN ('FAILED','UNCERTAIN') AND telegram_message_id IS NULL "
        "AND sent_at IS NULL AND completed_at IS NULL AND error_code IS NOT NULL) OR "
        "(status IN ('COMPLETED','CANCELED') AND telegram_message_id IS NOT NULL "
        "AND sent_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL)",
    )
    op.create_table(
        "field_work_reschedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("handoff_id", UUID(as_uuid=True), sa.ForeignKey(
            "field_work_handoffs.id", ondelete="RESTRICT"
        ), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("author_name", sa.String(160), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey(
            "app_users.id", ondelete="RESTRICT"
        )),
        sa.Column("telegram_user_id", sa.BigInteger()),
        sa.Column("telegram_chat_id", sa.String(32)),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("update_record_id", UUID(as_uuid=True), sa.ForeignKey(
            "field_dispatch_updates.id", ondelete="RESTRICT"
        )),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('TELEGRAM','OSS')", name="ck_field_reschedules_source"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_field_reschedules_reason"),
        sa.UniqueConstraint("update_record_id", name="uq_field_reschedules_update"),
    )
    op.create_index(
        "ix_field_reschedules_handoff_created", "field_work_reschedules",
        ["handoff_id", "created_at", "id"],
    )


def downgrade() -> None:
    raise RuntimeError("0040 contains visit history; downgrade requires a reviewed data plan")
