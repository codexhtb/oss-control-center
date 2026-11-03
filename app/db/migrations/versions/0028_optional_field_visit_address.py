"""Store an optional engineer-provided visit address on a Telegram handoff.

Revision ID: 0028_field_visit_address
Revises: 0027_field_report_notifications
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_field_visit_address"
down_revision: str | None = "0027_field_report_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("field_work_handoffs", sa.Column("visit_address", sa.String(500)))


def downgrade() -> None:
    op.drop_column("field_work_handoffs", "visit_address")
