"""Immutable monitoring checks and engineer-confirmed automatic diagnostics.

Revision ID: 0012_oss_diagnostics
Revises: 0011_subscriber_history_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012_oss_diagnostics"
down_revision: str | None = "0011_subscriber_history_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subscriber_snapshot_id", UUID(as_uuid=True), nullable=False),
        sa.Column("connection_snapshot_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=False),
        sa.Column("report", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["ticket_id", "subscriber_snapshot_id"],
            ["tickets.id", "tickets.subscriber_snapshot_id"],
            name="fk_diagnostic_checks_ticket_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            name="fk_diagnostic_checks_connection_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("expires_at > created_at", name="ck_diagnostic_checks_expiry"),
    )
    op.create_index(
        "ix_diagnostic_checks_ticket_created",
        "diagnostic_checks",
        ["ticket_id", "created_at", "id"],
    )
    op.execute(
        """CREATE FUNCTION diagnostic_check_ticket_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE selected_connection uuid;
        BEGIN
            SELECT connection_snapshot_id INTO selected_connection
              FROM tickets WHERE id = NEW.ticket_id FOR KEY SHARE;
            IF NOT FOUND OR NEW.connection_snapshot_id IS DISTINCT FROM selected_connection THEN
                RAISE EXCEPTION USING MESSAGE = 'diagnostic check connection does not match ticket',
                    ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER diagnostic_check_ticket_before_insert
        BEFORE INSERT ON diagnostic_checks FOR EACH ROW
        EXECUTE FUNCTION diagnostic_check_ticket_guard()"""
    )
    op.execute(
        """CREATE TRIGGER diagnostic_checks_immutable_before_change
        BEFORE UPDATE OR DELETE ON diagnostic_checks FOR EACH ROW
        EXECUTE FUNCTION ticket_records_immutable()"""
    )
    op.add_column("diagnostic_snapshots", sa.Column("check_id", UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_diagnostics_check",
        "diagnostic_snapshots",
        "diagnostic_checks",
        ["check_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_diagnostics_check", "diagnostic_snapshots", ["check_id"])
    op.drop_constraint("ck_diagnostics_phase1_source", "diagnostic_snapshots", type_="check")
    op.create_check_constraint(
        "ck_diagnostics_source",
        "diagnostic_snapshots",
        "source_kind IN ('MANUAL', 'AUTOMATED')",
    )
    op.create_check_constraint(
        "ck_diagnostics_check_source",
        "diagnostic_snapshots",
        "(source_kind = 'MANUAL' AND check_id IS NULL) OR "
        "(source_kind = 'AUTOMATED' AND check_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM diagnostic_checks)
           OR EXISTS (SELECT 1 FROM diagnostic_snapshots WHERE source_kind <> 'MANUAL') THEN
            RAISE EXCEPTION 'cannot downgrade diagnostics while monitoring data exists';
        END IF;
        END $$"""
    )
    op.drop_constraint("ck_diagnostics_check_source", "diagnostic_snapshots", type_="check")
    op.drop_constraint("ck_diagnostics_source", "diagnostic_snapshots", type_="check")
    op.create_check_constraint(
        "ck_diagnostics_phase1_source", "diagnostic_snapshots", "source_kind = 'MANUAL'"
    )
    op.drop_constraint("uq_diagnostics_check", "diagnostic_snapshots", type_="unique")
    op.drop_constraint("fk_diagnostics_check", "diagnostic_snapshots", type_="foreignkey")
    op.drop_column("diagnostic_snapshots", "check_id")
    op.execute("DROP TRIGGER diagnostic_checks_immutable_before_change ON diagnostic_checks")
    op.execute("DROP TRIGGER diagnostic_check_ticket_before_insert ON diagnostic_checks")
    op.execute("DROP FUNCTION diagnostic_check_ticket_guard()")
    op.drop_index("ix_diagnostic_checks_ticket_created", table_name="diagnostic_checks")
    op.drop_table("diagnostic_checks")
