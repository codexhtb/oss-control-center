"""Record chosen field work direction while preserving historical chat ids.

Revision ID: 0039_field_dispatch_direction
Revises: 0038_ticket_address_override
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_field_dispatch_direction"
down_revision: str | None = "0038_ticket_address_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("field_work_handoffs", sa.Column("direction", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_field_handoffs_direction",
        "field_work_handoffs",
        "direction IS NULL OR direction IN ('SUKHUM', 'OCHAMCHIRA')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_field_handoffs_direction", "field_work_handoffs", type_="check")
    op.drop_column("field_work_handoffs", "direction")
