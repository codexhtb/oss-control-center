"""Share Field Dispatch reports and reschedules with installer Mini App.

Revision ID: 0047_field_work_miniapp_reports
Revises: 0046_field_installers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_field_work_miniapp_reports"
down_revision: str | None = "0046_field_installers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE field_miniapp_update_id_seq")
    op.add_column(
        "field_external_messages",
        sa.Column("primary_installer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "field_external_messages",
        sa.Column("partner_installer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("field_external_messages", sa.Column("partner_name", sa.String(160)))
    op.add_column(
        "field_external_messages", sa.Column("client_request_id", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_field_report_primary_installer",
        "field_external_messages",
        "field_installers",
        ["primary_installer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_field_report_partner_installer",
        "field_external_messages",
        "field_installers",
        ["partner_installer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_field_report_distinct_installers",
        "field_external_messages",
        "partner_installer_id IS NULL OR partner_installer_id <> primary_installer_id",
    )
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT','MINIAPP_REPORT',"
        "'RESCHEDULE_CALLBACK','RESCHEDULE_REPORT','RESCHEDULE_CANCEL','RESCHEDULE_INVALID',"
        "'GROUP_BIND','GROUP_STATUS','GROUP_EVENT','UNSUPPORTED')",
    )
    op.drop_constraint("ck_field_messages_kind", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_kind",
        "field_external_messages",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT','MINIAPP_REPORT')",
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
        "AND telegram_message_id IS NULL AND primary_installer_id IS NOT NULL "
        "AND client_request_id IS NOT NULL)",
    )
    op.create_index(
        "uq_field_miniapp_request",
        "field_external_messages",
        ["primary_installer_id", "client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )
    op.create_index(
        "uq_field_miniapp_report",
        "field_external_messages",
        ["handoff_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'MINIAPP_REPORT'"),
    )
    op.drop_constraint("ck_field_reschedules_source", "field_work_reschedules", type_="check")
    op.create_check_constraint(
        "ck_field_reschedules_source",
        "field_work_reschedules",
        "source IN ('TELEGRAM','OSS','MINIAPP')",
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM field_external_messages WHERE kind = 'MINIAPP_REPORT')
             OR EXISTS (SELECT 1 FROM field_work_reschedules WHERE source = 'MINIAPP') THEN
            RAISE EXCEPTION 'cannot remove Mini App field work history';
          END IF;
        END $$
    """)
    op.drop_constraint("ck_field_reschedules_source", "field_work_reschedules", type_="check")
    op.create_check_constraint(
        "ck_field_reschedules_source", "field_work_reschedules", "source IN ('TELEGRAM','OSS')"
    )
    op.drop_index("uq_field_miniapp_report", table_name="field_external_messages")
    op.drop_index("uq_field_miniapp_request", table_name="field_external_messages")
    op.drop_constraint("ck_field_messages_content", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_content",
        "field_external_messages",
        "(kind = 'TEXT_REPLY' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL) OR "
        "(kind = 'COMPLETION_CALLBACK' AND body IS NULL) OR "
        "(kind = 'COMPLETION_REPORT' AND body IS NOT NULL AND length(btrim(body)) > 0 "
        "AND telegram_message_id IS NOT NULL)",
    )
    op.drop_constraint("ck_field_messages_kind", "field_external_messages", type_="check")
    op.create_check_constraint(
        "ck_field_messages_kind",
        "field_external_messages",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT')",
    )
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT',"
        "'RESCHEDULE_CALLBACK','RESCHEDULE_REPORT','RESCHEDULE_CANCEL','RESCHEDULE_INVALID',"
        "'GROUP_BIND','GROUP_STATUS','GROUP_EVENT','UNSUPPORTED')",
    )
    op.drop_constraint(
        "ck_field_report_distinct_installers", "field_external_messages", type_="check"
    )
    op.drop_constraint(
        "fk_field_report_partner_installer", "field_external_messages", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_field_report_primary_installer", "field_external_messages", type_="foreignkey"
    )
    for column in (
        "client_request_id",
        "partner_name",
        "partner_installer_id",
        "primary_installer_id",
    ):
        op.drop_column("field_external_messages", column)
    op.execute("DROP SEQUENCE field_miniapp_update_id_seq")
