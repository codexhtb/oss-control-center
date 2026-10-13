"""Identity, sessions, persistent login limits and append-only audit.

Revision ID: 0002_identity_rbac
Revises: 0001_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity_rbac"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("login ~ '^[a-z0-9][a-z0-9._-]{2,63}$'", name="ck_app_users_login"),
        sa.CheckConstraint("role IN ('operator', 'engineer', 'admin')", name="ck_app_users_role"),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_app_users_argon2id"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash"),
        sa.CheckConstraint("length(csrf_hash) = 64", name="ck_auth_sessions_csrf_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "auth_login_buckets",
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('login', 'source')", name="ck_auth_login_buckets_kind"),
        sa.CheckConstraint("length(key_digest) = 64", name="ck_auth_login_buckets_digest"),
        sa.CheckConstraint("failures >= 0", name="ck_auth_login_buckets_failures"),
    )
    op.create_index("ix_auth_login_buckets_blocked_until", "auth_login_buckets", ["blocked_until"])
    op.create_table(
        "identity_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("login_digest", sa.String(64)),
        sa.Column("source_digest", sa.String(64)),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["app_users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_identity_audit_occurred_at", "identity_audit_events", ["occurred_at"])
    op.execute(
        """
        CREATE FUNCTION identity_keep_active_admin() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.role <> 'admin' OR NOT OLD.is_active THEN
                RETURN NULL;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.role = 'admin' AND NEW.is_active THEN
                    RETURN NULL;
                END IF;
            END IF;
            PERFORM pg_advisory_xact_lock(18384, 1202);
            IF NOT EXISTS (
                SELECT 1 FROM app_users WHERE role = 'admin' AND is_active AND id <> OLD.id
            ) THEN
                RAISE EXCEPTION USING MESSAGE = 'last active administrator',
                    ERRCODE = '23514', CONSTRAINT = 'ck_app_users_last_admin';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER ck_app_users_last_admin_update
        AFTER UPDATE OF role, is_active ON app_users DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION identity_keep_active_admin()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER ck_app_users_last_admin_delete
        AFTER DELETE ON app_users DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION identity_keep_active_admin()"""
    )
    op.execute(
        """CREATE FUNCTION identity_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE = 'identity audit is append-only', ERRCODE = '23514';
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER ck_identity_audit_append_only
        BEFORE UPDATE OR DELETE ON identity_audit_events
        FOR EACH ROW EXECUTE FUNCTION identity_audit_append_only()"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ck_identity_audit_append_only ON identity_audit_events")
    op.execute("DROP FUNCTION identity_audit_append_only()")
    op.execute("DROP TRIGGER ck_app_users_last_admin_delete ON app_users")
    op.execute("DROP TRIGGER ck_app_users_last_admin_update ON app_users")
    op.execute("DROP FUNCTION identity_keep_active_admin()")
    op.drop_index("ix_identity_audit_occurred_at", table_name="identity_audit_events")
    op.drop_table("identity_audit_events")
    op.drop_index("ix_auth_login_buckets_blocked_until", table_name="auth_login_buckets")
    op.drop_table("auth_login_buckets")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("app_users")
