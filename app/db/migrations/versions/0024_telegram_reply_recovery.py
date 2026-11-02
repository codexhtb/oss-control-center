"""Recover delivered ForceReply replies and track report confirmations.

Revision ID: 0024_telegram_reply_recovery
Revises: 0023_telegram_dynamic_group
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_telegram_reply_recovery"
down_revision: str | None = "0023_telegram_dynamic_group"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "field_dispatch_updates",
        sa.Column(
            "confirmation_status",
            sa.String(16),
            nullable=False,
            server_default="NOT_REQUIRED",
        ),
    )
    op.add_column(
        "field_dispatch_updates",
        sa.Column("confirmation_error_code", sa.String(80), nullable=True),
    )
    op.create_check_constraint(
        "ck_field_updates_confirmation",
        "field_dispatch_updates",
        "confirmation_status IN ('NOT_REQUIRED','PENDING','SENDING','SENT','UNCERTAIN','FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_field_updates_confirmation", "field_dispatch_updates", type_="check")
    op.drop_column("field_dispatch_updates", "confirmation_error_code")
    op.drop_column("field_dispatch_updates", "confirmation_status")
