"""Prepare one Ticket Engine for linked, manual and internal tickets.

Revision ID: 0008_ticket_types
Revises: 0007_contacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_ticket_types"
down_revision: str | None = "0007_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fail without emitting subscriber data if an old writer bypassed service invariants.
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM tickets t JOIN subscriber_snapshots s
                  ON s.id = t.subscriber_snapshot_id
                WHERE t.account_no IS DISTINCT FROM s.account_no
            ) THEN
                RAISE EXCEPTION 'existing ticket/account snapshot mismatch';
            END IF;
            IF EXISTS (
                SELECT 1 FROM diagnostic_snapshots d JOIN tickets t ON t.id = d.ticket_id
                WHERE d.connection_snapshot_id IS NOT NULL
                  AND d.connection_snapshot_id IS DISTINCT FROM t.connection_snapshot_id
            ) THEN
                RAISE EXCEPTION 'existing diagnostic/ticket connection mismatch';
            END IF;
        END $$"""
    )
    op.add_column("tickets", sa.Column("ticket_type", sa.String(20), nullable=True))
    op.execute("UPDATE tickets SET ticket_type = 'SUBSCRIBER_LINKED'")
    op.alter_column(
        "tickets",
        "ticket_type",
        existing_type=sa.String(20),
        nullable=False,
        server_default=sa.text("'SUBSCRIBER_LINKED'"),
    )
    op.alter_column("tickets", "subscriber_snapshot_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column("tickets", "account_no", existing_type=sa.String(100), nullable=True)
    op.create_check_constraint(
        "ck_tickets_type_snapshot",
        "tickets",
        "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
        "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
        "(ticket_type IN ('MANUAL', 'INTERNAL') AND subscriber_snapshot_id IS NULL "
        "AND account_no IS NULL AND connection_snapshot_id IS NULL "
        "AND selected_service_id IS NULL)",
    )
    op.create_unique_constraint(
        "uq_subscriber_snapshots_id_account", "subscriber_snapshots", ["id", "account_no"]
    )
    op.create_foreign_key(
        "fk_tickets_confirmed_account",
        "tickets",
        "subscriber_snapshots",
        ["subscriber_snapshot_id", "account_no"],
        ["id", "account_no"],
        ondelete="RESTRICT",
    )
    op.execute(
        """CREATE FUNCTION ticket_source_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.ticket_type IS DISTINCT FROM OLD.ticket_type
               OR NEW.subscriber_snapshot_id IS DISTINCT FROM OLD.subscriber_snapshot_id
               OR NEW.account_no IS DISTINCT FROM OLD.account_no
               OR NEW.connection_snapshot_id IS DISTINCT FROM OLD.connection_snapshot_id
               OR NEW.selected_service_id IS DISTINCT FROM OLD.selected_service_id THEN
                RAISE EXCEPTION USING MESSAGE = 'ticket source is immutable', ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER immutable_ticket_source BEFORE UPDATE ON tickets
        FOR EACH ROW EXECUTE FUNCTION ticket_source_immutable()"""
    )

    # The old composite FK is bypassed when subscriber_snapshot_id is NULL.
    # A direct FK preserves ticket existence; the trigger also checks NULL equality.
    op.create_foreign_key(
        "fk_diagnostics_ticket",
        "diagnostic_snapshots",
        "tickets",
        ["ticket_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "diagnostic_snapshots", "subscriber_snapshot_id", existing_type=sa.UUID(), nullable=True
    )
    op.create_check_constraint(
        "ck_diagnostics_connection_parent_required",
        "diagnostic_snapshots",
        "connection_snapshot_id IS NULL OR subscriber_snapshot_id IS NOT NULL",
    )
    op.execute(
        """CREATE FUNCTION diagnostic_ticket_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_snapshot uuid; parent_connection uuid;
        BEGIN
            SELECT t.subscriber_snapshot_id, t.connection_snapshot_id
              INTO parent_snapshot, parent_connection
              FROM tickets t WHERE t.id = NEW.ticket_id FOR KEY SHARE;
            IF FOUND AND (NEW.subscriber_snapshot_id IS DISTINCT FROM parent_snapshot
               OR (NEW.connection_snapshot_id IS NOT NULL
                   AND NEW.connection_snapshot_id IS DISTINCT FROM parent_connection)) THEN
                RAISE EXCEPTION USING MESSAGE = 'diagnostic source does not match ticket',
                    ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER diagnostic_ticket_source_before_insert
        BEFORE INSERT ON diagnostic_snapshots FOR EACH ROW
        EXECUTE FUNCTION diagnostic_ticket_source_guard()"""
    )


def downgrade() -> None:
    # Never coerce manual/internal rows into linked records or discard diagnostics.
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM tickets WHERE ticket_type <> 'SUBSCRIBER_LINKED')
               OR EXISTS (SELECT 1 FROM diagnostic_snapshots
                           WHERE subscriber_snapshot_id IS NULL) THEN
                RAISE EXCEPTION 'cannot downgrade while unlinked ticket data exists';
            END IF;
        END $$"""
    )
    op.execute("DROP TRIGGER diagnostic_ticket_source_before_insert ON diagnostic_snapshots")
    op.execute("DROP FUNCTION diagnostic_ticket_source_guard()")
    op.drop_constraint(
        "ck_diagnostics_connection_parent_required", "diagnostic_snapshots", type_="check"
    )
    op.alter_column(
        "diagnostic_snapshots", "subscriber_snapshot_id", existing_type=sa.UUID(), nullable=False
    )
    op.drop_constraint("fk_diagnostics_ticket", "diagnostic_snapshots", type_="foreignkey")
    op.execute("DROP TRIGGER immutable_ticket_source ON tickets")
    op.execute("DROP FUNCTION ticket_source_immutable()")
    op.drop_constraint("fk_tickets_confirmed_account", "tickets", type_="foreignkey")
    op.drop_constraint("uq_subscriber_snapshots_id_account", "subscriber_snapshots", type_="unique")
    op.drop_constraint("ck_tickets_type_snapshot", "tickets", type_="check")
    op.alter_column("tickets", "account_no", existing_type=sa.String(100), nullable=False)
    op.alter_column("tickets", "subscriber_snapshot_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("tickets", "ticket_type")
