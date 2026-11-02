"""Persist a movable Telegram destination group.

Revision ID: 0023_telegram_dynamic_group
Revises: 0022_telegram_completion_prompt
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_telegram_dynamic_group"
down_revision: str | None = "0022_telegram_completion_prompt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_field_handoffs_lab_chat", "field_work_handoffs", type_="check")
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT','GROUP_BIND',"
        "'GROUP_STATUS','GROUP_EVENT','UNSUPPORTED')",
    )
    op.create_table(
        "field_dispatch_group_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.String(32)),
        sa.Column("chat_title", sa.String(200)),
        sa.Column("chat_type", sa.String(16)),
        sa.Column("status", sa.String(16), nullable=False, server_default="UNBOUND"),
        sa.Column("bound_by_telegram_id", sa.BigInteger()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_field_group_binding_singleton"),
        sa.CheckConstraint(
            "status IN ('UNBOUND','ACTIVE','BOT_REMOVED')", name="ck_field_group_binding_status"
        ),
        sa.CheckConstraint(
            "(status = 'UNBOUND' AND chat_id IS NULL AND chat_title IS NULL AND chat_type IS NULL) "
            "OR (status <> 'UNBOUND' AND chat_id IS NOT NULL AND chat_title IS NOT NULL "
            "AND chat_type IN ('group','supergroup'))",
            name="ck_field_group_binding_chat",
        ),
    )
    op.execute(
        "INSERT INTO field_dispatch_group_bindings (id, status) VALUES (1, 'UNBOUND')"
    )
    op.create_table(
        "field_dispatch_group_binding_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("update_record_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_telegram_id", sa.BigInteger()),
        sa.Column("old_chat_id", sa.String(32)),
        sa.Column("new_chat_id", sa.String(32)),
        sa.Column("chat_title", sa.String(200)),
        sa.Column("chat_type", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["update_record_id"], ["field_dispatch_updates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("update_record_id", name="uq_field_group_audit_update"),
        sa.CheckConstraint(
            "action IN ('BOUND','MOVED','BOT_REMOVED','BOT_RESTORED','MIGRATED','UNAVAILABLE')",
            name="ck_field_group_audit_action",
        ),
    )
    op.create_index(
        "ix_field_group_audit_created",
        "field_dispatch_group_binding_audit",
        ["created_at", "id"],
    )
    op.create_table(
        "field_dispatch_chat_migrations",
        sa.Column("old_chat_id", sa.String(32), primary_key=True),
        sa.Column("new_chat_id", sa.String(32), nullable=False),
        sa.Column("update_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["update_record_id"], ["field_dispatch_updates.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("update_record_id", name="uq_field_chat_migrations_update"),
        sa.CheckConstraint("old_chat_id <> new_chat_id", name="ck_field_chat_migration_distinct"),
    )
    op.create_index(
        "ix_field_chat_migrations_new", "field_dispatch_chat_migrations", ["new_chat_id"]
    )


def downgrade() -> None:
    op.drop_table("field_dispatch_chat_migrations")
    op.drop_table("field_dispatch_group_binding_audit")
    op.drop_table("field_dispatch_group_bindings")
    op.drop_constraint("ck_field_updates_kind", "field_dispatch_updates", type_="check")
    op.create_check_constraint(
        "ck_field_updates_kind",
        "field_dispatch_updates",
        "kind IN ('TEXT_REPLY','COMPLETION_CALLBACK','COMPLETION_REPORT','UNSUPPORTED')",
    )
    op.create_check_constraint(
        "ck_field_handoffs_lab_chat",
        "field_work_handoffs",
        "chat_id = '-5319791765'",
    )
