"""Verified OCC staff access to Field Work Mini App.

Revision ID: 0049_field_work_staff_access
Revises: 0048_field_work_publication
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_field_work_staff_access"
down_revision: str | None = "0048_field_work_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_work_staff_access",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), unique=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "telegram_user_id IS NULL OR telegram_user_id > 0", name="ck_field_staff_telegram_id"
        ),
    )
    op.create_table(
        "field_work_staff_directions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_work_staff_access.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("direction", sa.String(16), primary_key=True),
        sa.CheckConstraint("direction IN ('SUKHUM','OCHAMCHIRA')", name="ck_field_staff_direction"),
    )
    op.create_table(
        "field_work_staff_link_codes",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(code_hash) = 64", name="ck_field_staff_link_hash"),
    )


def downgrade() -> None:
    op.drop_table("field_work_staff_link_codes")
    op.drop_table("field_work_staff_directions")
    op.drop_table("field_work_staff_access")
