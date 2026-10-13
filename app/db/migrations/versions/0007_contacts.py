"""Add immutable alternative phone observations to lookup snapshots.

Revision ID: 0007_contacts
Revises: 0006_lookup_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_contacts"
down_revision: str | None = "0006_lookup_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriber_snapshots",
        sa.Column(
            "contacts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("subscriber_snapshots", "contacts")
