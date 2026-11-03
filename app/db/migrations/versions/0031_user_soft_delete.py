"""Keep historical references while removing employee access and visibility.

Revision ID: 0031_user_soft_delete
Revises: 0030_chat_moderation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_user_soft_delete"
down_revision: str | None = "0030_chat_moderation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("app_users", sa.Column("deleted_by", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_app_users_deleted_by",
        "app_users",
        "app_users",
        ["deleted_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_app_users_deleted",
        "app_users",
        "(deleted_at IS NULL AND deleted_by IS NULL) OR "
        "(deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND is_active = false)",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM app_users WHERE deleted_at IS NOT NULL)")
    ):
        raise RuntimeError("Deleted employees cannot be restored by schema downgrade")
    op.drop_constraint("ck_app_users_deleted", "app_users", type_="check")
    op.drop_constraint("fk_app_users_deleted_by", "app_users", type_="foreignkey")
    op.drop_column("app_users", "deleted_by")
    op.drop_column("app_users", "deleted_at")
