"""Preserve account and IP-group multiplicity in immutable billing snapshots.

Revision ID: 0005_netup_groups
Revises: 0004_subscriber_360
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_netup_groups"
down_revision: str | None = "0004_subscriber_360"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subscriber_snapshots", sa.Column("actual_address", sa.String(300)))
    op.add_column(
        "subscriber_snapshots",
        sa.Column(
            "accounts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column("connection_snapshots", sa.Column("service_id", sa.String(100)))
    op.add_column(
        "connection_snapshots",
        sa.Column(
            "ip_groups", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("connection_snapshots", "ip_groups")
    op.drop_column("connection_snapshots", "service_id")
    op.drop_column("subscriber_snapshots", "accounts")
    op.drop_column("subscriber_snapshots", "actual_address")
