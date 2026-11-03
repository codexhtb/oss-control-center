"""Track addressed ForceReply prompts and durable deletion attempts.

Revision ID: 0025_telegram_prompt_cleanup
Revises: 0024_telegram_reply_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_telegram_prompt_cleanup"
down_revision: str | None = "0024_telegram_reply_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("field_work_completion_prompts", sa.Column("recipient_username", sa.String(32)))
    op.add_column(
        "field_work_completion_prompts",
        sa.Column("deletion_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"),
    )
    op.add_column(
        "field_work_completion_prompts",
        sa.Column("deletion_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "field_work_completion_prompts",
        sa.Column("deletion_attempted_at", sa.DateTime(timezone=True)),
    )
    op.add_column("field_work_completion_prompts", sa.Column("deletion_error_code", sa.String(80)))
    op.create_check_constraint(
        "ck_field_prompts_deletion_status",
        "field_work_completion_prompts",
        "deletion_status IN ('NOT_REQUIRED','PENDING','SENDING','DELETED','FAILED')",
    )
    op.create_check_constraint(
        "ck_field_prompts_deletion_attempts",
        "field_work_completion_prompts",
        "deletion_attempts >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_field_prompts_deletion_attempts", "field_work_completion_prompts", type_="check"
    )
    op.drop_constraint(
        "ck_field_prompts_deletion_status", "field_work_completion_prompts", type_="check"
    )
    for column in (
        "deletion_error_code",
        "deletion_attempted_at",
        "deletion_attempts",
        "deletion_status",
        "recipient_username",
    ):
        op.drop_column("field_work_completion_prompts", column)
