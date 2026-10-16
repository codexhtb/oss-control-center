"""Separate engineer resolution from closure without rewriting historical tickets.

Revision ID: 0016_ticket_resolved
Revises: 0015_ticket_categories
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0016_ticket_resolved"
down_revision: str | None = "0015_ticket_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("resolved_by", UUID(as_uuid=True)))
    op.add_column("tickets", sa.Column("resolved_decision_id", UUID(as_uuid=True)))
    op.add_column("tickets", sa.Column("resolved_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_tickets_resolved_by",
        "tickets",
        "app_users",
        ["resolved_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tickets_resolved_decision",
        "tickets",
        "ticket_decisions",
        ["resolved_decision_id", "id"],
        ["id", "ticket_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_tickets_status", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_status",
        "tickets",
        "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
    )
    op.create_check_constraint(
        "ck_tickets_resolution_fields",
        "tickets",
        "(status IN ('OPEN', 'IN_PROGRESS') AND resolved_by IS NULL "
        "AND resolved_at IS NULL AND resolved_decision_id IS NULL) OR "
        "(status = 'RESOLVED' AND resolved_by IS NOT NULL "
        "AND resolved_at IS NOT NULL AND resolved_decision_id IS NOT NULL) OR "
        "(status = 'CLOSED' AND ((resolved_by IS NULL AND resolved_at IS NULL "
        "AND resolved_decision_id IS NULL) OR (resolved_by IS NOT NULL "
        "AND resolved_at IS NOT NULL AND resolved_decision_id IS NOT NULL)))",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM tickets WHERE status = 'RESOLVED')")):
        raise RuntimeError("Resolve or archive RESOLVED tickets before downgrade")
    op.drop_constraint("ck_tickets_resolution_fields", "tickets", type_="check")
    op.drop_constraint("ck_tickets_status", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_status", "tickets", "status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')"
    )
    op.drop_constraint("fk_tickets_resolved_decision", "tickets", type_="foreignkey")
    op.drop_constraint("fk_tickets_resolved_by", "tickets", type_="foreignkey")
    op.drop_column("tickets", "resolved_at")
    op.drop_column("tickets", "resolved_decision_id")
    op.drop_column("tickets", "resolved_by")
