"""Persist independent cleanup of the report message and handoff card.

Revision ID: 0026_telegram_report_cleanup
Revises: 0025_telegram_prompt_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_telegram_report_cleanup"
down_revision: str | None = "0025_telegram_prompt_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for action in ("report_delete", "card_edit"):
        op.add_column(
            "field_dispatch_updates",
            sa.Column(
                f"{action}_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"
            ),
        )
        op.add_column(
            "field_dispatch_updates",
            sa.Column(f"{action}_attempts", sa.Integer(), nullable=False, server_default="0"),
        )
        op.add_column(
            "field_dispatch_updates",
            sa.Column(f"{action}_attempted_at", sa.DateTime(timezone=True)),
        )
        op.add_column("field_dispatch_updates", sa.Column(f"{action}_error_code", sa.String(80)))
        op.create_check_constraint(
            f"ck_field_updates_{action}_status",
            "field_dispatch_updates",
            f"{action}_status IN ('NOT_REQUIRED','PENDING','SENDING','DONE','FAILED')",
        )
    op.create_check_constraint(
        "ck_field_updates_cleanup_attempts",
        "field_dispatch_updates",
        "report_delete_attempts >= 0 AND card_edit_attempts >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_field_updates_cleanup_attempts", "field_dispatch_updates", type_="check")
    for action in ("card_edit", "report_delete"):
        op.drop_constraint(
            f"ck_field_updates_{action}_status", "field_dispatch_updates", type_="check"
        )
        for column in ("error_code", "attempted_at", "attempts", "status"):
            op.drop_column("field_dispatch_updates", f"{action}_{column}")
