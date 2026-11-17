"""Add explicit Telegram Mini App installer allowlist.

Revision ID: 0046_field_installers
Revises: 0045_new_connection_ticket
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_field_installers"
down_revision: str | None = "0045_new_connection_ticket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_installers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_field_installers_telegram_id"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_field_installers_name"),
    )
    op.create_table(
        "field_installer_directions",
        sa.Column(
            "installer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_installers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("direction", sa.String(16), primary_key=True),
        sa.CheckConstraint("direction IN ('SUKHUM','OCHAMCHIRA')", name="ck_installer_direction"),
    )
    op.create_index(
        "ix_field_handoffs_miniapp_queue",
        "field_work_handoffs",
        ["direction", "work_status", "sent_at", "id"],
        postgresql_where=sa.text("delivery_status = 'SENT'"),
    )


def downgrade() -> None:
    op.drop_index("ix_field_handoffs_miniapp_queue", table_name="field_work_handoffs")
    op.drop_table("field_installer_directions")
    op.drop_table("field_installers")
