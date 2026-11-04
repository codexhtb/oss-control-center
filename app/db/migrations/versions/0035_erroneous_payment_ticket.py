"""Add structured erroneous-payment tickets without changing existing tickets.

Revision ID: 0035_erroneous_payment_ticket
Revises: 0034_manual_caller_name
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_erroneous_payment_ticket"
down_revision: str | None = "0034_manual_caller_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _origin_trigger(*, payment: bool) -> None:
    payment_fields = (
        "OR OLD.correct_account_no IS DISTINCT FROM NEW.correct_account_no "
        "OR OLD.erroneous_account_no IS DISTINCT FROM NEW.erroneous_account_no "
        "OR OLD.payment_occurred_at IS DISTINCT FROM NEW.payment_occurred_at "
        "OR OLD.payment_amount IS DISTINCT FROM NEW.payment_amount"
        if payment
        else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone
             OR OLD.city IS DISTINCT FROM NEW.city
             OR OLD.caller_full_name IS DISTINCT FROM NEW.caller_full_name
             {payment_fields} THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)


def upgrade() -> None:
    op.add_column("tickets", sa.Column("correct_account_no", sa.String(100)))
    op.add_column("tickets", sa.Column("erroneous_account_no", sa.String(100)))
    op.add_column("tickets", sa.Column("payment_occurred_at", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("payment_amount", sa.Numeric(14, 2)))
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_description",
        "tickets",
        "ticket_type IN ('MANUAL', 'ERRONEOUS_PAYMENT') OR length(description) > 0",
    )
    op.drop_constraint("ck_tickets_type_snapshot", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_type_snapshot",
        "tickets",
        "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
        "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
        "(ticket_type IN ('MANUAL', 'INTERNAL', 'ERRONEOUS_PAYMENT') "
        "AND subscriber_snapshot_id IS NULL AND account_no IS NULL "
        "AND connection_snapshot_id IS NULL AND selected_service_id IS NULL)",
    )
    for column in ("correct_account_no", "erroneous_account_no"):
        op.create_check_constraint(
            f"ck_tickets_{column}",
            "tickets",
            f"{column} IS NULL OR (length(btrim({column})) > 0 "
            f"AND {column} = btrim({column}) AND {column} !~ '[[:cntrl:]]')",
        )
    op.create_check_constraint(
        "ck_tickets_payment_fields",
        "tickets",
        "(ticket_type = 'ERRONEOUS_PAYMENT' AND caller_full_name IS NOT NULL "
        "AND correct_account_no IS NOT NULL AND erroneous_account_no IS NOT NULL "
        "AND correct_account_no <> erroneous_account_no "
        "AND payment_occurred_at IS NOT NULL AND payment_amount IS NOT NULL "
        "AND payment_amount > 0 AND subject IS NOT NULL "
        "AND subject = 'Ошибочный платёж' AND category_id IS NULL) OR "
        "(ticket_type <> 'ERRONEOUS_PAYMENT' AND correct_account_no IS NULL "
        "AND erroneous_account_no IS NULL AND payment_occurred_at IS NULL "
        "AND payment_amount IS NULL)",
    )
    _origin_trigger(payment=True)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM tickets WHERE ticket_type = 'ERRONEOUS_PAYMENT') THEN
            RAISE EXCEPTION 'cannot remove erroneous-payment tickets';
          END IF;
        END $$
    """)
    _origin_trigger(payment=False)
    op.drop_constraint("ck_tickets_payment_fields", "tickets", type_="check")
    for column in ("correct_account_no", "erroneous_account_no"):
        op.drop_constraint(f"ck_tickets_{column}", "tickets", type_="check")
    op.drop_constraint("ck_tickets_type_snapshot", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_type_snapshot",
        "tickets",
        "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
        "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
        "(ticket_type IN ('MANUAL', 'INTERNAL') AND subscriber_snapshot_id IS NULL "
        "AND account_no IS NULL AND connection_snapshot_id IS NULL "
        "AND selected_service_id IS NULL)",
    )
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_description",
        "tickets",
        "ticket_type = 'MANUAL' OR length(description) > 0",
    )
    for column in (
        "payment_amount",
        "payment_occurred_at",
        "erroneous_account_no",
        "correct_account_no",
    ):
        op.drop_column("tickets", column)
