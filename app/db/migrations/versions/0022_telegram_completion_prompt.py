"""Require a ForceReply description before reporting field work complete.

Revision ID: 0022_telegram_completion_prompt
Revises: 0021_telegram_inbound
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_telegram_completion_prompt"
down_revision: str | None = "0021_telegram_inbound"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT','UNSUPPORTED')",
    )
    op.drop_constraint("ck_field_messages_kind", "field_external_messages", type_="check")
    op.drop_constraint("ck_field_messages_content", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_kind",
        "field_external_messages",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT')",
    )
    op.create_check_constraint(
        "ck_field_messages_content",
        "field_external_messages",
        "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'COMPLETION_CALLBACK' AND body IS NULL) OR "
        "(kind = 'COMPLETION_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL)",
    )
    op.create_table(
        "field_work_completion_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("callback_update_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("sender_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("error_code", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["handoff_id"], ["field_work_handoffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["callback_update_id"], ["field_dispatch_updates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("callback_update_id", name="uq_field_prompts_callback_update"),
        sa.CheckConstraint(
            "status IN ('REQUESTING','WAITING','FAILED','UNCERTAIN','COMPLETED')",
            name="ck_field_prompts_status",
        ),
        sa.CheckConstraint(
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
    )
    op.create_index(
        "ix_field_prompts_handoff_created",
        "field_work_completion_prompts",
        ["handoff_id", "created_at", "id"],
    )
    op.create_index(
        "uq_field_prompts_active_handoff",
        "field_work_completion_prompts",
        ["handoff_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('REQUESTING','WAITING','UNCERTAIN')"),
    )
    op.create_index(
        "uq_field_prompts_chat_message",
        "field_work_completion_prompts",
        ["chat_id", "telegram_message_id"],
        unique=True,
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    # A completion description is still a valid external text reply in 0021.
    op.execute(
        "UPDATE field_external_messages SET kind = 'TEXT_REPLY' WHERE kind = 'COMPLETION_REPORT'"
    )
    op.execute(
        "UPDATE field_dispatch_updates SET kind = 'TEXT_REPLY', sync_status = 'NOT_REQUIRED', "
        "sync_error_code = NULL WHERE kind = 'COMPLETION_REPORT'"
    )
    op.drop_table("field_work_completion_prompts")
    op.drop_constraint("ck_field_messages_content", "field_external_messages", type_="check")
    op.drop_constraint("ck_field_messages_kind", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_kind",
        "field_external_messages",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK')",
    )
    op.create_check_constraint(
        "ck_field_messages_content",
        "field_external_messages",
        "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'COMPLETION_CALLBACK' AND body IS NULL)",
    )
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','UNSUPPORTED')",
    )
