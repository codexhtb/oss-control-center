"""Preserve the NetUP building field in immutable subscriber snapshots.

Revision ID: 0019_subscriber_address_ip
Revises: 0018_resolution_reasons
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_subscriber_address_ip"
down_revision: str | None = "0018_resolution_reasons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subscriber_snapshots", sa.Column("house_number", sa.String(100)))


def downgrade() -> None:
    op.drop_column("subscriber_snapshots", "house_number")
