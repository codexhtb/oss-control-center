"""Store an OSS ticket address correction without changing NetUP snapshots.

Revision ID: 0038_ticket_address_override
Revises: 0037_dynamic_decision_reasons
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_ticket_address_override"
down_revision: str | None = "0037_dynamic_decision_reasons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("address_override", sa.String(300), nullable=True))
    op.create_check_constraint(
        "ck_tickets_address_override",
        "tickets",
        "address_override IS NULL OR (length(btrim(address_override)) > 0 "
        "AND address_override = btrim(address_override) "
        "AND address_override !~ '[[:cntrl:]]')",
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.id IS DISTINCT FROM NEW.id
             OR OLD.number IS DISTINCT FROM NEW.number
             OR OLD.created_by IS DISTINCT FROM NEW.created_by
             OR OLD.created_at IS DISTINCT FROM NEW.created_at
             OR OLD.client_request_id IS DISTINCT FROM NEW.client_request_id THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket identity fields are immutable',
                                  ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_source_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.ticket_type IS DISTINCT FROM OLD.ticket_type
             OR NEW.subscriber_snapshot_id IS DISTINCT FROM OLD.subscriber_snapshot_id
             OR NEW.account_no IS DISTINCT FROM OLD.account_no
             OR NEW.connection_snapshot_id IS DISTINCT FROM OLD.connection_snapshot_id
             OR NEW.selected_service_id IS DISTINCT FROM OLD.selected_service_id THEN
            IF OLD.status NOT IN ('OPEN', 'IN_PROGRESS')
               OR OLD.ticket_type = 'ERRONEOUS_PAYMENT'
               OR NEW.ticket_type = 'ERRONEOUS_PAYMENT'
               OR (NEW.ticket_type IS DISTINCT FROM OLD.ticket_type
                   AND (OLD.status <> 'OPEN' OR OLD.assigned_engineer_id IS NOT NULL))
               OR EXISTS (SELECT 1 FROM ticket_decisions WHERE ticket_id = OLD.id)
               OR EXISTS (SELECT 1 FROM ticket_callbacks WHERE ticket_id = OLD.id)
               OR EXISTS (SELECT 1 FROM field_work_handoffs WHERE ticket_id = OLD.id)
               OR EXISTS (SELECT 1 FROM diagnostic_snapshots WHERE ticket_id = OLD.id)
               OR EXISTS (SELECT 1 FROM diagnostic_checks WHERE ticket_id = OLD.id) THEN
              RAISE EXCEPTION USING MESSAGE = 'ticket source has related work', ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM tickets WHERE address_override IS NOT NULL) THEN
            RAISE EXCEPTION 'ticket addresses must be reviewed before downgrade';
          END IF;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone
             OR OLD.city IS DISTINCT FROM NEW.city
             OR OLD.caller_full_name IS DISTINCT FROM NEW.caller_full_name
             OR OLD.correct_account_no IS DISTINCT FROM NEW.correct_account_no
             OR OLD.erroneous_account_no IS DISTINCT FROM NEW.erroneous_account_no
             OR OLD.payment_occurred_at IS DISTINCT FROM NEW.payment_occurred_at
             OR OLD.payment_amount IS DISTINCT FROM NEW.payment_amount THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_source_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.ticket_type IS DISTINCT FROM OLD.ticket_type
             OR NEW.subscriber_snapshot_id IS DISTINCT FROM OLD.subscriber_snapshot_id
             OR NEW.account_no IS DISTINCT FROM OLD.account_no
             OR NEW.connection_snapshot_id IS DISTINCT FROM OLD.connection_snapshot_id
             OR NEW.selected_service_id IS DISTINCT FROM OLD.selected_service_id THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket source is immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.drop_constraint("ck_tickets_address_override", "tickets", type_="check")
    op.drop_column("tickets", "address_override")
