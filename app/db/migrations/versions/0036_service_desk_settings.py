"""Typed text templates and versioned decision labels.

Revision ID: 0036_service_desk_settings
Revises: 0035_erroneous_payment_ticket
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_service_desk_settings"
down_revision: str | None = "0035_erroneous_payment_ticket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = (
    ("CABLE_BREAK", "Обрыв кабеля"),
    ("CUSTOMER_EQUIPMENT", "Оборудование абонента"),
    ("ONU_POWER", "Питание ONU"),
    ("NETWORK_FAULT", "Неисправность сети"),
    ("CONFIGURATION_IP", "Конфигурация / IP"),
    ("CONNECTION_SETUP", "Подключение / настройка"),
    ("CONSULTATION", "Консультация"),
    ("OTHER", "Другое"),
    ("NOT_ESTABLISHED", "Причина не установлена"),
)
PERMISSIONS = (
    "'users.manage','users.read','tickets.create','tickets.read.all',"
    "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
    "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
    "'tickets.close','tickets.callback','subscribers.read','subscribers.finance.read',"
    "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work',"
    "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge',"
    "'chat.messages.delete'"
)


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PERMISSIONS},'settings.manage')",
    )
    reasons = op.create_table(
        "decision_reason_catalog",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_decision_reason_sort"),
        sa.CheckConstraint("version > 0", name="ck_decision_reason_version"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_decision_reason_name"),
    )
    op.create_index("ix_decision_reason_order", "decision_reason_catalog", ["sort_order", "code"])
    op.bulk_insert(
        reasons,
        [
            {"code": code, "name": name, "sort_order": index * 10}
            for index, (code, name) in enumerate(REASONS, start=1)
        ],
    )
    op.create_table(
        "ticket_text_templates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by",
            sa.UUID(),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "purpose IN ('decision', 'comment', 'callback')", name="ck_ticket_template_purpose"
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_ticket_template_name"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_ticket_template_body"),
        sa.CheckConstraint("sort_order >= 0", name="ck_ticket_template_sort"),
        sa.CheckConstraint("version > 0", name="ck_ticket_template_version"),
    )
    op.create_index(
        "ix_ticket_template_purpose_order", "ticket_text_templates", ["purpose", "sort_order", "id"]
    )
    op.add_column("ticket_decisions", sa.Column("reason_name", sa.String(160)))
    # Existing decisions are immutable by trigger. Their original labels are
    # resolved from the versioned legacy-code dictionary at read time.
    op.create_foreign_key(
        "fk_ticket_decisions_reason_catalog",
        "ticket_decisions",
        "decision_reason_catalog",
        ["reason"],
        ["code"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ticket_text_templates)
             OR EXISTS (SELECT 1 FROM decision_reason_catalog WHERE version <> 1)
             OR EXISTS (SELECT 1 FROM app_user_permissions WHERE permission = 'settings.manage')
          THEN
            RAISE EXCEPTION 'cannot discard managed Service Desk settings';
          END IF;
        END $$
    """)
    op.drop_constraint("fk_ticket_decisions_reason_catalog", "ticket_decisions", type_="foreignkey")
    op.drop_column("ticket_decisions", "reason_name")
    op.drop_index("ix_ticket_template_purpose_order", table_name="ticket_text_templates")
    op.drop_table("ticket_text_templates")
    op.drop_index("ix_decision_reason_order", table_name="decision_reason_catalog")
    op.drop_table("decision_reason_catalog")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PERMISSIONS})",
    )
