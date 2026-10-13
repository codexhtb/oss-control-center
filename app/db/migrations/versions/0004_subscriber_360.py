"""Extend immutable billing snapshots and ticket service selection.

Revision ID: 0004_subscriber_360
Revises: 0003_ticket_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_subscriber_360"
down_revision: str | None = "0003_ticket_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subscriber_snapshots", sa.Column("subscriber_id", sa.String(100)))
    op.add_column("subscriber_snapshots", sa.Column("organization_name", sa.String(200)))
    op.add_column("subscriber_snapshots", sa.Column("email", sa.String(200)))
    op.add_column(
        "subscriber_snapshots",
        sa.Column(
            "field_meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("connection_snapshots", sa.Column("address", sa.String(300)))
    op.add_column("connection_snapshots", sa.Column("ip_group", sa.String(100)))
    op.add_column(
        "connection_snapshots",
        sa.Column(
            "field_meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("tickets", sa.Column("selected_service_id", sa.String(100)))
    op.create_check_constraint(
        "ck_tickets_selected_service_id",
        "tickets",
        "selected_service_id IS NULL OR length(selected_service_id) > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tickets_selected_service_id", "tickets", type_="check")
    op.drop_column("tickets", "selected_service_id")
    op.drop_column("connection_snapshots", "field_meta")
    op.drop_column("connection_snapshots", "ip_group")
    op.drop_column("connection_snapshots", "address")
    op.drop_column("subscriber_snapshots", "field_meta")
    op.drop_column("subscriber_snapshots", "email")
    op.drop_column("subscriber_snapshots", "organization_name")
    op.drop_column("subscriber_snapshots", "subscriber_id")
