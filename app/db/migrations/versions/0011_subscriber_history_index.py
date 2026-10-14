"""Index immutable lookups for bounded cross-account subscriber ticket history.

Revision ID: 0011_subscriber_history_index
Revises: 0010_subscriber_finance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_subscriber_history_index"
down_revision: str | None = "0010_subscriber_finance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_subscriber_snapshots_source_subscriber",
        "subscriber_snapshots",
        ["source", "subscriber_id", "id"],
        postgresql_where=sa.text("subscriber_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subscriber_snapshots_source_subscriber", table_name="subscriber_snapshots")
