"""Editable Field Work details without changing delivery or completion state."""

import sqlalchemy as sa
from alembic import op

revision = "0055_field_work_details"
down_revision = "0054_subscriber_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("field_work_handoffs", "reason", type_=sa.Text())
    op.drop_constraint("ck_field_handoffs_note", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_note", "field_work_handoffs",
        "note IS NULL OR length(note) <= 10000",
    )
    op.create_check_constraint(
        "ck_field_handoffs_reason_length", "field_work_handoffs", "length(reason) <= 10000",
    )
    op.add_column("field_work_handoffs", sa.Column(
        "details_version", sa.Integer(), nullable=False, server_default="1",
    ))
    op.add_column("field_work_handoffs", sa.Column(
        "internal_dispatch_approved", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.create_check_constraint(
        "ck_field_handoffs_details_version", "field_work_handoffs", "details_version >= 1",
    )


def downgrade() -> None:
    # Never truncate accepted text or erase an approval used by active dispatches.
    connection = op.get_bind()
    if connection.scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM field_work_handoffs "
        "WHERE length(reason)>2000 OR length(note)>4000 OR internal_dispatch_approved)"
    )):
        raise RuntimeError("Field Work data requires 0055; restore the previous app image only")
    op.drop_constraint("ck_field_handoffs_details_version", "field_work_handoffs", type_="check")
    op.drop_column("field_work_handoffs", "internal_dispatch_approved")
    op.drop_column("field_work_handoffs", "details_version")
    op.drop_constraint("ck_field_handoffs_reason_length", "field_work_handoffs", type_="check")
    op.drop_constraint("ck_field_handoffs_note", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_note", "field_work_handoffs",
        "note IS NULL OR length(note) <= 4000",
    )
    op.alter_column("field_work_handoffs", "reason", type_=sa.String(2000))
