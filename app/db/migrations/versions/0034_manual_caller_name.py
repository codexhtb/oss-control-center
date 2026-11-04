"""Store the manual caller's name and allow an omitted manual description.

Revision ID: 0034_manual_caller_name
Revises: 0033_ticket_city
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_manual_caller_name"
down_revision: str | None = "0033_ticket_city"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _immutable(*, include_name: bool) -> None:
    name_clause = (
        "OR OLD.caller_full_name IS DISTINCT FROM NEW.caller_full_name" if include_name else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone
             OR OLD.city IS DISTINCT FROM NEW.city
             {name_clause} THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)


def upgrade() -> None:
    op.add_column("tickets", sa.Column("caller_full_name", sa.String(length=160), nullable=True))
    op.create_check_constraint(
        "ck_tickets_caller_full_name",
        "tickets",
        "caller_full_name IS NULL OR (length(btrim(caller_full_name)) > 0 "
        "AND caller_full_name = btrim(caller_full_name) "
        "AND caller_full_name !~ '[[:cntrl:]]')",
    )
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_description", "tickets", "ticket_type = 'MANUAL' OR length(description) > 0"
    )
    _immutable(include_name=True)


def downgrade() -> None:
    op.execute(
        "UPDATE tickets SET description = COALESCE(subject, 'Ручное обращение') "
        "WHERE ticket_type = 'MANUAL' AND description = ''"
    )
    op.drop_constraint("ck_tickets_description", "tickets", type_="check")
    op.create_check_constraint("ck_tickets_description", "tickets", "length(description) > 0")
    _immutable(include_name=False)
    op.drop_constraint("ck_tickets_caller_full_name", "tickets", type_="check")
    op.drop_column("tickets", "caller_full_name")
