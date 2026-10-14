"""Store optional source billing observations beside immutable lookup snapshots.

Revision ID: 0010_subscriber_finance
Revises: 0009_ticket_subject_feed
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_subscriber_finance"
down_revision: str | None = "0009_ticket_subject_feed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriber_snapshots",
        sa.Column(
            "financial_accounts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("subscriber_snapshots", "financial_accounts")
