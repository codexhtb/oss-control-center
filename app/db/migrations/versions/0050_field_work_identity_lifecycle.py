"""Unify active Field Work identities and staff work reports.

Revision ID: 0050_field_work_identity
Revises: 0049_field_work_staff_access
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_field_work_identity"
down_revision: str | None = "0049_field_work_staff_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KNOWN_PERMISSIONS = (
    "'users.manage','users.read','tickets.create','tickets.read.all','tickets.read.own',"
    "'tickets.edit.all','tickets.edit.own','tickets.comment','tickets.assign','tickets.start',"
    "'tickets.diagnose','tickets.resolve','tickets.close','tickets.callback',"
    "'subscribers.read','subscribers.finance.read','diagnostics.read','diagnostics.run',"
    "'audit.read','tickets.field_work','tickets.delete.own','tickets.delete.all',"
    "'tickets.restore','tickets.purge','chat.messages.delete','settings.manage','reports.read',"
    "'tasks.create','tasks.execute'"
)


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({KNOWN_PERMISSIONS},'field_work.perform')",
    )
    op.add_column(
        "field_work_staff_access", sa.Column("linked_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "UPDATE field_work_staff_access SET linked_at = updated_at "
        "WHERE telegram_user_id IS NOT NULL"
    )
    op.create_table(
        "field_installer_access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("directions", postgresql.ARRAY(sa.String(16))),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_field_access_request_telegram"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_field_access_request_status",
        ),
    )
    op.create_index(
        "uq_field_access_request_pending",
        "field_installer_access_requests",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_field_access_request_created",
        "field_installer_access_requests",
        ["created_at", "id"],
    )

    op.add_column(
        "field_external_messages",
        sa.Column("primary_staff_user_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "field_external_messages",
        sa.Column("partner_staff_user_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_field_report_primary_staff",
        "field_external_messages",
        "app_users",
        ["primary_staff_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_field_report_partner_staff",
        "field_external_messages",
        "app_users",
        ["partner_staff_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_field_report_distinct_staff",
        "field_external_messages",
        "partner_staff_user_id IS NULL OR partner_staff_user_id <> primary_staff_user_id",
    )
    op.create_check_constraint(
        "ck_field_report_one_partner_kind",
        "field_external_messages",
        "partner_installer_id IS NULL OR partner_staff_user_id IS NULL",
    )
    op.drop_constraint("ck_field_messages_content", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_content",
        "field_external_messages",
        "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'COMPLETION_CALLBACK' AND body IS NULL) OR "
        "(kind = 'COMPLETION_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'MINIAPP_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NULL "
        "AND ((primary_installer_id IS NOT NULL AND primary_staff_user_id IS NULL) "
        "OR (primary_installer_id IS NULL AND primary_staff_user_id IS NOT NULL)) "
        "AND client_request_id IS NOT NULL)",
    )
    op.create_index(
        "uq_field_staff_miniapp_request",
        "field_external_messages",
        ["primary_staff_user_id", "client_request_id"],
        unique=True,
        postgresql_where=sa.text(
            "primary_staff_user_id IS NOT NULL AND client_request_id IS NOT NULL"
        ),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_field_work_active_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = 'field_installers' THEN
            IF NEW.is_active AND EXISTS (
              SELECT 1 FROM field_work_staff_access s
              WHERE s.telegram_user_id = NEW.telegram_user_id AND s.is_enabled
            ) THEN
              RAISE EXCEPTION 'telegram identity is active as OCC staff'
                USING ERRCODE = '23505';
            END IF;
          ELSE
            IF NEW.is_enabled AND NEW.telegram_user_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM field_installers i
              WHERE i.telegram_user_id = NEW.telegram_user_id AND i.is_active
            ) THEN
              RAISE EXCEPTION 'telegram identity is active as installer'
                USING ERRCODE = '23505';
            END IF;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_field_installer_active_identity
        BEFORE INSERT OR UPDATE OF telegram_user_id, is_active ON field_installers
        FOR EACH ROW EXECUTE FUNCTION enforce_field_work_active_identity()
    """)
    op.execute("""
        CREATE TRIGGER trg_field_staff_active_identity
        BEFORE INSERT OR UPDATE OF telegram_user_id, is_enabled ON field_work_staff_access
        FOR EACH ROW EXECUTE FUNCTION enforce_field_work_active_identity()
    """)
    op.execute("""
        INSERT INTO app_user_permissions (user_id, permission)
        SELECT id, 'field_work.perform' FROM app_users
        WHERE role IN ('engineer', 'admin') AND deleted_at IS NULL AND permissions_customized
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM field_external_messages WHERE primary_staff_user_id IS NOT NULL
              OR partner_staff_user_id IS NOT NULL
          ) OR EXISTS (SELECT 1 FROM field_installer_access_requests) THEN
            RAISE EXCEPTION 'cannot remove Field Work identity history';
          END IF;
        END $$
    """)
    op.execute("DELETE FROM app_user_permissions WHERE permission = 'field_work.perform'")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({KNOWN_PERMISSIONS})",
    )
    op.execute("DROP TRIGGER trg_field_staff_active_identity ON field_work_staff_access")
    op.execute("DROP TRIGGER trg_field_installer_active_identity ON field_installers")
    op.execute("DROP FUNCTION enforce_field_work_active_identity()")
    op.drop_index("uq_field_staff_miniapp_request", table_name="field_external_messages")
    op.drop_constraint("ck_field_messages_content", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_content",
        "field_external_messages",
        "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'COMPLETION_CALLBACK' AND body IS NULL) OR "
        "(kind = 'COMPLETION_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'MINIAPP_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NULL AND primary_installer_id IS NOT NULL "
        "AND client_request_id IS NOT NULL)",
    )
    op.drop_constraint("ck_field_report_one_partner_kind", "field_external_messages", type_="check")
    op.drop_constraint("ck_field_report_distinct_staff", "field_external_messages", type_="check")
    op.drop_constraint(
        "fk_field_report_partner_staff", "field_external_messages", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_field_report_primary_staff", "field_external_messages", type_="foreignkey"
    )
    op.drop_column("field_external_messages", "partner_staff_user_id")
    op.drop_column("field_external_messages", "primary_staff_user_id")
    op.drop_table("field_installer_access_requests")
    op.drop_column("field_work_staff_access", "linked_at")
