"""Add optional ticket subject and an index for recent Service Desk history.

Revision ID: 0009_ticket_subject_feed
Revises: 0008_ticket_types
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_ticket_subject_feed"
down_revision: str | None = "0008_ticket_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Historical rows intentionally retain NULL: no historical subject can be reconstructed.
    op.add_column("tickets", sa.Column("subject", sa.String(160), nullable=True))
    op.create_check_constraint(
        "ck_tickets_subject",
        "tickets",
        "subject IS NULL OR (length(btrim(subject)) > 0 AND subject !~ '[[:cntrl:]]')",
    )
    op.create_index("ix_ticket_history_recent", "ticket_history", ["occurred_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_history_recent", table_name="ticket_history")
    op.drop_constraint("ck_tickets_subject", "tickets", type_="check")
    op.drop_column("tickets", "subject")
