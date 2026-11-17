"""Distinguish Mini App publication from confirmed Telegram delivery.

Revision ID: 0048_field_work_publication
Revises: 0047_field_work_miniapp_reports
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_field_work_publication"
down_revision: str | None = "0047_field_work_miniapp_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "field_work_handoffs", sa.Column("published_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "field_work_handoffs", sa.Column("published_by", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_field_handoffs_published_by", "field_work_handoffs", "app_users",
        ["published_by"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("ck_field_handoffs_delivery", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_delivery", "field_work_handoffs",
        "delivery_status IN ('DRAFT','SENDING','SENT','FAILED','UNCERTAIN','PUBLISHED')",
    )
    op.create_check_constraint(
        "ck_field_handoffs_publication", "field_work_handoffs",
        "(delivery_status = 'PUBLISHED' AND direction IS NOT NULL "
        "AND published_at IS NOT NULL AND published_by IS NOT NULL) OR "
        "(delivery_status <> 'PUBLISHED' AND published_at IS NULL AND published_by IS NULL)",
    )
    op.drop_index("ix_field_handoffs_miniapp_queue", table_name="field_work_handoffs")
    op.create_index(
        "ix_field_handoffs_miniapp_queue", "field_work_handoffs",
        ["direction", "work_status", "id"],
        postgresql_where=sa.text("delivery_status IN ('SENT','PUBLISHED')"),
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM field_work_handoffs WHERE delivery_status = 'PUBLISHED')
          THEN RAISE EXCEPTION 'cannot remove published Mini App handoffs';
          END IF;
        END $$
    """)
    op.drop_index("ix_field_handoffs_miniapp_queue", table_name="field_work_handoffs")
    op.create_index(
        "ix_field_handoffs_miniapp_queue", "field_work_handoffs",
        ["direction", "work_status", "sent_at", "id"],
        postgresql_where=sa.text("delivery_status = 'SENT'"),
    )
    op.drop_constraint("ck_field_handoffs_publication", "field_work_handoffs", type_="check")
    op.drop_constraint("ck_field_handoffs_delivery", "field_work_handoffs", type_="check")
    op.create_check_constraint(
        "ck_field_handoffs_delivery", "field_work_handoffs",
        "delivery_status IN ('DRAFT','SENDING','SENT','FAILED','UNCERTAIN')",
    )
    op.drop_constraint(
        "fk_field_handoffs_published_by", "field_work_handoffs", type_="foreignkey"
    )
    op.drop_column("field_work_handoffs", "published_by")
    op.drop_column("field_work_handoffs", "published_at")
