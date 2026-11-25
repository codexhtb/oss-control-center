"""Opt-in cabinet password permission; never grants it to existing users."""

from collections.abc import Sequence

from alembic import op

revision: str = "0054_subscriber_password"
down_revision: str | None = "0053_task_replies_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = (
    "'users.manage','users.read','tickets.create','tickets.read.all','tickets.read.own',"
    "'tickets.edit.all','tickets.edit.own','tickets.comment','tickets.assign','tickets.start',"
    "'tickets.diagnose','tickets.resolve','tickets.close','tickets.callback',"
    "'subscribers.read','subscribers.finance.read','diagnostics.read','diagnostics.run',"
    "'audit.read','tickets.field_work','tickets.delete.own','tickets.delete.all',"
    "'tickets.restore','tickets.purge','chat.messages.delete','settings.manage','reports.read',"
    "'tasks.create','tasks.execute','field_work.perform'"
)


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known", "app_user_permissions",
        f"permission IN ({PREVIOUS},'subscribers.password.read')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_user_permissions WHERE permission='subscribers.password.read'")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known", "app_user_permissions", f"permission IN ({PREVIOUS})",
    )
