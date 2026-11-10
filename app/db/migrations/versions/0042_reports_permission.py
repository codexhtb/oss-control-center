"""Add a separate reporting permission without granting it to operators.

Revision ID: 0042_reports_permission
Revises: 0041_reschedule_inline_dialog
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_reports_permission"
down_revision: str | None = "0041_reschedule_inline_dialog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_PERMISSIONS = (
    "'users.manage','users.read','tickets.create','tickets.read.all',"
    "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
    "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
    "'tickets.close','tickets.callback','subscribers.read','subscribers.finance.read',"
    "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work',"
    "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge',"
    "'chat.messages.delete','settings.manage'"
)


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PREVIOUS_PERMISSIONS},'reports.read')",
    )
    # Existing users have explicit grants. Preserve those grants and extend only
    # the engineer/admin defaults; operators remain opt-in through the WEB editor.
    op.execute("""
        INSERT INTO app_user_permissions (user_id, permission)
        SELECT id, 'reports.read' FROM app_users
        WHERE role IN ('engineer', 'admin') AND deleted_at IS NULL
          AND permissions_customized = TRUE
        ON CONFLICT (user_id, permission) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_user_permissions WHERE permission = 'reports.read'")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PREVIOUS_PERMISSIONS})",
    )
