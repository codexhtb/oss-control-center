"""Soft deletion and narrowly scoped, audited physical ticket purge.

Revision ID: 0014_ticket_deletion
Revises: 0013_identity_permissions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0014_ticket_deletion"
down_revision: str | None = "0013_identity_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_PERMISSIONS = (
    "'users.manage','users.read','tickets.create','tickets.read.all',"
    "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
    "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
    "'tickets.close','subscribers.read','subscribers.finance.read',"
    "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work'"
)
DELETE_PERMISSIONS = "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge'"


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({OLD_PERMISSIONS},{DELETE_PERMISSIONS})",
    )
    op.execute("""
        INSERT INTO app_user_permissions (user_id, permission)
        SELECT u.id, p.permission FROM app_users u
        CROSS JOIN (VALUES ('tickets.delete.own'), ('tickets.delete.all'),
                           ('tickets.restore'), ('tickets.purge')) AS p(permission)
        WHERE u.role = 'admin' ON CONFLICT DO NOTHING
    """)

    op.add_column("tickets", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("deleted_by", UUID(as_uuid=True)))
    op.add_column("tickets", sa.Column("delete_reason", sa.String(500)))
    op.create_foreign_key(
        "fk_tickets_deleted_by", "tickets", "app_users", ["deleted_by"], ["id"], ondelete="RESTRICT"
    )
    op.create_check_constraint(
        "ck_tickets_delete_fields",
        "tickets",
        "(deleted_at IS NULL AND deleted_by IS NULL AND delete_reason IS NULL) OR "
        "(deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
    )
    op.create_index(
        "ix_tickets_deleted_at",
        "tickets",
        ["deleted_at", "id"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_table(
        "ticket_purge_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_number", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "purged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["app_users.id"], ondelete="RESTRICT"),
    )
    # Existing append-only triggers remain in place. Only a single deleted ticket
    # in a transaction with an active, explicitly granted actor may be purged.
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_records_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE purge_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' AND TG_TABLE_NAME IN (
                'tickets', 'ticket_comments', 'ticket_decisions', 'ticket_history',
                'audit_events', 'diagnostic_snapshots', 'diagnostic_checks'
            ) THEN
                IF TG_TABLE_NAME = 'tickets' THEN purge_id := OLD.id;
                ELSE purge_id := OLD.ticket_id; END IF;
                IF purge_id IS NOT NULL
                   AND current_setting('oss.purge_ticket_id', true) = purge_id::text
                   AND EXISTS (SELECT 1 FROM public.tickets t
                               WHERE t.id = purge_id AND t.deleted_at IS NOT NULL)
                   AND EXISTS (
                       SELECT 1 FROM public.app_users u
                       WHERE u.id = nullif(current_setting('oss.purge_actor_id', true), '')::uuid
                         AND u.is_active AND (
                             (NOT u.permissions_customized AND u.role = 'admin')
                             OR EXISTS (
                                 SELECT 1 FROM public.app_user_permissions p
                                 WHERE p.user_id = u.id AND p.permission = 'tickets.purge'
                             )
                         )
                   ) THEN
                    RETURN OLD;
                END IF;
            END IF;
            RAISE EXCEPTION USING MESSAGE = 'immutable Service Desk record', ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER immutable_ticket_purge_audit BEFORE UPDATE OR DELETE
        ON ticket_purge_audit FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_ticket_purge_audit ON ticket_purge_audit")
    op.execute("DROP TABLE ticket_purge_audit")
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_records_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE = 'immutable Service Desk record', ERRCODE = '23514';
        END $$
    """)
    op.drop_index("ix_tickets_deleted_at", table_name="tickets")
    op.drop_constraint("ck_tickets_delete_fields", "tickets", type_="check")
    op.drop_constraint("fk_tickets_deleted_by", "tickets", type_="foreignkey")
    op.drop_column("tickets", "delete_reason")
    op.drop_column("tickets", "deleted_by")
    op.drop_column("tickets", "deleted_at")
    op.execute(
        "DELETE FROM app_user_permissions WHERE permission IN "
        "('tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge')"
    )
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({OLD_PERMISSIONS})",
    )
