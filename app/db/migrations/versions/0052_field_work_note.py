"""Optional preparation note for Field Work; historical messages stay immutable."""

import sqlalchemy as sa
from alembic import op

revision = "0052_field_work_note"
down_revision = "0051_field_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("field_work_handoffs", sa.Column("note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_field_handoffs_note", "field_work_handoffs", "note IS NULL OR length(note) <= 4000"
    )


def downgrade() -> None:
    op.drop_constraint("ck_field_handoffs_note", "field_work_handoffs", type_="check")
    op.drop_column("field_work_handoffs", "note")
