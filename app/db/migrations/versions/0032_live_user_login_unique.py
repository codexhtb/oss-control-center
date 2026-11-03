"""Allow reuse of logins belonging to deleted employees.

Revision ID: 0032_live_user_login_unique
Revises: 0031_user_soft_delete
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_live_user_login_unique"
down_revision: str | None = "0031_user_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("app_users_login_key", "app_users", type_="unique")
    op.create_index(
        "uq_app_users_login_live",
        "app_users",
        ["login"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM app_users GROUP BY login HAVING count(*) > 1)")
    ):
        raise RuntimeError("Reused employee logins prevent restoring global uniqueness")
    op.drop_index("uq_app_users_login_live", table_name="app_users")
    op.create_unique_constraint("app_users_login_key", "app_users", ["login"])
