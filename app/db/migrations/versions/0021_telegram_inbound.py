"""Receive Telegram installer replies and completion callbacks.

Revision ID: 0021_telegram_inbound
Revises: 0020_telegram_field_dispatch
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_telegram_inbound"
down_revision: str | None = "0020_telegram_field_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_field_handoffs_work", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_work",
        "field_work_handoffs",
        "work_status IN ('AWAITING_REPORT','REPORTED','VERIFIED')",
    )
    op.add_column("field_work_handoffs", sa.Column("reported_at", sa.DateTime(timezone=True)))
    op.add_column("field_work_handoffs", sa.Column("verified_by", postgresql.UUID(as_uuid=True)))
    op.add_column("field_work_handoffs", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_field_handoffs_verified_by",
        "field_work_handoffs",
        "app_users",
        ["verified_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_field_handoffs_work_receipt",
        "field_work_handoffs",
        "(work_status = 'AWAITING_REPORT' AND reported_at IS NULL "
        "AND verified_at IS NULL AND verified_by IS NULL) OR "
        "(work_status = 'REPORTED' AND reported_at IS NOT NULL "
        "AND verified_at IS NULL AND verified_by IS NULL) OR "
        "(work_status = 'VERIFIED' AND reported_at IS NOT NULL "
        "AND verified_at IS NOT NULL AND verified_by IS NOT NULL)",
    )

    op.create_table(
        "field_dispatch_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bot_identity", sa.String(80), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("sync_status", sa.String(16), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("sync_error_code", sa.String(80)),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["handoff_id"], ["field_work_handoffs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bot_identity", "update_id", name="uq_field_updates_bot_update"),
        sa.CheckConstraint(
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','UNSUPPORTED')",
            name="ck_field_updates_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('PROCESSED','DENIED','IGNORED','DUPLICATE')",
            name="ck_field_updates_outcome",
        ),
        sa.CheckConstraint(
            "sync_status IN ('NOT_REQUIRED','PENDING','SYNCED','FAILED')",
            name="ck_field_updates_sync",
        ),
    )
    op.create_index("ix_field_updates_received", "field_dispatch_updates", ["received_at", "id"])
    op.create_table(
        "field_external_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("sender_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("telegram_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["handoff_id"], ["field_work_handoffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["update_record_id"], ["field_dispatch_updates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("update_record_id", name="uq_field_messages_update"),
        sa.CheckConstraint(
            "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK')", name="ck_field_messages_kind"
        ),
        sa.CheckConstraint("length(btrim(sender_name)) > 0", name="ck_field_messages_sender"),
        sa.CheckConstraint(
            "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
            "AND telegram_message_id IS NOT NULL) OR "
            "(kind = 'COMPLETION_CALLBACK' AND body IS NULL)",
            name="ck_field_messages_content",
        ),
    )
    op.create_index(
        "ix_field_messages_handoff_created",
        "field_external_messages",
        ["handoff_id", "telegram_created_at", "id"],
    )
    op.create_index(
        "uq_field_messages_chat_message",
        "field_external_messages",
        ["chat_id", "telegram_message_id"],
        unique=True,
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )
    op.create_index(
        "uq_field_messages_completion",
        "field_external_messages",
        ["handoff_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'COMPLETION_CALLBACK'"),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_records_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE purge_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' AND TG_TABLE_NAME IN (
                'tickets', 'ticket_comments', 'ticket_decisions', 'ticket_history',
                'audit_events', 'diagnostic_snapshots', 'diagnostic_checks',
                'field_external_messages'
            ) THEN
                IF TG_TABLE_NAME = 'tickets' THEN purge_id := OLD.id;
                ELSIF TG_TABLE_NAME = 'field_external_messages' THEN
                    SELECT h.ticket_id INTO purge_id FROM field_work_handoffs h
                    WHERE h.id = OLD.handoff_id;
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
    op.execute(
        "CREATE TRIGGER immutable_field_external_messages BEFORE UPDATE OR DELETE "
        "ON field_external_messages FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_field_external_messages ON field_external_messages")
    op.drop_table("field_external_messages")
    op.drop_table("field_dispatch_updates")
    op.drop_constraint("ck_field_handoffs_work_receipt", "field_work_handoffs", type_="check")
    op.drop_constraint("fk_field_handoffs_verified_by", "field_work_handoffs", type_="foreignkey")
    op.drop_column("field_work_handoffs", "verified_at")
    op.drop_column("field_work_handoffs", "verified_by")
    op.drop_column("field_work_handoffs", "reported_at")
    op.drop_constraint("ck_field_handoffs_work", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_work", "field_work_handoffs", "work_status = 'AWAITING_REPORT'"
    )
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
