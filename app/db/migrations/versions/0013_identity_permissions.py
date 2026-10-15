"""Persist individual grants, password-change state and administrative audit details.

Revision ID: 0013_identity_permissions
Revises: 0012_oss_diagnostics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0013_identity_permissions"
down_revision: str | None = "0012_oss_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "app_users",
        sa.Column(
            "permissions_customized", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("identity_audit_events", sa.Column("details", JSONB(), nullable=True))
    op.create_table(
        "app_user_permissions",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "permission"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "permission IN ("
            "'users.manage','users.read','tickets.create','tickets.read.all',"
            "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
            "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
            "'tickets.close','subscribers.read','subscribers.finance.read',"
            "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work')",
            name="ck_app_user_permissions_known",
        ),
    )
    # A data migration freezes the then-current presets. Existing passwords and sessions survive.
    presets = {
        "operator": [
            "subscribers.read",
            "subscribers.finance.read",
            "tickets.read.all",
            "tickets.create",
            "tickets.edit.own",
            "tickets.comment",
            "diagnostics.read",
        ],
        "engineer": [
            "subscribers.read",
            "tickets.read.all",
            "tickets.create",
            "tickets.edit.all",
            "tickets.comment",
            "tickets.start",
            "tickets.assign",
            "tickets.field_work",
            "tickets.diagnose",
            "diagnostics.read",
            "diagnostics.run",
            "tickets.resolve",
            "tickets.close",
            "audit.read",
        ],
        "admin": [
            "users.manage",
            "users.read",
            "tickets.create",
            "tickets.read.all",
            "tickets.read.own",
            "tickets.edit.all",
            "tickets.edit.own",
            "tickets.comment",
            "tickets.assign",
            "tickets.start",
            "tickets.diagnose",
            "tickets.resolve",
            "tickets.close",
            "subscribers.read",
            "subscribers.finance.read",
            "diagnostics.read",
            "diagnostics.run",
            "audit.read",
            "tickets.field_work",
        ],
    }
    connection = op.get_bind()
    for role, permissions in presets.items():
        connection.execute(
            sa.text(
                "INSERT INTO app_user_permissions (user_id, permission) "
                "SELECT id, :permission FROM app_users WHERE role = :role"
            ),
            [{"role": role, "permission": permission} for permission in permissions],
        )
    op.execute("UPDATE app_users SET permissions_customized = true")
    op.execute("""
        CREATE FUNCTION identity_keep_admin_grant() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.permission <> 'users.manage' THEN RETURN NULL; END IF;
            PERFORM pg_advisory_xact_lock(18384, 1202);
            IF NOT EXISTS (
                SELECT 1 FROM app_users u JOIN app_user_permissions p ON p.user_id = u.id
                WHERE u.role = 'admin' AND u.is_active AND p.permission = 'users.manage'
            ) THEN
                RAISE EXCEPTION USING MESSAGE = 'last active administrator grant',
                    ERRCODE = '23514', CONSTRAINT = 'ck_app_users_last_admin_grant';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ck_app_users_last_admin_grant
        AFTER DELETE OR UPDATE ON app_user_permissions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION identity_keep_admin_grant()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER ck_app_users_last_admin_grant ON app_user_permissions")
    op.execute("DROP FUNCTION identity_keep_admin_grant()")
    op.drop_table("app_user_permissions")
    op.drop_column("identity_audit_events", "details")
    op.drop_column("app_users", "permissions_customized")
    op.drop_column("app_users", "must_change_password")
