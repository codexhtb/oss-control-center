"""Add a dedicated new-connection ticket type without converting historical rows.

Revision ID: 0045_new_connection_ticket
Revises: 0044_chat_replies
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_new_connection_ticket"
down_revision: str | None = "0044_chat_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _source_trigger(*, lock_new_connection: bool) -> None:
    locked = (
        "('ERRONEOUS_PAYMENT', 'NEW_CONNECTION')"
        if lock_new_connection else "('ERRONEOUS_PAYMENT')"
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION ticket_source_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.ticket_type IS DISTINCT FROM OLD.ticket_type
             OR NEW.subscriber_snapshot_id IS DISTINCT FROM OLD.subscriber_snapshot_id
             OR NEW.account_no IS DISTINCT FROM OLD.account_no
             OR NEW.connection_snapshot_id IS DISTINCT FROM OLD.connection_snapshot_id
             OR NEW.selected_service_id IS DISTINCT FROM OLD.selected_service_id THEN
            IF OLD.status NOT IN ('OPEN', 'IN_PROGRESS')
               OR OLD.ticket_type IN {locked}
               OR NEW.ticket_type IN {locked}
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


def upgrade() -> None:
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_description", "tickets",
        "ticket_type IN ('MANUAL', 'NEW_CONNECTION', 'ERRONEOUS_PAYMENT') "
        "OR length(description) > 0",
    )
    op.drop_constraint("ck_tickets_type_snapshot", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_type_snapshot", "tickets",
        "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
        "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
        "(ticket_type IN ('MANUAL', 'NEW_CONNECTION', 'INTERNAL', 'ERRONEOUS_PAYMENT') "
        "AND subscriber_snapshot_id IS NULL AND account_no IS NULL "
        "AND connection_snapshot_id IS NULL AND selected_service_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_tickets_new_connection", "tickets",
        "ticket_type <> 'NEW_CONNECTION' OR (caller_full_name IS NOT NULL "
        "AND caller_phone IS NOT NULL AND city IS NOT NULL "
        "AND address_override IS NOT NULL AND category_id IS NOT NULL "
        "AND category_name = 'Новое подключение' AND subject = 'Новое подключение')",
    )
    _source_trigger(lock_new_connection=True)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM tickets WHERE ticket_type = 'NEW_CONNECTION') THEN
            RAISE EXCEPTION 'cannot remove new-connection tickets';
          END IF;
        END $$
    """)
    _source_trigger(lock_new_connection=False)
    op.drop_constraint("ck_tickets_new_connection", "tickets", type_="check")
    op.drop_constraint("ck_tickets_type_snapshot", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_type_snapshot", "tickets",
        "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
        "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
        "(ticket_type IN ('MANUAL', 'INTERNAL', 'ERRONEOUS_PAYMENT') "
        "AND subscriber_snapshot_id IS NULL AND account_no IS NULL "
        "AND connection_snapshot_id IS NULL AND selected_service_id IS NULL)",
    )
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_description", "tickets",
        "ticket_type IN ('MANUAL', 'ERRONEOUS_PAYMENT') OR length(description) > 0",
    )
